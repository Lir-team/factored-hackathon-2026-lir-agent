import json
from datetime import UTC, datetime, timedelta

from lir_agent.application.ports import StoredReceipt
from lir_agent.domain.case_intake import CaseReceipt, CaseStart
from lir_agent.domain.telegram import ChatLink
from lir_agent.infrastructure.case_store import InMemoryCaseStore
from lir_agent.infrastructure.cases_inbox import GcsCaseInbox, LocalCaseInbox

CASE_ID = "6f1c2d3e-4a5b-4c6d-8e7f-0123456789ab"
PAYLOAD = {"case_id": CASE_ID, "description": "No reconozco el cargo en Bogotá"}
ATTRIBUTES = {"category": "unrecognized_charge", "fraud_suspected": "true"}


class FakeBlob:
    def __init__(self, name: str) -> None:
        self.name = name
        self.metadata: dict[str, str] | None = None
        self.uploads: list[tuple[str, str]] = []

    def upload_from_string(self, data: str, content_type: str) -> None:
        self.uploads.append((data, content_type))


class FakeBucket:
    def __init__(self) -> None:
        self.blobs: list[FakeBlob] = []

    def blob(self, name: str) -> FakeBlob:
        self.blobs.append(FakeBlob(name))
        return self.blobs[-1]


class FakeClient:
    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket())


def test_gcs_inbox_writes_the_case_with_its_attributes_as_metadata():
    client = FakeClient()

    GcsCaseInbox("cases-inbox", client=client).put(CASE_ID, PAYLOAD, ATTRIBUTES)

    [blob] = client.buckets["cases-inbox"].blobs
    assert blob.name == f"cases/{CASE_ID}.json"
    assert blob.metadata == ATTRIBUTES
    [(data, content_type)] = blob.uploads
    assert json.loads(data) == PAYLOAD
    assert content_type == "application/json"


def test_local_inbox_writes_the_case_and_its_attributes(tmp_path):
    LocalCaseInbox(tmp_path / "inbox").put(CASE_ID, PAYLOAD, ATTRIBUTES)

    cases = tmp_path / "inbox" / "cases"
    case_file = cases / f"{CASE_ID}.json"
    attributes_file = cases / f"{CASE_ID}.attributes.json"
    assert json.loads(case_file.read_text(encoding="utf-8")) == PAYLOAD
    assert json.loads(attributes_file.read_text(encoding="utf-8")) == ATTRIBUTES


def test_store_keeps_receipts_by_idempotency_key():
    store = InMemoryCaseStore()
    stored = StoredReceipt("CLI-DEMO-001", CaseReceipt(CASE_ID, "LB-2026-6F1C2D", None))

    assert store.get_receipt(CASE_ID) is None
    store.save_receipt(CASE_ID, stored)
    assert store.get_receipt(CASE_ID) == stored


def test_start_tokens_are_single_use_and_expire():
    store = InMemoryCaseStore()
    now = datetime(2026, 10, 4, tzinfo=UTC)
    start = CaseStart(CASE_ID, "CLI-DEMO-001", "LB-2026-6F1C2D", "es", "summary")
    store.add_start_token("t1", start, now + timedelta(minutes=5))
    store.add_start_token("t2", start, now + timedelta(minutes=5))

    assert store.consume_start_token("t1", now) == start
    assert store.consume_start_token("t1", now) is None
    assert store.consume_start_token("t2", now + timedelta(minutes=5)) is None
    assert store.consume_start_token("unknown", now) is None


def test_a_chat_keeps_its_latest_link():
    store = InMemoryCaseStore()
    first = ChatLink(CASE_ID, "LB-2026-6F1C2D", f"case:{CASE_ID}", "s1", "es")
    latest = ChatLink("other", "LB-2026-000000", "case:other", "s2", "pt")

    assert store.get_chat_link(42) is None
    store.link_chat(42, first)
    store.link_chat(42, latest)
    assert store.get_chat_link(42) == latest
