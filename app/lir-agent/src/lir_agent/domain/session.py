"""Typed view over the conversation state.

ADK stores session state as a plain mapping that must stay JSON-serializable. `SessionState`
wraps that mapping so the rest of the code works with typed properties instead of string keys.

The channel adapter (Telegram, web) authenticates the customer against a trusted test identity
service and starts the session. A national ID or customer number typed in the chat never proves
identity (Bases, data boundaries).
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from lir_agent.domain.case_intake import CaseReport
from lir_agent.domain.errors import AuthError, UnauthenticatedSessionError
from lir_agent.domain.models import Evidence, Lane, Outcome

TRANSACTION_REF_PREFIX = "T"


class _Key(StrEnum):
    CUSTOMER_ID = "customer_id"
    AUTH_EXPIRES_AT = "auth_expires_at"
    AUTH_METHOD = "auth_method"
    AUTH_IDLE_SECONDS = "auth_idle_seconds"
    AUTH_MAX_EXPIRES_AT = "auth_max_expires_at"
    LAST_USER_TEXT = "last_user_text"
    DECISIONS = "decisions"
    DECISION_META = "decision_meta"
    TURN_OUTCOME = "turn_outcome"
    CASE_OUTCOME = "case_outcome"
    TRANSACTION_REFS = "transaction_refs"
    PSEUDONYMS = "pseudonyms"
    CASE_REPORT = "case_report"
    EXPLAINED_TRANSACTION = "explained_transaction"
    REVIEW_TRANSACTION = "review_transaction"
    EVIDENCE = "evidence"
    APPROVAL_IDS = "approval_ids"
    ACTIONS = "actions"
    HANDOFF_ID = "handoff_id"


class StateStore(Protocol):
    """Read/write access shared by ADK's `State` and a plain dict."""

    def get(self, key: str, default: Any = None, /) -> Any:
        """Return the value for `key`, or `default`."""
        ...

    def __setitem__(self, key: str, value: Any, /) -> None:
        """Store `value` under `key`."""
        ...


def utc_now() -> datetime:
    """Current time in UTC."""
    return datetime.now(UTC)


class SessionState:
    """Typed read/write access to the ADK session state."""

    def __init__(self, raw: StateStore) -> None:
        """Wrap the session's raw state mapping."""
        self._raw = raw

    # ---- authentication ------------------------------------------------------------------
    def start(
        self,
        customer_id: str,
        ttl: timedelta,
        method: str,
        *,
        max_ttl: timedelta | None = None,
    ) -> datetime:
        """Bind the authenticated customer to the session; return when it expires.

        With `max_ttl`, `ttl` is an idle timeout: every message extends the session by `ttl`
        (`touch`), never past `max_ttl` from now. Without it, `ttl` is fixed.
        """
        now = utc_now()
        expires_at = now + ttl
        if max_ttl is not None:
            ceiling = now + max(max_ttl, ttl)
            self._raw[_Key.AUTH_IDLE_SECONDS] = ttl.total_seconds()
            self._raw[_Key.AUTH_MAX_EXPIRES_AT] = ceiling.isoformat()
            expires_at = min(expires_at, ceiling)
        self._raw[_Key.CUSTOMER_ID] = customer_id
        self._raw[_Key.AUTH_EXPIRES_AT] = expires_at.isoformat()
        self._raw[_Key.AUTH_METHOD] = method
        return expires_at

    def touch(self, at: datetime | None = None) -> None:
        """Extend an idle-timeout session after activity, up to its ceiling.

        A session that already expired stays expired: the customer must sign in again.
        """
        idle = self._raw.get(_Key.AUTH_IDLE_SECONDS)
        ceiling = self._raw.get(_Key.AUTH_MAX_EXPIRES_AT)
        if not idle or not ceiling or self.auth_error(at) is not None:
            return
        extended = min(
            (at or utc_now()) + timedelta(seconds=idle), datetime.fromisoformat(ceiling)
        )
        self._raw[_Key.AUTH_EXPIRES_AT] = extended.isoformat()

    def auth_error(self, at: datetime | None = None) -> AuthError | None:
        """Why the session cannot be used now, or None when it is valid."""
        expires_at = self._raw.get(_Key.AUTH_EXPIRES_AT)
        if not self.customer_id or not expires_at:
            return AuthError.MISSING
        if (at or utc_now()) >= datetime.fromisoformat(expires_at):
            return AuthError.EXPIRED
        return None

    @property
    def customer_id(self) -> str | None:
        """The authenticated customer's id."""
        return self._raw.get(_Key.CUSTOMER_ID)

    def require_customer_id(self) -> str:
        """The authenticated customer's id; raises when the session has none.

        Raises:
            UnauthenticatedSessionError: If no customer is bound to the session.
        """
        customer_id = self.customer_id
        if not isinstance(customer_id, str) or not customer_id.strip():
            raise UnauthenticatedSessionError(
                "No authenticated customer in this session."
            )
        return customer_id

    # ---- conversation --------------------------------------------------------------------
    @property
    def last_user_text(self) -> str | None:
        """The newest customer message."""
        return self._raw.get(_Key.LAST_USER_TEXT)

    @last_user_text.setter
    def last_user_text(self, text: str) -> None:
        self._raw[_Key.LAST_USER_TEXT] = text

    @property
    def decisions(self) -> dict | None:
        """Serialized typed decisions of the current turn."""
        return self._raw.get(_Key.DECISIONS)

    @decisions.setter
    def decisions(self, decisions: dict | None) -> None:
        self._raw[_Key.DECISIONS] = decisions

    @property
    def decision_meta(self) -> dict | None:
        """Which decision model answered the current turn, its latency and any fallback."""
        return self._raw.get(_Key.DECISION_META)

    @decision_meta.setter
    def decision_meta(self, meta: dict | None) -> None:
        self._raw[_Key.DECISION_META] = meta

    @property
    def turn_outcome(self) -> Outcome | None:
        """Policy outcome of the current turn."""
        return self._load_outcome(_Key.TURN_OUTCOME)

    @turn_outcome.setter
    def turn_outcome(self, outcome: Outcome) -> None:
        self._raw[_Key.TURN_OUTCOME] = outcome.model_dump(mode="json")

    @property
    def turn_lane(self) -> Lane:
        """Current turn lane; without one, only the safest lane applies."""
        outcome = self.turn_outcome
        return outcome.lane if outcome else Lane.ESCALATE

    @property
    def case_outcome(self) -> Outcome | None:
        """Policy outcome of the last transaction investigated."""
        return self._load_outcome(_Key.CASE_OUTCOME)

    @case_outcome.setter
    def case_outcome(self, outcome: Outcome) -> None:
        self._raw[_Key.CASE_OUTCOME] = outcome.model_dump(mode="json")

    # ---- opaque transaction references ---------------------------------------------------
    def ref_for(self, transaction_id: str) -> str:
        """Session-scoped reference shown to the LLM instead of the internal id."""
        refs: dict[str, str] = dict(self._raw.get(_Key.TRANSACTION_REFS) or {})
        for ref, known_id in refs.items():
            if known_id == transaction_id:
                return ref
        ref = f"{TRANSACTION_REF_PREFIX}{len(refs) + 1}"
        refs[ref] = transaction_id
        self._raw[_Key.TRANSACTION_REFS] = refs
        return ref

    def resolve_ref(self, ref: str | None) -> str | None:
        """Internal id for a reference issued in this session, or None."""
        return (self._raw.get(_Key.TRANSACTION_REFS) or {}).get(ref or "")

    # ---- explanation rejected: dispute under human review ------------------------------
    @property
    def explained_transaction(self) -> str | None:
        """The last charge the agent explained (case lane `explain`)."""
        return self._raw.get(_Key.EXPLAINED_TRANSACTION)

    @explained_transaction.setter
    def explained_transaction(self, transaction_id: str | None) -> None:
        self._raw[_Key.EXPLAINED_TRANSACTION] = transaction_id

    @property
    def review_transaction(self) -> str | None:
        """The explained charge the customer rejected: it may go to a person as a dispute."""
        return self._raw.get(_Key.REVIEW_TRANSACTION)

    @review_transaction.setter
    def review_transaction(self, transaction_id: str | None) -> None:
        self._raw[_Key.REVIEW_TRANSACTION] = transaction_id

    # ---- what the customer reported in the web form ------------------------------------
    @property
    def case_report(self) -> CaseReport | None:
        """The structured report of the case this conversation works, if any."""
        raw = self._raw.get(_Key.CASE_REPORT)
        return CaseReport.model_validate(raw) if raw else None

    @case_report.setter
    def case_report(self, report: CaseReport) -> None:
        self._raw[_Key.CASE_REPORT] = report.model_dump(mode="json")

    # ---- placeholders for bank records (see domain/pseudonyms.py) ------------------------
    @property
    def pseudonyms(self) -> dict[str, str]:
        """Placeholder -> value table of this session; it never leaves the service."""
        return dict(self._raw.get(_Key.PSEUDONYMS) or {})

    @pseudonyms.setter
    def pseudonyms(self, table: dict[str, str]) -> None:
        self._raw[_Key.PSEUDONYMS] = table

    # ---- evidence, approvals and actions ------------------------------------------------
    @property
    def evidence(self) -> list[Evidence]:
        """Evidence gathered in this session."""
        return [
            Evidence.model_validate(item) for item in self._raw.get(_Key.EVIDENCE, [])
        ]

    def upsert_evidence(self, evidence: Evidence) -> None:
        """Store evidence, replacing earlier evidence for the same transaction."""
        kept = [
            item
            for item in self._raw.get(_Key.EVIDENCE, [])
            if item["transaction_id"] != evidence.transaction_id
        ]
        self._raw[_Key.EVIDENCE] = [*kept, evidence.model_dump(mode="json")]

    def has_evidence_for(self, transaction_id: str | None) -> bool:
        """Whether evidence was gathered for the transaction in this session."""
        return any(
            item["transaction_id"] == transaction_id
            for item in self._raw.get(_Key.EVIDENCE, [])
        )

    @property
    def approval_ids(self) -> list[str]:
        """Approval requests created in this session, oldest first."""
        return list(self._raw.get(_Key.APPROVAL_IDS) or [])

    @approval_ids.setter
    def approval_ids(self, approval_ids: list[str]) -> None:
        self._raw[_Key.APPROVAL_IDS] = approval_ids

    @property
    def actions(self) -> list[dict]:
        """Actions taken in this session, in order."""
        return list(self._raw.get(_Key.ACTIONS, []))

    def record_action(self, action: dict) -> None:
        """Append an action to the session record."""
        self._raw[_Key.ACTIONS] = [*self.actions, action]

    @property
    def handoff_id(self) -> str | None:
        """Id of the handoff submitted for this session."""
        return self._raw.get(_Key.HANDOFF_ID)

    @handoff_id.setter
    def handoff_id(self, handoff_id: str) -> None:
        self._raw[_Key.HANDOFF_ID] = handoff_id

    def _load_outcome(self, key: _Key) -> Outcome | None:
        raw = self._raw.get(key)
        return Outcome.model_validate(raw) if raw else None
