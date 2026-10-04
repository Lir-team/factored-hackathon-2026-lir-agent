"""Use case: work a case delivered to the agent, and send its replies to the customer's chat."""

from datetime import timedelta
from typing import Any

from lir_agent.application.ports import (
    AuditSink,
    CaseStore,
    Conversations,
    CustomerNotFoundError,
    MessageNotSentError,
    Messenger,
)
from lir_agent.application.use_cases.approvals import PresentApprovals
from lir_agent.domain.case_intake import (
    CaseConversation,
    CaseReport,
    case_summary,
    folio_for,
)
from lir_agent.domain.telegram import case_owner

# How conversations opened from a delivered case are authenticated (session state and audit).
AUTH_METHOD = "case_intake"


class ProcessCase:
    """Use case: start the case's conversation and run the agent's first turn, once per case."""

    def __init__(
        self,
        conversations: Conversations,
        store: CaseStore,
        messenger: Messenger | None,
        audit: AuditSink,
        *,
        session_ttl: timedelta,
        present_approvals: PresentApprovals | None = None,
    ) -> None:
        """Keep the adapters; without a messenger, replies wait in the store."""
        self._conversations = conversations
        self._store = store
        self._messenger = messenger
        self._audit = audit
        self._session_ttl = session_ttl
        self._present_approvals = present_approvals

    async def execute(self, payload: dict[str, Any]) -> None:
        """Work a schema-valid case; a case already worked only sends its waiting replies.

        Never audits message text. Agent and send failures propagate, so the delivery is
        retried: nothing is kept until the first turn succeeded, and a reply that could not
        be sent stays queued for the retry.
        """
        case_id = payload["case_id"]
        if self._store.get_conversation(case_id) is not None:  # a redelivery
            await deliver_replies(self._store, self._messenger, self._audit, case_id)
            return
        owner = case_owner(case_id)
        try:
            started = await self._conversations.start(
                owner,
                payload["customer"]["customer_id"],
                ttl=self._session_ttl,
                auth_method=AUTH_METHOD,
                transaction_ids=[t["transaction_id"] for t in payload["transactions"]],
                case_report=CaseReport.from_payload(payload),
            )
        except CustomerNotFoundError:
            self._audit.record(
                "case_rejected", None, case_id=case_id, reason="unknown_customer"
            )
            return
        turn = await self._conversations.converse(
            owner, started.session_id, case_summary(payload, started.transaction_refs)
        )
        reply = turn.reply
        conversation = CaseConversation(
            case_id=case_id,
            folio=folio_for(case_id, payload["submitted_at"]),
            language=payload["language"],
            owner=owner,
            session_id=started.session_id,
        )
        if not self._store.add_conversation(conversation):
            return  # a concurrent delivery of the same case got there first
        self._audit.record("case_processed", started.session_id, case_id=case_id)
        if reply.strip():
            self._store.queue_reply(case_id, reply)
            self._audit.record("case_reply_queued", started.session_id, case_id=case_id)
        await deliver_replies(self._store, self._messenger, self._audit, case_id)
        # Approval requests go after the reply that explains them.
        if self._present_approvals and turn.approvals:
            await self._present_approvals.execute(turn.approvals)


async def deliver_replies(
    store: CaseStore, messenger: Messenger | None, audit: AuditSink, case_id: str
) -> None:
    """Send the case's queued replies to the chat linked to it, if any.

    Both the case worker and `/start` call this after their own write (reply queued, chat
    linked); popping is atomic, so each reply is sent once whichever runs last. A reply
    that could not be sent is put back with the ones after it, then the error propagates.

    Raises:
        MessageNotSentError: If a reply was not sent (it stays queued).
    """
    chat_id = store.get_case_chat(case_id)
    if chat_id is None or messenger is None:
        return
    link = store.get_chat_link(chat_id)
    if link is None or link.case_id != case_id:  # the chat moved to another case
        return
    replies = store.pop_replies(case_id)
    sent = 0
    try:
        for reply in replies:
            await messenger.send(chat_id, reply)
            sent += 1
    except MessageNotSentError:
        store.requeue_replies(case_id, replies[sent:])
        raise
    finally:
        if sent:
            audit.record("case_reply_sent", None, case_id=case_id, replies=sent)
