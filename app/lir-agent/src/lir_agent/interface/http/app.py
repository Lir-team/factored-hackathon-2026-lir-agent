"""FastAPI application: create a customer session, then exchange messages with the agent.

Authentication happens upstream: on Cloud Run, IAP verifies the caller's Google identity and
forwards it in a header. That identity is the *operator* (a tester or the bank channel) and
owns the sessions it creates. The *customer* is chosen on session creation, standing in for
the bank's identity check (biometric KYC, mocked).

Cases filed from the web form (`POST /v1/cases`) come through API Gateway instead, which
verifies the *customer's* JWT and forwards its claims. Each accepted case is published to
Pub/Sub and pushed back to the agent (`POST /pubsub/push`, see `pubsub.py`), which works it
at once; the customer continues on Telegram (`POST /channels/telegram`, see `telegram.py`).
"""

import re
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from lir_agent.application.ports import (
    CaseInProgressError,
    CasePublishError,
    ConversationNotFoundError,
    Conversations,
    CustomerNotFoundError,
    Messenger,
)
from lir_agent.application.use_cases import AnswerTelegramMessage, ProcessCase
from lir_agent.config.settings import Settings
from lir_agent.domain.case_intake import (
    ForeignCaseError,
    IdempotencyKeyMismatchError,
    UnknownTransactionError,
)
from lir_agent.interface.http.approvals import ApprovalView, approvals_router
from lir_agent.interface.http.cases import customer_from_userinfo, schema_errors
from lir_agent.interface.http.pubsub import (
    TokenVerifier,
    google_token_verifier,
    pubsub_router,
)
from lir_agent.interface.http.telegram import telegram_router

if TYPE_CHECKING:
    from lir_agent.container import Container

# IAP prefixes the e-mail with the identity provider.
_IAP_PREFIX = "accounts.google.com:"


class StartSessionRequest(BaseModel):
    """Body of `POST /v1/sessions`."""

    customer_id: str

    @field_validator("customer_id")
    @classmethod
    def _normalize(cls, value: str) -> str:
        return value.strip().upper()


class StartSessionResponse(BaseModel):
    """A new session, valid until `expires_at`."""

    session_id: str
    expires_at: str


class TransactionView(BaseModel):
    """One transaction of the signed-in customer, as the bank's web page shows it."""

    transaction_id: str
    occurred_at: str
    merchant: str | None
    amount: float | None
    currency: str | None
    country: str | None
    channel: str | None
    status: str | None


class MyTransactionsResponse(BaseModel):
    """The signed-in customer and their latest transactions, newest first."""

    customer_id: str
    first_name: str | None
    country: str | None
    transactions: list[TransactionView]


class TraceView(BaseModel):
    """How one turn was decided (returned only when `expose_trace` is on)."""

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


class MessageResponse(BaseModel):
    """The agent's reply to one customer message, and its trace when enabled."""

    reply: str
    trace: TraceView | None = None
    # Important actions waiting for the customer's approval (human in the loop): show them
    # as cards with approve and reject buttons next to the reply.
    approvals: list[ApprovalView] = Field(default_factory=list)


class CaseAcceptedResponse(BaseModel):
    """`202` body of `POST /v1/cases` (lir-web contract)."""

    case_id: str
    folio: str
    status: str
    telegram_start_url: str | None


def _signed_in_customer(request: Request, settings: Settings) -> str | None:
    """The customer API Gateway verified (JWT userinfo), or None without a readable one."""
    userinfo = request.headers.get(settings.customer_identity_header, "").strip()
    return customer_from_userinfo(userinfo, settings.customer_claim) if userinfo else None


def _invalid_case() -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, "invalid case")


def _field_errors(errors: dict[str, str]) -> JSONResponse:
    return JSONResponse({"errors": errors}, status.HTTP_422_UNPROCESSABLE_CONTENT)


def _real_container(settings: Settings) -> "Container":
    """Case intake and its store wired from settings (local or Cloud Storage inbox)."""
    from lir_agent.container import build_container

    return build_container(settings)


def _real_messenger(bot_token: str) -> Messenger:
    """The Telegram Bot API."""
    from lir_agent.infrastructure.messaging import TelegramBotMessenger

    return TelegramBotMessenger(bot_token)


def _telegram_credentials(settings: Settings) -> tuple[str, str] | None:
    """Bot token and webhook secret, or None when the channel is not configured."""
    token, secret = settings.telegram_bot_token, settings.telegram_webhook_secret
    if token and secret and token.get_secret_value() and secret.get_secret_value():
        return token.get_secret_value(), secret.get_secret_value()
    return None


def _push_verifier(
    settings: Settings, injected: TokenVerifier | None
) -> tuple[bool, TokenVerifier | None]:
    """Whether the push route exists, and how its OIDC token is verified (None: it is not)."""
    if not settings.pubsub_verify_token:
        return True, None
    if not settings.pubsub_push_audience:
        return False, None
    return True, injected or google_token_verifier(settings.pubsub_push_audience)


def _real_conversations(settings: Settings, container: "Container") -> Conversations:
    """The real agent behind ADK, sharing the app container (one case store).

    Imported lazily because the agent stack is heavy.
    """
    from lir_agent.interface.adk import build_conversations

    return build_conversations(settings, container)


def create_app(
    settings: Settings,
    conversations: Conversations | None = None,
    container: "Container | None" = None,
    messenger: Messenger | None = None,
    push_token_verifier: TokenVerifier | None = None,
) -> FastAPI:
    """Build the API; tests inject the agent, adapters and the push token verifier."""
    deps = container or _real_container(settings)
    agent = conversations or _real_conversations(settings, deps)
    intake = deps.submit_case
    session_ttl = timedelta(minutes=settings.session_ttl_minutes)
    customer_id_pattern = re.compile(settings.customer_id_pattern)

    class SessionRequest(StartSessionRequest):
        """Body of `POST /v1/sessions`: the customer the bank already verified (KYC, mocked)."""

        customer_id: str = Field(
            description=(
                f"Customer to open the session for: {settings.customer_id_format}. "
                "Stands in for the bank's identity check; the agent only sees this "
                "customer's records."
            ),
            examples=[settings.api_example_customer_id],
        )

    class MessageRequest(BaseModel):
        """Body of `POST /v1/sessions/{session_id}/messages`."""

        text: str = Field(
            min_length=1,
            max_length=settings.max_message_chars,
            description="What the customer writes, in Spanish or Portuguese.",
            examples=[settings.api_example_message],
        )

    identity_error = {
        401: {"description": "No caller identity (the service is reached through IAP)"}
    }

    def operator(request: Request) -> str:
        identity = request.headers.get(settings.identity_header, "").strip()
        if identity:
            return identity.removeprefix(_IAP_PREFIX)
        if settings.require_identity:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing caller identity")
        return settings.local_operator

    def customer(request: Request) -> str | None:
        """The customer verified by API Gateway; None only on local runs without it."""
        userinfo = request.headers.get(settings.customer_identity_header, "").strip()
        if userinfo:
            customer_id = customer_from_userinfo(userinfo, settings.customer_claim)
            if customer_id is None:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Unreadable customer identity"
                )
            return customer_id
        if settings.require_identity:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Missing customer identity"
            )
        return None

    app = FastAPI(
        title="Lir agent API",
        version="1.0.0",
        description=(
            "Customer service agent for charges the customer does not recognize.\n\n"
            "1. `POST /v1/sessions` with a `customer_id` "
            f"({settings.customer_id_format}); try `{settings.api_example_customer_id}`.\n"
            "2. `POST /v1/sessions/{session_id}/messages` with what the customer writes.\n"
            "3. If the agent hands the case to a specialist, read the case file at "
            "`GET /v1/handoffs/{handoff_id}/report.md`."
        ),
    )
    if origins := [o.strip() for o in settings.cors_origins.split(",") if o.strip()]:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Idempotency-Key", "Authorization", "X-Approval-Token"],
        )

    app.include_router(
        approvals_router(
            deps.approvals,
            deps.decide_approval,
            deps.verify_approval_link,
            settings.identity_header,
            signed_in_customer=lambda request: _signed_in_customer(request, settings),
            require_sign_in=settings.approval_requires_sign_in,
        )
    )

    # Not /healthz: Cloud Run reserves public paths ending in "z".
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/v1/sessions",
        status_code=status.HTTP_201_CREATED,
        responses={
            **identity_error,
            404: {"description": "No customer with this id in the data"},
            422: {"description": f"customer_id is not {settings.customer_id_format}"},
        },
    )
    async def start_session(
        body: SessionRequest, caller: Annotated[str, Depends(operator)]
    ) -> StartSessionResponse:
        if not customer_id_pattern.fullmatch(body.customer_id):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Invalid customer_id: expected {settings.customer_id_format}",
            )
        try:
            started = await agent.start(
                caller,
                body.customer_id,
                ttl=session_ttl,
                auth_method=settings.http_auth_method,
            )
        except CustomerNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Customer not found: expected {settings.customer_id_format}",
            ) from None
        return StartSessionResponse(
            session_id=started.session_id, expires_at=started.expires_at.isoformat()
        )

    @app.post(
        "/v1/sessions/{session_id}/messages",
        responses={
            **identity_error,
            404: {"description": "No session with this id for the caller (or expired)"},
            422: {"description": "Empty message or longer than the allowed length"},
        },
    )
    async def send_message(
        session_id: str, body: MessageRequest, caller: Annotated[str, Depends(operator)]
    ) -> MessageResponse:
        try:
            turn = await agent.converse(caller, session_id, body.text.strip())
        except ConversationNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Session not found"
            ) from None
        trace = TraceView(**vars(turn.trace)) if settings.expose_trace else None
        links = await deps.present_approvals.execute(turn.approvals)
        approvals = [
            ApprovalView.of(request, links.get(approval_id))
            for approval_id in turn.approvals
            if (request := deps.approvals.get(approval_id)) is not None
        ]
        return MessageResponse(reply=turn.reply, trace=trace, approvals=approvals)

    @app.get(
        "/v1/me/transactions",
        responses={
            401: {"description": "No verified customer identity (the bank sign-in JWT)"},
            404: {"description": "The signed-in customer is not in the data"},
        },
    )
    async def list_my_transactions(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=settings.transactions_max_limit)] = 20,
    ) -> MyTransactionsResponse:
        """The signed-in customer's latest transactions; the customer comes from the JWT."""
        customer_id = customer(request)
        if customer_id is None:
            # Never a local fallback: without the bank's sign-in there is no statement.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing customer identity")
        profile = await run_in_threadpool(deps.repository.get_customer, customer_id)
        if profile is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
        rows = await run_in_threadpool(deps.repository.list_transactions, customer_id)
        deps.audit.record("transactions_viewed", None, lines=min(len(rows), limit))
        return MyTransactionsResponse(
            customer_id=profile.customer_id,
            first_name=profile.first_name,
            country=profile.country,
            transactions=[
                TransactionView(
                    transaction_id=t.transaction_id,
                    occurred_at=t.transaction_date.isoformat(),
                    merchant=t.merchant_name,
                    amount=t.amount,
                    currency=t.currency,
                    country=t.transaction_country,
                    channel=t.channel,
                    status=t.transaction_status,
                )
                for t in rows[:limit]
            ],
        )

    @app.get(
        "/v1/handoffs/{handoff_id}/report.md",
        response_class=PlainTextResponse,
        responses={**identity_error, 404: {"description": "Handoff not found"}},
    )
    async def handoff_report(
        handoff_id: str,
        caller: Annotated[str, Depends(operator)],
        language: str | None = None,
    ) -> PlainTextResponse:
        """The case file for the bank specialist, in Markdown (es or pt)."""
        packet = deps.cases.get_handoff(handoff_id)
        if packet is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Handoff not found")
        # Reading a case file exposes customer data: who read it is audited.
        deps.audit.record(
            "handoff_report_viewed", None, handoff_id=handoff_id, operator=caller
        )
        return PlainTextResponse(
            deps.handoff_report.markdown(packet, language),
            media_type="text/markdown; charset=utf-8",
        )

    @app.post(
        "/v1/cases",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=CaseAcceptedResponse,
    )
    async def submit(
        request: Request,
        caller: Annotated[str | None, Depends(customer)],
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> CaseAcceptedResponse | JSONResponse:
        try:
            payload = await request.json()
        except ValueError:
            raise _invalid_case() from None
        errors = schema_errors(payload)
        if errors is not None:
            if not errors:  # no error names a form field the client can show
                raise _invalid_case()
            return _field_errors(errors)
        if not idempotency_key:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing Idempotency-Key")
        customer_id = caller or payload["customer"]["customer_id"]
        try:
            # Sync adapters (Cloud Storage) must not block the event loop.
            receipt = await run_in_threadpool(
                intake.execute, customer_id, idempotency_key, payload
            )
        except IdempotencyKeyMismatchError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Idempotency-Key must be the case_id"
            ) from None
        except ForeignCaseError:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your case") from None
        except CustomerNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Customer not found"
            ) from None
        except UnknownTransactionError:
            return _field_errors({"transaction_ids": "unknown"})
        except CaseInProgressError:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Case still being accepted, retry"
            ) from None
        except CasePublishError:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Case not accepted, retry"
            ) from None
        return CaseAcceptedResponse(
            case_id=receipt.case_id,
            folio=receipt.folio,
            status=receipt.status,
            telegram_start_url=receipt.telegram_start_url,
        )

    credentials = _telegram_credentials(settings)
    if messenger is None and credentials:
        messenger = deps.messenger or _real_messenger(credentials[0])

    push_on, verifier = _push_verifier(settings, push_token_verifier)
    if push_on:
        process = ProcessCase(
            agent,
            deps.case_store,
            messenger,
            deps.audit,
            session_ttl=timedelta(minutes=settings.case_session_idle_minutes),
            max_session_ttl=timedelta(minutes=settings.case_session_max_minutes),
            present_approvals=deps.present_approvals,
        )
        app.include_router(
            pubsub_router(
                process, deps.audit, verifier, settings.pubsub_push_service_account
            )
        )

    if credentials and messenger is not None:
        answer = AnswerTelegramMessage(
            agent,
            deps.case_store,
            messenger,
            deps.audit,
            max_message_chars=settings.max_message_chars,
            present_approvals=deps.present_approvals,
        )
        app.include_router(
            telegram_router(credentials[1], answer, deps.answer_approval_button)
        )

    return app
