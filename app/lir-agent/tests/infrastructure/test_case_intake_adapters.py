import json

import pytest

from lir_agent.application.ports import CasePublishError
from lir_agent.infrastructure.cases_inbox import GcsCaseInbox, LocalCaseInbox
from lir_agent.infrastructure.publishing import PubSubCasePublisher

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


class FakeFuture:
    def __init__(self, error: Exception | None) -> None:
        self._error = error

    def result(self, timeout: float | None = None) -> str:
        if self._error is not None:
            raise self._error
        return "message-id"


class FakePublisherClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.published: list[tuple[str, bytes, str, dict[str, str]]] = []
        self.resumed: list[tuple[str, str]] = []
        self._error = error

    def topic_path(self, project: str, topic: str) -> str:
        return f"projects/{project}/topics/{topic}"

    def publish(self, topic: str, data: bytes, ordering_key: str = "", **attributes):
        self.published.append((topic, data, ordering_key, attributes))
        return FakeFuture(self._error)

    def resume_publish(self, topic: str, ordering_key: str) -> None:
        self.resumed.append((topic, ordering_key))


def test_pubsub_publisher_sends_the_payload_with_attributes_and_ordering_key():
    client = FakePublisherClient()

    PubSubCasePublisher("lir", "lir-cases", client=client).publish(
        PAYLOAD, ATTRIBUTES, "CLI-DEMO-001"
    )

    [(topic, data, ordering_key, attributes)] = client.published
    assert topic == "projects/lir/topics/lir-cases"
    assert json.loads(data) == PAYLOAD
    assert ordering_key == "CLI-DEMO-001"
    assert attributes == ATTRIBUTES


def test_a_failed_publish_raises_and_resumes_the_ordering_key():
    client = FakePublisherClient(error=RuntimeError("deadline exceeded"))
    publisher = PubSubCasePublisher("lir", "lir-cases", client=client)

    with pytest.raises(CasePublishError):
        publisher.publish(PAYLOAD, ATTRIBUTES, "CLI-DEMO-001")

    assert client.resumed == [("projects/lir/topics/lir-cases", "CLI-DEMO-001")]
