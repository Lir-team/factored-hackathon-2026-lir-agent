import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from lir_agent.application.ports import CaseInProgressError
from lir_agent.container import build_container
from lir_agent.infrastructure.audit import InMemoryAuditSink
from lir_agent.infrastructure.case_store import InMemoryCaseStore
from tests.interface.test_cases_api import (
    CASE_ID,
    CUSTOMER,
    RecordingInbox,
    RecordingPublisher,
    case,
)


class BlockingPublisher(RecordingPublisher):
    """Holds the first publish until `release` is set, as a slow Pub/Sub would."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def publish(self, payload, attributes, ordering_key) -> None:
        if not self.entered.is_set():
            self.entered.set()
            assert self.release.wait(timeout=5)
        super().publish(payload, attributes, ordering_key)


@pytest.fixture
def publisher() -> BlockingPublisher:
    return BlockingPublisher()


@pytest.fixture
def inbox() -> RecordingInbox:
    return RecordingInbox()


@pytest.fixture
def submit(settings, inbox, publisher):
    container = build_container(
        settings,
        audit=InMemoryAuditSink(),
        case_inbox=inbox,
        case_store=InMemoryCaseStore(),
        case_publisher=publisher,
    )
    return container.submit_case


def test_a_concurrent_duplicate_is_refused_and_the_case_published_once(
    submit, inbox, publisher
):
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(submit.execute, CUSTOMER, CASE_ID, case())
        assert publisher.entered.wait(timeout=5)

        try:
            with pytest.raises(CaseInProgressError):
                submit.execute(CUSTOMER, CASE_ID, case())
        finally:
            publisher.release.set()
        receipt = first.result(timeout=5)

    assert len(publisher.published) == 1
    assert len(inbox.puts) == 1
    assert submit.execute(CUSTOMER, CASE_ID, case()) == receipt
    assert len(publisher.published) == 1
