import io
import json

from lir_agent.config.settings import Settings
from lir_agent.container import build_audit
from lir_agent.infrastructure.audit import JsonlAuditSink, StdoutAuditSink


def test_entries_are_structured_json_for_cloud_logging():
    stream = io.StringIO()
    entry = StdoutAuditSink(stream).record("tool_call", "s1", tool="find")
    line = json.loads(stream.getvalue())
    assert line["severity"] == "INFO"
    assert line["message"] == "audit tool_call"
    assert line["tool"] == "find" and line["session_id"] == "s1"
    assert entry == {k: line[k] for k in entry}


def test_audit_sink_follows_the_setting(settings: Settings):
    assert isinstance(build_audit(settings), JsonlAuditSink)
    stdout = settings.model_copy(update={"audit_sink": "stdout"})
    assert isinstance(build_audit(stdout), StdoutAuditSink)
