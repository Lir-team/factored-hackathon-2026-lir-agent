"""JwtSigner on the IAM Credentials API: a service account signs, no private key leaves Google.

`signJwt` accepts tokens that expire at most 12 hours after they are issued.
"""

import json
from collections.abc import Mapping
from typing import Any

from lir_agent.application.ports import SigningError

_SIGN_URL = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{}:signJwt"
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_TIMEOUT_SECONDS = 10.0


class IamJwtSigner:
    """Signs JWTs as `service_account`; the caller needs serviceAccountTokenCreator on it."""

    def __init__(self, service_account: str, session: Any = None) -> None:
        """Keep the account; `session` (an authorized requests session) is made on first use."""
        self._url = _SIGN_URL.format(service_account)
        self._session = session

    def sign(self, claims: Mapping[str, Any]) -> str:
        """The signed JWT.

        Raises:
            SigningError: If the IAM API refused or failed.
        """
        try:
            response = self._authorized().post(
                self._url, json={"payload": json.dumps(dict(claims))}, timeout=_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return response.json()["signedJwt"]
        except Exception as error:
            raise SigningError(str(error)) from error

    def _authorized(self) -> Any:
        if self._session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(scopes=_SCOPES)
            self._session = AuthorizedSession(credentials)
        return self._session
