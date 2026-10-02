"""before_agent refuses unusable sessions before the agent (and model) run."""

from lir_agent.domain.errors import AuthError
from tests.support import make_context


def test_unauthenticated_session_is_refused_without_calling_the_model(harness):
    reply = harness.callbacks.before_agent(make_context(authenticated=False))
    assert reply is not None
    assert "iniciar sesión" in reply.parts[0].text
    assert "session_refused" in harness.audit.events()


def test_expired_session_is_refused(harness):
    reply = harness.callbacks.before_agent(make_context(ttl_minutes=-1))
    assert reply is not None
    assert harness.audit.entries[-1]["reason"] == AuthError.EXPIRED.value


def test_valid_session_runs_the_agent(harness, context):
    assert harness.callbacks.before_agent(context) is None


def test_dev_session_is_seeded_only_when_configured(make_harness, settings):
    settings.dev_customer_id = "CLI-DEMO-001"
    harness = make_harness()
    context = make_context(authenticated=False)
    assert harness.callbacks.before_agent(context) is None
    assert context.state["auth_method"] == "dev_test_session"
