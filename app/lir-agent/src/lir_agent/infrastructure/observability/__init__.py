"""Observability adapters: request traces to Cloud Trace."""

from lir_agent.infrastructure.observability.cloud_trace import configure_cloud_trace

__all__ = ["configure_cloud_trace"]
