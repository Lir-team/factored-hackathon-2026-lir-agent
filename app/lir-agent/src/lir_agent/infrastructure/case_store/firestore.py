"""CaseStore on Firestore: case state that survives restarts and is shared by instances.

One collection per record kind, every name starting with a configurable prefix (`lir_`):

- `<prefix>receipts/<idempotency key>`: the customer and the 202 answer to replay.
- `<prefix>start_tokens/<sha256 of the token>`: the `CaseStart` and `expires_at`. Only the
  hash is stored, so the database never holds a usable start link.
- `<prefix>chats/<chat id>`: the chat's current `ChatLink`.
- `<prefix>case_chats/<case id>`: the chat last linked to the case.
- `<prefix>conversations/<case id>`: the case's `CaseConversation` (created once).
- `<prefix>replies/<case id>`: `texts`, the agent replies waiting for a chat, oldest
  first. A list field rewritten in a transaction keeps order and repeats (`ArrayUnion`
  would drop repeated texts).

The client honours `FIRESTORE_EMULATOR_HOST`, so local runs use the emulator unchanged.
Calls are short and blocking; async handlers call them directly.
"""

import hashlib
from datetime import datetime
from typing import cast

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore

from lir_agent.application.ports import StoredReceipt
from lir_agent.domain.case_intake import CaseConversation, CaseReceipt, CaseStart
from lir_agent.domain.language import Language
from lir_agent.domain.telegram import ChatLink

DEFAULT_PREFIX = "lir_"


def _token_id(token: str) -> str:
    """Document id of a start token: its SHA-256, never the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


@firestore.transactional
def _consume(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    now: datetime,
) -> CaseStart | None:
    snapshot = ref.get(transaction=transaction)
    if not snapshot.exists:
        return None
    transaction.delete(ref)
    data = snapshot.to_dict() or {}
    if now >= data["expires_at"]:
        return None
    return CaseStart(data["case_id"], data["folio"], data["language"])


@firestore.transactional
def _append(
    transaction: firestore.Transaction, ref: firestore.DocumentReference, text: str
) -> None:
    snapshot = ref.get(transaction=transaction)
    texts = (snapshot.to_dict() or {}).get("texts", []) if snapshot.exists else []
    transaction.set(ref, {"texts": [*texts, text]})


@firestore.transactional
def _pop(
    transaction: firestore.Transaction, ref: firestore.DocumentReference
) -> list[str]:
    snapshot = ref.get(transaction=transaction)
    if not snapshot.exists:
        return []
    transaction.delete(ref)
    return list((snapshot.to_dict() or {}).get("texts", []))


class FirestoreCaseStore:
    """CaseStore kept in Firestore (see the module docstring for the layout)."""

    def __init__(self, client: firestore.Client, prefix: str = DEFAULT_PREFIX) -> None:
        """Bind the client; `prefix` keeps environments or test runs apart."""
        self._client = client
        self._receipts = client.collection(f"{prefix}receipts")
        self._tokens = client.collection(f"{prefix}start_tokens")
        self._chats = client.collection(f"{prefix}chats")
        self._case_chats = client.collection(f"{prefix}case_chats")
        self._conversations = client.collection(f"{prefix}conversations")
        self._replies = client.collection(f"{prefix}replies")

    def get_receipt(self, idempotency_key: str) -> StoredReceipt | None:
        """Return the answer stored for this key, or None."""
        data = self._receipts.document(idempotency_key).get().to_dict()
        if data is None:
            return None
        return StoredReceipt(
            data["customer_id"],
            CaseReceipt(
                data["case_id"],
                data["folio"],
                data["telegram_start_url"],
                data["status"],
            ),
        )

    def save_receipt(self, idempotency_key: str, stored: StoredReceipt) -> None:
        """Keep a successful answer for replay."""
        receipt = stored.receipt
        self._receipts.document(idempotency_key).set(
            {
                "customer_id": stored.customer_id,
                "case_id": receipt.case_id,
                "folio": receipt.folio,
                "telegram_start_url": receipt.telegram_start_url,
                "status": receipt.status,
            }
        )

    def add_start_token(
        self, token: str, start: CaseStart, expires_at: datetime
    ) -> None:
        """Bind a single-use start token to a case until `expires_at`."""
        self._tokens.document(_token_id(token)).set(
            {
                "case_id": start.case_id,
                "folio": start.folio,
                "language": start.language,
                "expires_at": expires_at,
            }
        )

    def consume_start_token(self, token: str, now: datetime) -> CaseStart | None:
        """Burn the token and return its case; None when unknown, used or expired."""
        ref = self._tokens.document(_token_id(token))
        return _consume(self._client.transaction(), ref, now)

    def link_chat(self, chat_id: int, link: ChatLink) -> None:
        """Bind a chat to a case, replacing the chat's previous link."""
        batch = self._client.batch()
        batch.set(
            self._chats.document(str(chat_id)),
            {"case_id": link.case_id, "folio": link.folio, "language": link.language},
        )
        batch.set(self._case_chats.document(link.case_id), {"chat_id": chat_id})
        batch.commit()

    def get_chat_link(self, chat_id: int) -> ChatLink | None:
        """Return the chat's current link, or None."""
        data = self._chats.document(str(chat_id)).get().to_dict()
        if data is None:
            return None
        return ChatLink(
            data["case_id"], data["folio"], cast(Language, data["language"])
        )

    def get_case_chat(self, case_id: str) -> int | None:
        """Return the chat last linked to the case, or None."""
        data = self._case_chats.document(case_id).get().to_dict()
        return None if data is None else data["chat_id"]

    def add_conversation(self, conversation: CaseConversation) -> bool:
        """Keep the case's conversation unless it has one; False when it already had one."""
        try:
            self._conversations.document(conversation.case_id).create(
                {
                    "case_id": conversation.case_id,
                    "folio": conversation.folio,
                    "language": conversation.language,
                    "owner": conversation.owner,
                    "session_id": conversation.session_id,
                }
            )
        except AlreadyExists:
            return False
        return True

    def get_conversation(self, case_id: str) -> CaseConversation | None:
        """Return the case's conversation, or None while the case was not worked."""
        data = self._conversations.document(case_id).get().to_dict()
        if data is None:
            return None
        return CaseConversation(
            data["case_id"],
            data["folio"],
            data["language"],
            data["owner"],
            data["session_id"],
        )

    def queue_reply(self, case_id: str, text: str) -> None:
        """Keep an agent reply until a chat is linked to the case."""
        _append(self._client.transaction(), self._replies.document(case_id), text)

    def pop_replies(self, case_id: str) -> list[str]:
        """Remove and return the case's queued replies, oldest first."""
        return _pop(self._client.transaction(), self._replies.document(case_id))
