"""FastAPI application: create a customer session, then exchange messages with the agent.

Authentication happens upstream: on Cloud Run, IAP verifies the caller's Google identity and
forwards it in a header. That identity is the *operator* (a tester or the bank channel) and
owns the sessions it creates. The *customer* is chosen on session creation, standing in for
the bank's identity check (biometric KYC, mocked).
"""

import re
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from google.adk.runners import InMemoryRunner
from pydantic import BaseModel, Field, field_validator

from lir_agent.config.settings import Settings
from lir_agent.interface.http.gateway import (
    AgentGateway,
    CustomerNotFoundError,
    SessionNotFoundError,
)

APP_NAME = "lir"
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


def build_gateway(settings: Settings) -> AgentGateway:
    """Wire the real agent behind an in-memory ADK runner, sharing one container."""
    from lir_agent.container import build_container  # heavy imports, only when serving
    from lir_agent.interface.adk import build_agent

    container = build_container(settings)
    runner = InMemoryRunner(agent=build_agent(container=container), app_name=APP_NAME)
    return AgentGateway(
        runner,
        customers=container.repository,
        audit=container.audit,
        session_ttl=timedelta(minutes=settings.session_ttl_minutes),
        auth_method=settings.http_auth_method,
    )


def create_app(settings: Settings, gateway: AgentGateway | None = None) -> FastAPI:
    """Build the API. `gateway` is injected in tests; by default the real agent is wired."""
    agent_gateway = gateway or build_gateway(settings)
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

    app = FastAPI(title="Lir agent API", version="1.0.0")

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
            started = await agent_gateway.start_session(caller, body.customer_id)
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
            reply = await agent_gateway.send(caller, session_id, body.text.strip())
        except SessionNotFoundError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Session not found"
            ) from None
        return MessageResponse(reply=reply)

    return app
