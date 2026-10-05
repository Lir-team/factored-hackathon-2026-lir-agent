"""Approval requests survive a round trip through Firestore documents (fake client)."""

from lir_agent.domain.approvals import ApprovalStatus, Approver
from lir_agent.infrastructure.approvals import FirestoreApprovalRepository
from tests.infrastructure.test_firestore_case_repository import FakeSnapshot
from tests.infrastructure.test_notice_surfaces import NOW, request


class FakeQuery:
    def __init__(self, docs, filters=()):
        self._docs, self._filters = docs, filters

    def where(self, *, filter):
        return FakeQuery(self._docs, (*self._filters, filter))

    def stream(self):
        for data in self._docs.values():
            if all(data[f.field_path] == f.value for f in self._filters):
                yield FakeSnapshot(data)


class FakeCollection(FakeQuery):
    def __init__(self):
        super().__init__({})

    def document(self, key):
        collection = self

        class Document:
            def set(self, data):
                collection._docs[key] = data

            def get(self):
                return FakeSnapshot(collection._docs.get(key))

        return Document()


class FakeClient:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection())


def repository_on(client: FakeClient, prefix: str = "lir_") -> FirestoreApprovalRepository:
    return FirestoreApprovalRepository(client, prefix)  # type: ignore[arg-type]


def test_a_request_saved_by_one_service_is_decided_by_another():
    client = FakeClient()
    saved = request(approval_id="APR-1", case_id="case-1")
    repository_on(client).save(saved)

    assert repository_on(client).get("APR-1") == saved
    assert repository_on(client).get("APR-MISSING") is None
    assert set(client.collections) == {"lir_approvals"}


def test_list_filters_by_status_and_approver_newest_first():
    repository = repository_on(FakeClient(), prefix="test_")
    older = request(approval_id="APR-OLD", created_at=NOW.replace(hour=20))
    newer = request(approval_id="APR-NEW")
    customer = request(approval_id="APR-CUSTOMER", approver=Approver.CUSTOMER)
    decided = request(approval_id="APR-DONE", status=ApprovalStatus.APPROVED)
    for r in (older, newer, customer, decided):
        repository.save(r)

    pending = repository.list(ApprovalStatus.PENDING, Approver.SPECIALIST)

    assert [r.approval_id for r in pending] == ["APR-NEW", "APR-OLD"]
    assert len(repository.list()) == 4
