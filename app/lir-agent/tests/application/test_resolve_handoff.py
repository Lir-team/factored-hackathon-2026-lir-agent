"""A specialist resolves a handed-off case: recorded once, the customer and the team told."""

import asyncio
from datetime import UTC, datetime

import pytest

from lir_agent.application.ports import MessageNotSentError
from lir_agent.application.use_cases import HandoffResolutionError, ResolveHandoff
from lir_agent.domain.case_intake import CaseReport
from lir_agent.domain.language import Language
from lir_agent.domain.telegram import ChatLink
from lir_agent.infrastructure.audit.in_memory import InMemoryAuditSink
from lir_agent.infrastructure.case_store import InMemoryCaseStore
from lir_agent.infrastructure.cases import InMemoryCaseRepository
from lir_agent.infrastructure.resources import ResourceLoader
from tests.application.test_handoff_report import packet

NOW = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)
CASE_ID = "case-1"
CHAT = 4242


class Chat:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send(self, chat_id, text):
        if self.fail:
            raise MessageNotSentError("telegram down")
        self.sent.append((chat_id, text))


class Team:
    def __init__(self):
        self.resolved = []

    def notify(self, packet):
        raise AssertionError("not a new handoff")

    def notify_resolution(self, packet):
        self.resolved.append(packet)


class World:
    def __init__(self, settings, language: Language = "es", chat=None, linked=True):
        self.cases = InMemoryCaseRepository()
        self.store = InMemoryCaseStore()
        self.audit = InMemoryAuditSink()
        self.chat = chat or Chat()
        self.team = Team()
        report = CaseReport(
            category="unrecognized_charge",
            fraud_suspected=True,
            freeze_card_requested=False,
            case_id=CASE_ID,
        )
        self.cases.submit_handoff(packet(case_report=report))
        if linked:
            self.store.link_chat(CHAT, ChatLink(case_id=CASE_ID, folio="LB-1", language=language))
        self.resolve = ResolveHandoff(
            self.cases,
            self.store,
            self.audit,
            ResourceLoader().load_labels(settings.approval_labels_path),
            self.chat,
            self.team,
            clock=lambda: NOW,
        )

    def run(self, accepted=True, note=None):
        return asyncio.run(self.resolve.execute("HND-ABC", "ana@bank", accepted, note))


def test_accepting_records_the_decision_and_tells_the_customer_and_the_team(settings):
    world = World(settings)

    resolved = world.run(accepted=True, note="Fraude confirmado con el comercio")

    assert resolved.resolution is not None
    assert (resolved.resolution.accepted, resolved.resolution.resolved_by) == (True, "ana@bank")
    assert world.cases.get_handoff("HND-ABC") == resolved
    [(chat, text)] = world.chat.sent
    assert chat == CHAT and "aceptó tu reclamo" in text
    assert "devol" not in text and "reembols" not in text  # never promises money
    assert world.team.resolved == [resolved]
    [entry] = [e for e in world.audit.entries if e["event"] == "handoff_resolved"]
    assert (entry["decision"], entry["operator"], entry["customer_told"]) == (
        "accepted", "ana@bank", True,
    )


def test_rejecting_speaks_the_customers_language(settings):
    world = World(settings, language="pt")

    world.run(accepted=False)

    [(_, text)] = world.chat.sent
    assert "não aceitou" in text


def test_a_case_is_resolved_only_once(settings):
    world = World(settings)
    world.run()

    with pytest.raises(HandoffResolutionError) as error:
        world.run(accepted=False)

    assert error.value.code == "already_resolved"
    assert len(world.chat.sent) == 1


def test_an_unknown_case_is_not_found(settings):
    world = World(settings)
    with pytest.raises(HandoffResolutionError) as error:
        asyncio.run(world.resolve.execute("HND-NOPE", "ana@bank", True))
    assert error.value.code == "not_found"


@pytest.mark.parametrize("world_args", [{"linked": False}, {"chat": Chat(fail=True)}])
def test_without_a_reachable_chat_the_decision_still_stands(settings, world_args):
    world = World(settings, **world_args)

    resolved = world.run()

    assert world.cases.get_handoff("HND-ABC") == resolved
    [entry] = [e for e in world.audit.entries if e["event"] == "handoff_resolved"]
    assert entry["customer_told"] is False


def test_an_outcome_decided_before_the_chat_linked_is_delivered_once_on_link(settings):
    world = World(settings, linked=False)
    world.run(accepted=True)
    assert world.chat.sent == []

    world.store.link_chat(CHAT, ChatLink(case_id=CASE_ID, folio="LB-1", language="es"))
    asyncio.run(world.resolve.deliver_pending(CASE_ID))
    asyncio.run(world.resolve.deliver_pending(CASE_ID))

    [(chat, text)] = world.chat.sent
    assert chat == CHAT and "aceptó tu reclamo" in text
    stored = world.cases.get_handoff("HND-ABC")
    assert stored is not None and stored.resolution is not None
    assert stored.resolution.customer_notified is True
    events = [e["event"] for e in world.audit.entries]
    assert events.count("handoff_resolution_delivered") == 1


def test_an_outcome_told_at_once_is_not_sent_again_on_link(settings):
    world = World(settings)
    world.run(accepted=False)

    asyncio.run(world.resolve.deliver_pending(CASE_ID))

    assert len(world.chat.sent) == 1


def test_other_cases_are_left_alone_on_link(settings):
    world = World(settings, linked=False)
    world.run()

    asyncio.run(world.resolve.deliver_pending("another-case"))

    assert world.chat.sent == []
