from datetime import timedelta

from google.adk.tools import FunctionTool

from lir_agent.domain.session import SessionState
from lir_agent.interface.adk import AgentFactory
from tests.support import CUSTOMER, OTHER_CUSTOMER_TXN

PARAMETERLESS_TOOLS = {"get_my_customer_profile"}


def model_facing_properties(method) -> dict:
    declaration = FunctionTool(method)._get_declaration()
    assert declaration is not None
    if declaration.parameters_json_schema is not None:
        return declaration.parameters_json_schema.get("properties", {})
    if declaration.parameters is not None:
        return declaration.parameters.properties or {}
    return {}


def test_invalid_date_becomes_tool_error(harness, context):
    result = harness.toolkit.find_candidate_transactions(
        context, date_from="14/06/2026"
    )
    assert result["error"] == "invalid_date"


def test_not_found_becomes_tool_result(harness, context):
    ref = SessionState(context.state).ref_for(OTHER_CUSTOMER_TXN)
    assert harness.toolkit.get_transaction_evidence(context, ref) == {
        "status": "not_found"
    }


def test_profile_returns_only_allowlisted_fields(harness, context):
    result = harness.toolkit.get_my_customer_profile(context)
    allowed = set(harness.container.policy.config.llm_exposure.customer_fields)
    assert result["found"] is True
    assert set(result["customer"]) == allowed
    assert CUSTOMER not in str(result)


def test_profile_of_unknown_customer_is_not_found(harness, context):
    SessionState(context.state).start("CLI-UNKNOWN", timedelta(minutes=5), "test")
    assert harness.toolkit.get_my_customer_profile(context) == {"found": False}


def test_agent_builds_and_tool_schemas_hide_session_fields(harness):
    agent = AgentFactory(harness.container).create()
    for method in agent.tools:
        properties = model_facing_properties(method)
        if method.__name__ in PARAMETERLESS_TOOLS:
            assert properties == {}, f"{method.__name__} must take no arguments"
        else:
            assert properties, (
                f"{method.__name__} exposes no parameters; schema not inspected"
            )
        assert "tool_context" not in properties
        assert "customer_id" not in properties
        assert "self" not in properties


def test_model_sees_transaction_type_and_channel(harness, context):
    found = harness.toolkit.find_candidate_transactions(
        context, date_from="2026-01-01", merchant_hint="Spotify"
    )
    candidate = found["candidates"][0]
    assert (candidate["transaction_type"], candidate["channel"]) == ("Purchase", "Web")
    evidence = harness.toolkit.get_transaction_evidence(
        context, candidate["transaction_ref"]
    )["evidence"]
    assert (evidence["transaction_type"], evidence["channel"]) == ("Purchase", "Web")
