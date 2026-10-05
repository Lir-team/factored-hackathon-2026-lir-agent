"""Traces to Cloud Trace: Google ADK's OpenTelemetry spans for every turn.

ADK already emits a span per agent invocation, LLM call and tool call; this only gives them
a provider that exports to Cloud Trace, so each turn shows as a waterfall with its latency.
"""

from typing import Any

from opentelemetry import trace


def configure_cloud_trace(project_id: str, service_name: str, exporter: Any = None) -> None:
    """Export every span to Cloud Trace in `project_id`; `exporter` replaces it in tests."""
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if exporter is None:
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

        exporter = CloudTraceSpanExporter(project_id=project_id)
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
