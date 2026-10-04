"""Pub/Sub push: `POST /pubsub/push`, called by the `lir-cases` push subscription.

Pub/Sub signs each call with a Google OIDC token (`Authorization: Bearer ...`) for the
subscription's audience. A `2xx` acknowledges the message; anything else makes Pub/Sub
redeliver it. A message that can never be worked (unreadable, not a valid case) is
acknowledged and audited as `case_rejected`, so it is not redelivered forever; agent
failures answer `500`, so the case is retried.
"""

import base64
import binascii
import json
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from lir_agent.application.ports import AuditSink
from lir_agent.application.use_cases import ProcessCase
from lir_agent.interface.http.cases import schema_errors

# Checks an OIDC token and returns its claims; raises ValueError when it is not valid.
TokenVerifier = Callable[[str], Mapping[str, Any]]

_BEARER = "Bearer "


class _Message(BaseModel):
    data: str
    message_id: str = Field(default="", alias="messageId")


class _Envelope(BaseModel):
    """The only fields read from a push body."""

    message: _Message


def google_token_verifier(audience: str) -> TokenVerifier:
    """Verify Google-signed OIDC tokens for `audience` (fetches Google's public keys)."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    transport = google_requests.Request()

    def verify(token: str) -> Mapping[str, Any]:
        return id_token.verify_oauth2_token(token, transport, audience=audience)

    return verify


def pubsub_router(
    process: ProcessCase,
    audit: AuditSink,
    verifier: TokenVerifier | None,
    service_account: str | None = None,
) -> APIRouter:
    """The push route; `verifier` None accepts unsigned calls (local emulator only)."""
    router = APIRouter()

    async def authorize(request: Request) -> None:
        if verifier is None:
            return
        header = request.headers.get("Authorization", "")
        if not header.startswith(_BEARER):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")
        try:
            # Blocking: Google's public keys are fetched over HTTP.
            claims = await run_in_threadpool(verifier, header.removeprefix(_BEARER))
        except ValueError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from None
        if service_account and not (
            claims.get("email") == service_account and claims.get("email_verified")
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unexpected caller")

    def reject(reason: str, message_id: str = "") -> Response:
        audit.record("case_rejected", None, reason=reason, message_id=message_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/pubsub/push")
    async def push(request: Request) -> Response:
        await authorize(request)
        try:
            message = _Envelope.model_validate(await request.json()).message
        except ValueError:  # bad JSON, or not a push envelope
            return reject("unreadable_envelope")
        try:
            payload = json.loads(base64.b64decode(message.data, validate=True))
        except (binascii.Error, ValueError):  # bad base64, UTF-8 or JSON
            return reject("unreadable_data", message.message_id)
        if schema_errors(payload) is not None:
            return reject("invalid_case", message.message_id)
        await process.execute(payload)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
