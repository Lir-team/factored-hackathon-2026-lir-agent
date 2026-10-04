"""HTTP interface: the agent behind a small JSON API, served on Cloud Run behind IAP."""

from lir_agent.interface.http.app import create_app

__all__ = ["create_app"]
