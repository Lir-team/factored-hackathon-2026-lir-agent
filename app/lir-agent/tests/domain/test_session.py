from datetime import timedelta

from lir_agent.domain.errors import AuthError
from lir_agent.domain.session import SessionState


def test_unauthenticated_session():
    assert SessionState({}).auth_error() is AuthError.MISSING


def test_expired_session():
    session = SessionState({})
    session.start("CLI-X", timedelta(minutes=-1), "test")
    assert session.auth_error() is AuthError.EXPIRED


def test_refs_are_stable_and_opaque():
    session = SessionState({})
    first, second = session.ref_for("TXN-A"), session.ref_for("TXN-B")
    assert (first, second, session.ref_for("TXN-A")) == ("T1", "T2", "T1")
    assert session.resolve_ref("T2") == "TXN-B"
    assert session.resolve_ref("T99") is None


def test_state_stays_json_serializable():
    import json

    raw: dict = {}
    session = SessionState(raw)
    session.start("CLI-X", timedelta(minutes=5), "test")
    session.ref_for("TXN-A")
    session.record_action({"action": "handoff"})
    json.dumps(raw)
