"""Application ports (interfaces).

Infrastructure adapters implement them; use cases depend only on these contracts.
The decision model port is `decision_layer.DecisionModel`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from lir_agent.domain.approvals import (
    ApprovalDraft,
    ApprovalRequest,
    ApprovalStatus,
    Approver,
)
from lir_agent.domain.case_intake import (
    CaseConversation,
    CaseReceipt,
    CaseReport,
    CaseStart,
)
from lir_agent.domain.language import Language
from lir_agent.domain.models import Customer, DisputeCase, HandoffPacket, Transaction
from lir_agent.domain.session import SessionState
from lir_agent.domain.telegram import ChatLink, InlineButton


class TransactionRepository(Protocol):
    """Read access to a customer's records.

    Contract for `list_transactions`: results belong to `customer_id` only and are ordered by
    transaction_date descending.
    """

    name: str

    def list_transactions(self, customer_id: str) -> list[Transaction]:
        """Return the customer's transactions, most recent first."""
        ...

    def get_customer(self, customer_id: str) -> Customer | None:
        """Return the customer, or None when the id is unknown."""
        ...


class CaseRepository(Protocol):
    """Case service (mock in this PoC). Writes are read back by use cases before reporting success."""

    def open_dispute(
        self, customer_id: str, transaction_id: str, reason: str
    ) -> DisputeCase:
        """Open a dispute case and return it as stored."""
        ...

    def get_dispute(self, case_id: str) -> DisputeCase | None:
        """Return a stored dispute, or None."""
        ...

    def submit_handoff(self, packet: HandoffPacket) -> str:
        """Store a handoff packet for human review and return its id."""
        ...

    def get_handoff(self, handoff_id: str) -> HandoffPacket | None:
        """Return a stored handoff packet, or None."""
        ...


class AuditSink(Protocol):
    """Execution record (Bases §6): one structured entry per decision, rule, tool call or reply."""

    def record(self, event: str, session_id: str | None, **fields: Any) -> dict:
        """Write one audit entry and return it."""
        ...


class ConversationNotFoundError(Exception):
    """The conversation does not exist, expired from memory, or belongs to another owner."""


class CustomerNotFoundError(Exception):
    """No customer with this id exists in the data source."""


@dataclass(frozen=True)
class StartedConversation:
    """A new conversation bound to a customer."""

    session_id: str
    expires_at: datetime
    # Session references (T1, T2, ...) of the transactions the conversation started with.
    transaction_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnTrace:
    """How one turn was decided: the typed decisions, the policy lanes and what it cost.

    Facts only, read from session state and runtime events; nothing is model-written.
    """

    decision_model: str | None
    decision_fallback: list[str] | None
    decisions: dict | None
    turn_lane: str | None
    turn_rule: str | None
    case_lane: str | None
    case_rule: str | None
    policy_version: str | None
    tools: list[str]
    handoff_id: str | None
    llm_model: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


@dataclass(frozen=True)
class Turn:
    """The agent's reply to one customer message and how it was decided."""

    reply: str
    trace: TurnTrace
    # Approval requests the agent created in this turn: the caller presents them.
    approvals: tuple[str, ...] = ()


class Conversations(Protocol):
    """Customer conversations with the agent, shared by every entry point.

    Each conversation belongs to an `owner` (the IAP operator over HTTP, a case for intake
    channels): only that owner can continue it. The customer is bound on start and never
    passes through the conversation with the model.
    """

    async def start(
        self,
        owner: str,
        customer_id: str,
        *,
        ttl: timedelta,
        auth_method: str,
        transaction_ids: Sequence[str] = (),
        case_report: CaseReport | None = None,
        max_ttl: timedelta | None = None,
    ) -> StartedConversation:
        """Start a conversation for `customer_id`, valid for `ttl`.

        `transaction_ids` (the charges a case reports) get session references, so the first
        message can name them without carrying the bank's records. `case_report` is what the
        customer reported in the form; the policy acts on it. With `max_ttl`, `ttl` is an
        idle timeout that activity extends up to `max_ttl`.

        Raises:
            CustomerNotFoundError: If the customer does not exist.
        """
        ...

    async def send(self, owner: str, session_id: str, text: str) -> str:
        """Send one customer message and return the agent's reply.

        Raises:
            ConversationNotFoundError: If `owner` has no conversation with this id.
        """
        ...

    async def converse(self, owner: str, session_id: str, text: str) -> Turn:
        """Like `send`, but also return how the turn was decided.

        Raises:
            ConversationNotFoundError: If `owner` has no conversation with this id.
        """
        ...


class CaseInbox(Protocol):
    """Archive of accepted cases (the `cases-inbox` bucket in production)."""

    def put(
        self, case_id: str, payload: dict[str, Any], attributes: dict[str, str]
    ) -> None:
        """Store the case payload unchanged, with its routing attributes as metadata."""
        ...


class CasePublishError(Exception):
    """The case could not be handed to the agent; the client may retry with the same key."""


class CaseInProgressError(Exception):
    """Another request with the same key is still being accepted; the client may retry."""


class CasePublisher(Protocol):
    """Hands accepted cases to the agent (the `lir-cases` Pub/Sub topic in production)."""

    def publish(
        self, payload: dict[str, Any], attributes: dict[str, str], ordering_key: str
    ) -> None:
        """Publish the payload unchanged and wait until it is accepted.

        Raises:
            CasePublishError: If the message was not accepted.
        """
        ...


@dataclass(frozen=True)
class StoredReceipt:
    """An accepted case's answer and the customer who filed it."""

    customer_id: str
    receipt: CaseReceipt


class CaseStore(Protocol):
    """Case state that must outlive the request.

    Answers, start tokens, chat links, each case's conversation and the agent's replies
    waiting for a chat.
    """

    def get_receipt(self, idempotency_key: str) -> StoredReceipt | None:
        """Return the answer stored for this key, or None."""
        ...

    def save_receipt(self, idempotency_key: str, stored: StoredReceipt) -> None:
        """Keep a successful answer for replay."""
        ...

    def claim_key(
        self, idempotency_key: str, now: datetime, expires_at: datetime
    ) -> bool:
        """Claim the key until `expires_at`; False while another claim is still valid.

        Must be atomic: it is what makes concurrent requests with one key accept it once.
        A claim is never cleared on success (the receipt then answers); an expired one can
        be taken over, so a request that died mid-way does not block the key forever.
        """
        ...

    def release_key(self, idempotency_key: str) -> None:
        """Drop the key's claim, so a retry can accept the case."""
        ...

    def add_start_token(
        self, token: str, start: CaseStart, expires_at: datetime
    ) -> None:
        """Bind a single-use Telegram start token to a case until `expires_at`."""
        ...

    def consume_start_token(self, token: str, now: datetime) -> CaseStart | None:
        """Burn the token and return its case; None when unknown, used or expired."""
        ...

    def link_chat(self, chat_id: int, link: ChatLink) -> None:
        """Bind a Telegram chat to a case, replacing the chat's previous link."""
        ...

    def get_chat_link(self, chat_id: int) -> ChatLink | None:
        """Return the chat's current link, or None."""
        ...

    def get_case_chat(self, case_id: str) -> int | None:
        """Return the chat last linked to the case, or None."""
        ...

    def add_conversation(self, conversation: CaseConversation) -> bool:
        """Keep the case's conversation unless it has one; False when it already had one.

        Must be atomic: it is what makes a redelivered case answered only once.
        """
        ...

    def get_conversation(self, case_id: str) -> CaseConversation | None:
        """Return the case's conversation, or None while the case was not worked."""
        ...

    def queue_reply(self, case_id: str, text: str) -> None:
        """Keep an agent reply until a chat is linked to the case (repeats allowed)."""
        ...

    def pop_replies(self, case_id: str) -> list[str]:
        """Remove and return the case's queued replies, oldest first.

        Must be atomic: two callers never get the same reply. A caller that cannot send
        them puts the unsent ones back with `requeue_replies`.
        """
        ...

    def requeue_replies(self, case_id: str, texts: list[str]) -> None:
        """Put popped replies that were not sent back, before any queued since."""
        ...


class MessageNotSentError(Exception):
    """A message did not reach the chat; the caller decides whether to retry or move on."""


class HandoffNotifier(Protocol):
    """Tells the bank team that a case was handed off (no customer data in the notice)."""

    def notify(self, packet: HandoffPacket) -> None:
        """Announce the handoff; raises on failure (the handoff itself is already stored)."""
        ...


class Messenger(Protocol):
    """Outbound messages to a customer's chat (Telegram today)."""

    async def send(self, chat_id: int, text: str) -> None:
        """Deliver `text` to the chat.

        Raises:
            MessageNotSentError: If it was not delivered (a long text split in parts may
                have been delivered in part).
        """
        ...


# ---- human in the loop -------------------------------------------------------------------
class ApprovalRepository(Protocol):
    """Approval requests and their decisions."""

    def save(self, request: ApprovalRequest) -> None:
        """Store a request (insert or replace by id)."""
        ...

    def get(self, approval_id: str) -> ApprovalRequest | None:
        """Return a request, or None."""
        ...

    def list(
        self, status: ApprovalStatus | None = None, approver: Approver | None = None
    ) -> list[ApprovalRequest]:
        """Requests, newest first, optionally filtered."""
        ...


class ApprovalSurface(Protocol):
    """Where a person sees an approval request and decides it (Telegram, web, back office).

    A surface only shows requests and reports outcomes. Its decisions enter through
    `DecideApproval`, with the actor the surface authenticated: the rules and the audit
    trail stay in one place, whatever the surface.
    """

    name: str

    async def present(self, request: ApprovalRequest, link: str | None) -> bool:
        """Show the request to its approver; False when it is not this surface's to show.

        `link` is a single-use web link to the request, when one was issued.
        """
        ...

    async def report(self, request: ApprovalRequest) -> None:
        """Tell the people involved how a request was decided (and what ran)."""
        ...


class ApprovalAction(Protocol):
    """Adapter of an important action: runs only after its last approval.

    Which tools are important actions, and who approves them, is in the policy
    (`approvals.actions`); the tool guard turns their calls into approval requests.
    """

    name: str

    def describe(
        self, session: SessionState, args: Mapping[str, Any], labels: Mapping[str, Any]
    ) -> ApprovalDraft | None:
        """What the approver reads and what will run; None when the action is not allowed."""
        ...

    def execute(self, request: ApprovalRequest) -> dict:
        """Run the action and read it back; the result is kept with the request.

        Raises:
            ApprovalError: `action_not_verified` when the result could not be read back.
        """
        ...


class ChatButtons(Protocol):
    """Messages with buttons, and answers to button presses (Telegram today)."""

    async def send_buttons(
        self, chat_id: int, text: str, rows: list[list[InlineButton]]
    ) -> None:
        """Send `text` with rows of buttons under it.

        Raises:
            MessageNotSentError: If it was not delivered.
        """
        ...

    async def answer_callback(
        self, callback_id: str, text: str, alert: bool = False
    ) -> None:
        """Acknowledge a button press (a short notice, or an alert the user must close)."""
        ...


class ChatChannel(Messenger, ChatButtons, Protocol):
    """A customer chat channel: plain messages and messages with buttons."""


# ---- voice notes ------------------------------------------------------------------------
class FileNotDownloadedError(Exception):
    """A file sent to the chat could not be fetched from the channel."""


class ChatFiles(Protocol):
    """Files customers send to the chat (Telegram voice notes today)."""

    async def download_file(self, file_id: str) -> bytes:
        """The file's bytes.

        Raises:
            FileNotDownloadedError: If the channel did not hand it over.
        """
        ...


class TranscriptionError(Exception):
    """Speech could not be turned into text (the service failed or refused the audio)."""


class SpeechToText(Protocol):
    """Transcribes a short voice note (Telegram OGG/Opus) in the customer's language."""

    async def transcribe(self, audio: bytes, language: Language) -> str:
        """The transcript, `""` when no speech was recognized.

        Raises:
            TranscriptionError: If the service failed.
        """
        ...


class SigningError(Exception):
    """A token could not be signed (the signing service failed or refused)."""


class JwtSigner(Protocol):
    """Signs JWT claims as the bank's identity provider (mocked for the demo)."""

    def sign(self, claims: Mapping[str, Any]) -> str:
        """The signed token.

        Raises:
            SigningError: If the signing service failed.
        """
        ...
