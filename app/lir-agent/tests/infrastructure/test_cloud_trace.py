"""ADK's spans reach the configured exporter, named after the service."""

from opentelemetry import trace
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from lir_agent.infrastructure.observability import configure_cloud_trace


class Collector(SpanExporter):
    def __init__(self):
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS


def test_spans_are_exported_with_the_service_name():
    collector = Collector()
    configure_cloud_trace("lir-agent", "lir-agent-cases", exporter=collector)

    with trace.get_tracer("gcp.vertex.agent").start_as_current_span("invoke_agent"):
        pass
    trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]

    [span] = collector.spans
    assert span.name == "invoke_agent"
    assert span.resource.attributes["service.name"] == "lir-agent-cases"
