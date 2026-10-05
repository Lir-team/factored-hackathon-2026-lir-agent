"""Disputes and handoffs survive a round trip through Firestore documents (fake client)."""

import json

from lir_agent.infrastructure.cases import FirestoreCaseRepository
from tests.application.test_handoff_report import packet


class FakeSnapshot:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return json.loads(json.dumps(self._data))


class FakeDocument:
    def __init__(self, store, key):
        self._store, self._key = store, key

    def set(self, data):
        json.dumps(data)  # Firestore only stores JSON-like values
        self._store[self._key] = data

    def get(self):
        return FakeSnapshot(self._store.get(self._key))


class FakeCollection:
    def __init__(self):
        self.docs = {}

    def document(self, key):
        return FakeDocument(self.docs, key)


def _stream(collection):
    return [FakeSnapshot(data) for data in collection.docs.values()]


FakeCollection.stream = _stream  # type: ignore[attr-defined]


class FakeClient:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection())


def test_handoff_round_trips_with_its_evidence_and_outcomes():
    client = FakeClient()
    repository = FirestoreCaseRepository(client, prefix="test_")  # type: ignore[arg-type]
    original = packet()
    assert repository.submit_handoff(original) == original.handoff_id
    assert repository.get_handoff(original.handoff_id) == original
    assert "test_handoffs" in client.collections


def test_another_instance_sees_the_same_handoff():
    client = FakeClient()
    FirestoreCaseRepository(client).submit_handoff(packet())  # type: ignore[arg-type]
    assert FirestoreCaseRepository(client).get_handoff("HND-ABC") is not None  # type: ignore[arg-type]


def test_handoffs_are_listed_newest_first():
    repository = FirestoreCaseRepository(FakeClient())  # type: ignore[arg-type]
    older = packet(handoff_id="HND-OLD", created_at=packet().created_at.replace(hour=1))
    repository.submit_handoff(older)
    repository.submit_handoff(packet())
    assert [p.handoff_id for p in repository.list_handoffs()] == ["HND-ABC", "HND-OLD"]


def test_disputes_round_trip_and_unknown_ids_are_none():
    repository = FirestoreCaseRepository(FakeClient())  # type: ignore[arg-type]
    case = repository.open_dispute("CLI-1", "TX-1", "duplicate")
    assert repository.get_dispute(case.case_id) == case
    assert repository.get_dispute("DSP-NOPE") is None
    assert repository.get_handoff("HND-NOPE") is None
