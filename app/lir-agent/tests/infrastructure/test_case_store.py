"""CaseStore contract: the same behavior from every adapter.

The Firestore cases run against the emulator only (`scripts/firestore-emulator.sh up`, then
`FIRESTORE_EMULATOR_HOST=localhost:8086 GOOGLE_CLOUD_PROJECT=lir-local`); each test writes
under its own collection prefix, so runs never see each other's documents.
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from lir_agent.application.ports import CaseStore, StoredReceipt
from lir_agent.config.settings import Settings
from lir_agent.container import build_case_store
from lir_agent.domain.case_intake import CaseConversation, CaseReceipt, CaseStart
from lir_agent.domain.telegram import ChatLink
from lir_agent.infrastructure.case_store import FirestoreCaseStore, InMemoryCaseStore

CASE_ID = "6f1c2d3e-4a5b-4c6d-8e7f-0123456789ab"
FOLIO = "LB-2026-6F1C2D"
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)
START = CaseStart(CASE_ID, FOLIO, "es")

EMULATOR = os.environ.get("FIRESTORE_EMULATOR_HOST")
needs_emulator = pytest.mark.skipif(
    not EMULATOR, reason="FIRESTORE_EMULATOR_HOST is not set (Firestore emulator off)"
)


def _firestore_client():
    from google.cloud import firestore

    return firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "lir-local"))


@pytest.fixture
def prefix() -> str:
    return f"test_{uuid.uuid4().hex[:12]}_"


@pytest.fixture(
    params=[
        pytest.param("memory"),
        pytest.param("firestore", marks=needs_emulator),
    ]
)
def store(request, prefix) -> CaseStore:
    if request.param == "memory":
        return InMemoryCaseStore()
    return FirestoreCaseStore(_firestore_client(), prefix=prefix)


def test_receipts_round_trip_by_idempotency_key(store):
    stored = StoredReceipt(
        "CLI-DEMO-001",
        CaseReceipt(CASE_ID, FOLIO, "https://t.me/lir_bot?start=abc"),
    )

    assert store.get_receipt(CASE_ID) is None
    store.save_receipt(CASE_ID, stored)
    assert store.get_receipt(CASE_ID) == stored


def test_a_receipt_without_a_start_link_round_trips(store):
    stored = StoredReceipt("CLI-DEMO-001", CaseReceipt(CASE_ID, FOLIO, None))

    store.save_receipt(CASE_ID, stored)

    assert store.get_receipt(CASE_ID) == stored


def test_a_key_is_claimed_once(store):
    until = NOW + timedelta(minutes=5)

    assert store.claim_key(CASE_ID, NOW, until) is True
    assert store.claim_key(CASE_ID, NOW, until) is False
    assert store.claim_key("other", NOW, until) is True


def test_a_released_key_can_be_claimed_again(store):
    store.claim_key(CASE_ID, NOW, NOW + timedelta(minutes=5))

    store.release_key(CASE_ID)

    assert store.claim_key(CASE_ID, NOW, NOW + timedelta(minutes=5)) is True


def test_an_expired_claim_can_be_taken_over(store):
    store.claim_key(CASE_ID, NOW, NOW + timedelta(minutes=5))
    later = NOW + timedelta(minutes=5)

    assert store.claim_key(CASE_ID, later, later + timedelta(minutes=5)) is True


def test_concurrent_claims_have_one_winner(store):
    until = NOW + timedelta(minutes=5)

    with ThreadPoolExecutor(max_workers=8) as pool:
        won = list(pool.map(lambda _: store.claim_key(CASE_ID, NOW, until), range(8)))

    assert won.count(True) == 1


def test_a_start_token_is_single_use(store):
    store.add_start_token("t1", START, NOW + timedelta(minutes=5))

    assert store.consume_start_token("t1", NOW) == START
    assert store.consume_start_token("t1", NOW) is None


def test_an_expired_start_token_is_refused(store):
    store.add_start_token("t1", START, NOW + timedelta(minutes=5))

    assert store.consume_start_token("t1", NOW + timedelta(minutes=5)) is None


def test_an_unknown_start_token_is_refused(store):
    assert store.consume_start_token("unknown", NOW) is None


def test_tokens_do_not_interfere(store):
    other = CaseStart("other", "LB-2026-000000", "pt")
    store.add_start_token("t1", START, NOW + timedelta(minutes=5))
    store.add_start_token("t2", other, NOW + timedelta(minutes=5))

    assert store.consume_start_token("t2", NOW) == other
    assert store.consume_start_token("t1", NOW) == START


def test_a_chat_link_is_readable_by_chat_and_by_case(store):
    link = ChatLink(CASE_ID, FOLIO, "es")

    assert store.get_chat_link(42) is None
    assert store.get_case_chat(CASE_ID) is None
    store.link_chat(42, link)

    assert store.get_chat_link(42) == link
    assert store.get_case_chat(CASE_ID) == 42


def test_a_chat_keeps_its_latest_link(store):
    latest = ChatLink("other", "LB-2026-000000", "pt")
    store.link_chat(42, ChatLink(CASE_ID, FOLIO, "es"))
    store.link_chat(42, latest)
    store.link_chat(7, ChatLink(CASE_ID, FOLIO, "es"))

    assert store.get_chat_link(42) == latest
    assert store.get_case_chat("other") == 42
    assert store.get_case_chat(CASE_ID) == 7


def test_a_case_keeps_its_first_conversation(store):
    first = CaseConversation(CASE_ID, FOLIO, "es", f"case:{CASE_ID}", "s1")
    second = CaseConversation(CASE_ID, FOLIO, "es", f"case:{CASE_ID}", "s2")

    assert store.get_conversation(CASE_ID) is None
    assert store.add_conversation(first) is True
    assert store.add_conversation(second) is False
    assert store.get_conversation(CASE_ID) == first


def test_queued_replies_pop_once_in_order_with_repeats(store):
    store.queue_reply(CASE_ID, "one")
    store.queue_reply(CASE_ID, "one")
    store.queue_reply(CASE_ID, "two")
    store.queue_reply("other", "elsewhere")

    assert store.pop_replies(CASE_ID) == ["one", "one", "two"]
    assert store.pop_replies(CASE_ID) == []
    assert store.pop_replies("other") == ["elsewhere"]


def test_popping_a_case_without_replies_returns_nothing(store):
    assert store.pop_replies(CASE_ID) == []


@needs_emulator
def test_firestore_never_keeps_a_usable_start_token(prefix):
    client = _firestore_client()
    store = FirestoreCaseStore(client, prefix=prefix)

    store.add_start_token("raw-secret-token", START, NOW + timedelta(minutes=5))

    ids = [doc.id for doc in client.collection(f"{prefix}start_tokens").stream()]
    assert len(ids) == 1
    assert "raw-secret-token" not in ids
    assert store.consume_start_token("raw-secret-token", NOW) == START


def test_the_case_store_is_in_memory_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert isinstance(build_case_store(Settings()), InMemoryCaseStore)


def test_case_store_firestore_builds_the_firestore_adapter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # The emulator host makes the client skip credentials; nothing is called here.
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", EMULATOR or "localhost:8086")
    settings = Settings(case_store="firestore", google_cloud_project="lir-local")

    assert isinstance(build_case_store(settings), FirestoreCaseStore)


def test_case_store_firestore_needs_a_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        build_case_store(Settings(case_store="firestore"))
