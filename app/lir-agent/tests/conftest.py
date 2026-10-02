"""Pytest fixtures. Fakes and helpers live in tests/support.py."""

from types import SimpleNamespace

import pytest

from lir_agent.config.settings import Settings
from lir_agent.container import build_container
from lir_agent.infrastructure.audit import InMemoryAuditSink
from tests.support import IN_SCOPE, Harness, ScriptedDecisions, make_context


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    # `.env` is read from the working directory: an empty tmp dir keeps it out of tests.
    monkeypatch.chdir(tmp_path)
    return Settings(store="fixture", audit_path=tmp_path / "audit.jsonl")


@pytest.fixture
def make_harness(settings):
    def _make(answers: dict[str, tuple] | None = None, fail: bool = False) -> Harness:
        audit = InMemoryAuditSink()
        decisions = ScriptedDecisions(
            IN_SCOPE if answers is None else answers, fail=fail
        )
        return Harness(
            build_container(settings, audit=audit, decisions=decisions), audit
        )

    return _make


@pytest.fixture
def harness(make_harness) -> Harness:
    return make_harness()


@pytest.fixture
def context() -> SimpleNamespace:
    return make_context()
