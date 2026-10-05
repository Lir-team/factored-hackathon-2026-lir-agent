"""Use case: the demo bank sign-in, a short-lived customer JWT for one configured customer.

It stands in for the bank's identity provider: API Gateway verifies the token like any
customer JWT, and every route still takes the customer from the verified claims.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from lir_agent.application.ports import AuditSink, JwtSigner
from lir_agent.domain.session import utc_now


@dataclass(frozen=True)
class DemoSession:
    """A signed customer token and when it expires."""

    token: str
    expires_at: datetime


class IssueDemoSession:
    """Signs a token for the demo customer, valid for `ttl`."""

    def __init__(
        self,
        signer: JwtSigner,
        audit: AuditSink,
        *,
        customer_id: str,
        issuer: str,
        audience: str,
        ttl: timedelta,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """Keep the signer, the claims to issue and the token lifetime."""
        self._signer = signer
        self._audit = audit
        self._customer_id = customer_id
        self._issuer = issuer
        self._audience = audience
        self._ttl = ttl
        self._clock = clock

    def execute(self) -> DemoSession:
        """A fresh token.

        Raises:
            SigningError: If the token could not be signed.
        """
        now = self._clock()
        expires_at = now + self._ttl
        token = self._signer.sign(
            {
                "iss": self._issuer,
                "sub": self._customer_id,
                "aud": self._audience,
                "iat": int(now.timestamp()),
                "exp": int(expires_at.timestamp()),
            }
        )
        self._audit.record("demo_sign_in", None, customer_id=self._customer_id)
        return DemoSession(token=token, expires_at=expires_at)
