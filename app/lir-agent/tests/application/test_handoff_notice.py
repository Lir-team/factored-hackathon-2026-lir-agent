from datetime import timedelta

from lir_agent.application.use_cases import RequestHandoff
from lir_agent.domain.session import SessionState
from lir_agent.infrastructure.cases import InMemoryCaseRepository
from lir_agent.infrastructure.resources import ResourceLoader


class RecordingNotifier:
    def __init__(self, fail=False):
        self.packets, self.fail = [], fail

    def notify(self, packet):
        if self.fail:
            raise RuntimeError("slack down")
        self.packets.append(packet)


def handoff(settings, notifier):
    policy = ResourceLoader().load_policy(settings.policy_path).config
    state = {}
    SessionState(state).start("CLI-DEMO-001", timedelta(minutes=5), "test")
    return RequestHandoff(InMemoryCaseRepository(), policy, notifier).execute(SessionState(state))


def test_a_stored_handoff_is_announced(settings):
    notifier = RecordingNotifier()
    result = handoff(settings, notifier)
    assert result["status"] == "submitted"
    assert [p.handoff_id for p in notifier.packets] == [result["handoff_id"]]


def test_a_failed_notice_never_undoes_the_handoff(settings):
    assert handoff(settings, RecordingNotifier(fail=True))["status"] == "submitted"
