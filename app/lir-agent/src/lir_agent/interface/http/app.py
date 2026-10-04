"""FastAPI application: create a customer session, then exchange messages with the agent.

Authentication happens upstream: on Cloud Run, IAP verifies the caller's Google identity and
forwards it in a header. That identity is the *operator* (a tester or the bank channel) and
owns the sessions it creates. The *customer* is chosen on session creation, standing in for
the bank's identity check (biometric KYC, mocked).

Cases filed from the web form (`POST /v1/cases`) come through API Gateway instead, which
verifies the *customer's* JWT and forwards its claims.
"""

import re
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from lir_agent.application.ports import (
    ConversationNotFoundError,
    Conversations,
    CustomerNotFoundError,
)
from lir_agent.application.use_cases import SubmitCase
from lir_agent.config.settings import Settings
from lir_agent.domain.case_intake import (
    ForeignCaseError,
    IdempotencyKeyMismatchError,
    UnknownTransactionError,
)
from lir_agent.interface.http.cases import customer_from_userinfo, schema_errors

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


class MessageResponse(BaseModel):
    """The agent's reply to one customer message."""

    reply: str


class CaseAcceptedResponse(BaseModel):
    """`202` body of `POST /v1/cases` (lir-web contract)."""

    case_id: str
    folio: str
    status: str
    telegram_start_url: str | None


def _invalid_case() -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, "invalid case")


def _field_errors(errors: dict[str, str]) -> JSONResponse:
    return JSONResponse({"errors": errors}, status.HTTP_422_UNPROCESSABLE_CONTENT)


def _real_case_intake(settings: Settings) -> SubmitCase:
    """Case intake wired from settings (local or Cloud Storage inbox)."""
    from lir_agent.container import build_container

    return build_container(settings).submit_case


def _real_conversations(settings: Settings) -> Conversations:
    """The real agent behind ADK; imported lazily because the agent stack is heavy."""
    from lir_agent.interface.adk import build_conversations

    return build_conversations(settings)


def create_app(
    settings: Settings,
    conversations: Conversations | None = None,
    submit_case: SubmitCase | None = None,
) -> FastAPI:
    """Build the API; tests inject `conversations` and `submit_case`, else the real ones."""
    agent = conversations or _real_conversations(settings)
    intake = submit_case or _real_case_intake(settings)
    session_ttl = timedelta(minutes=settings.session_ttl_minutes)
    customer_id_pattern = re.compile(settings.customer_id_pattern)

    class MessageRequest(BaseModel):
        """Body of `POST /v1/sessions/{session_id}/messages`."""

        text: str = Field(min_length=1, max_length=settings.max_message_chars)

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

    app = FastAPI(title="Lir agent API", version="1.0.0")
    if origins := [o.strip() for o in settings.cors_origins.split(",") if o.strip()]:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Idempotency-Key", "Authorization"],
        )

    # Not /healthz: Cloud Run reserves public paths ending in "z".
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/sessions", status_code=status.HTTP_201_CREATED)
    async def start_session(
        body: StartSessionRequest, caller: Annotated[str, Depends(operator)]
    ) -> StartSessionResponse:
        if not customer_id_pattern.fullmatch(body.customer_id):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid customer_id"
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
                status.HTTP_404_NOT_FOUND, "Customer not found"
            ) from None
        return StartSessionResponse(
            session_id=started.session_id, expires_at=started.expires_at.isoformat()
        )

    @app.post("/v1/sessions/{session_id}/messages")
    async def send_message(
        session_id: str, body: MessageRequest, caller: Annotated[str, Depends(operator)]
    ) -> MessageResponse:
        try:
            reply = await agent.send(caller, session_id, body.text.strip())
        except ConversationNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Session not found"
            ) from None
        return MessageResponse(reply=reply)

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
        return CaseAcceptedResponse(
            case_id=receipt.case_id,
            folio=receipt.folio,
            status=receipt.status,
            telegram_start_url=receipt.telegram_start_url,
        )

    return app
