"""The Telegram approval surface: a customer approves or rejects with buttons in their chat."""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from lir_agent.application.use_cases import RequestApproval
from lir_agent.domain.approvals import (
    Actor,
    ApprovalDetail,
    ApprovalDraft,
    ApprovalStatus,
    Approver,
)
from lir_agent.domain.case_intake import CaseReport
from lir_agent.domain.models import Lane, Outcome
from lir_agent.domain.session import SessionState
from lir_agent.domain.telegram import InlineButton, approval_callback
from lir_agent.infrastructure.messaging import TelegramBotMessenger
from tests.interface.test_cases_api import CASE_ID, CUSTOMER
from tests.interface.test_telegram_webhook import CHAT, Bot, telegram_on

OTHER_CHAT = 9009


@pytest.fixture
def bot(settings) -> Bot:
    return Bot(telegram_on(settings))


def request_for(
    bot: Bot, case_id: str = CASE_ID, approvers: tuple[Approver, ...] = (Approver.CUSTOMER,)
):
    """A pending dispute for the case's customer, as the agent's tool guard creates it."""
    state: dict = {}
    session = SessionState(state)
    session.start(CUSTOMER, timedelta(minutes=15), "case_intake")
    session.case_report = CaseReport(
        category="unrecognized_charge",
        fraud_suspected=False,
        freeze_card_requested=False,
        case_id=case_id,
    )
    return RequestApproval(bot.container.approvals, bot.audit, timedelta(minutes=60)).execute(
        session,
        action="open_dispute",
        draft=ApprovalDraft(
            params={"transaction_id": "TXN-D1-006", "reason": "Cobro duplicado"},
            title="Abrir una disputa por este cargo",
            details=[
                ApprovalDetail(label="Comercio", value="OXXO LAS AGUILAS"),
                ApprovalDetail(label="Monto", value="245.50 MXN"),
            ],
            outcome=Outcome(lane=Lane.DISPUTE, rule_id="C7", reason="r", policy_version="v"),
        ),
        approvers=list(approvers),
        language="es",
    )


def linked(bot: Bot) -> Bot:
    bot.say(f"/start {bot.issue()}")
    bot.messenger.sent.clear()
    return bot


def present(bot: Bot, approval_id: str) -> None:
    asyncio.run(bot.container.present_approvals.execute([approval_id]))


def test_the_request_shows_in_the_chat_with_its_buttons(bot):
    request = request_for(linked(bot))

    present(bot, request.approval_id)

    [(chat, text)] = bot.messenger.sent
    assert chat == CHAT
    assert "Abrir una disputa por este cargo" in text
    assert "• Comercio: OXXO LAS AGUILAS" in text
    [(_, rows)] = bot.messenger.buttons
    assert [b.callback_data for b in rows[0]] == [
        approval_callback(request.approval_id, True),
        approval_callback(request.approval_id, False),
    ]


def test_approving_with_the_button_opens_the_dispute_and_says_so(bot):
    request = request_for(linked(bot))
    present(bot, request.approval_id)

    assert bot.press(approval_callback(request.approval_id, True)).status_code == 200

    decided = bot.container.approvals.get(request.approval_id)
    assert decided is not None and decided.status == "approved"
    assert (decided.decided_by, decided.channel) == (CUSTOMER, "telegram")
    dispute_id = (decided.result or {})["dispute_case_id"]
    assert bot.container.cases.get_dispute(dispute_id) is not None
    assert bot.messenger.answers == [("cb-1", "Aprobaste la solicitud.", False)]
    assert bot.messenger.sent[-1] == (
        CHAT,
        f"Aprobaste la disputa: quedó abierta con el número {dispute_id}.",
    )


SPECIALIST = Actor(role=Approver.SPECIALIST, identity="ana@bank", channel="backoffice")


def approved_by_the_customer_then_waiting_for_a_specialist(bot: Bot):
    request = request_for(linked(bot), approvers=(Approver.CUSTOMER, Approver.SPECIALIST))
    present(bot, request.approval_id)
    bot.press(approval_callback(request.approval_id, True))
    assert bot.messenger.sent[-1] == (
        CHAT,
        "Recibimos tu aprobación. Un especialista del banco la revisará y te avisaremos por aquí.",
    )
    [review] = bot.container.approvals.list(ApprovalStatus.PENDING, Approver.SPECIALIST)
    return review


def test_the_specialists_approval_opens_the_dispute_and_tells_the_customer(bot):
    review = approved_by_the_customer_then_waiting_for_a_specialist(bot)

    decided = asyncio.run(
        bot.container.decide_approval.execute(
            review.approval_id, SPECIALIST, True, review.content_hash
        )
    )

    dispute_id = (decided.result or {})["dispute_case_id"]
    assert bot.container.cases.get_dispute(dispute_id) is not None
    assert bot.messenger.sent[-1] == (
        CHAT,
        f"Un especialista del banco aprobó tu disputa: quedó abierta con el número {dispute_id}.",
    )


def test_the_specialists_rejection_opens_nothing_and_tells_the_customer(bot):
    review = approved_by_the_customer_then_waiting_for_a_specialist(bot)

    decided = asyncio.run(
        bot.container.decide_approval.execute(review.approval_id, SPECIALIST, False)
    )

    assert (decided.status, decided.result) == ("rejected", None)
    assert bot.messenger.sent[-1] == (
        CHAT,
        "Un especialista del banco revisó tu caso y no aprobó la solicitud. "
        "No se hizo ningún cambio.",
    )


def test_rejecting_opens_nothing(bot):
    request = request_for(linked(bot))
    present(bot, request.approval_id)

    bot.press(approval_callback(request.approval_id, False))

    decided = bot.container.approvals.get(request.approval_id)
    assert decided is not None and (decided.status, decided.result) == ("rejected", None)
    assert bot.messenger.sent[-1] == (CHAT, "Rechazaste la solicitud: no se hizo nada.")


def test_a_second_press_is_refused(bot):
    request = request_for(linked(bot))
    present(bot, request.approval_id)
    bot.press(approval_callback(request.approval_id, True))

    bot.press(approval_callback(request.approval_id, False), callback_id="cb-2")

    assert bot.messenger.answers[-1] == ("cb-2", "Esta solicitud ya fue decidida.", True)
    decided = bot.container.approvals.get(request.approval_id)
    assert decided is not None and decided.status == "approved"


def test_a_chat_not_linked_to_the_case_cannot_decide(bot):
    request = request_for(linked(bot))

    bot.press(approval_callback(request.approval_id, True), chat_id=OTHER_CHAT)

    pending = bot.container.approvals.get(request.approval_id)
    assert pending is not None and pending.status == "pending"
    assert bot.messenger.answers[-1][2] is True  # an alert, nothing decided


def test_a_request_made_before_the_chat_was_linked_shows_on_start(bot):
    request = request_for(bot)
    present(bot, request.approval_id)
    assert bot.messenger.buttons == []  # no chat yet

    bot.say(f"/start {bot.issue()}")

    [(chat, rows)] = bot.messenger.buttons
    assert chat == CHAT
    assert rows[0][0].callback_data == approval_callback(request.approval_id, True)


def test_the_bot_api_receives_inline_buttons():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"path": request.url.path, "json": json.loads(request.content)})
        return httpx.Response(200, json={"ok": True})

    messenger = TelegramBotMessenger(
        "123:abc", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    rows = [[InlineButton("Aprobar", "apr:APR-1:a")], [InlineButton("Ver", url="https://x")]]

    asyncio.run(messenger.send_buttons(7, "hola", rows))
    asyncio.run(messenger.send_buttons(7, "listo", []))
    asyncio.run(messenger.answer_callback("cb", "ok"))

    assert calls[0]["path"].endswith("/sendMessage")
    assert calls[0]["json"]["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "Aprobar", "callback_data": "apr:APR-1:a"}],
            [{"text": "Ver", "url": "https://x"}],
        ]
    }
    assert "reply_markup" not in calls[1]["json"]
    assert calls[2]["path"].endswith("/answerCallbackQuery")


# ---- step-up: the chat alone cannot approve ------------------------------------------------
@pytest.fixture
def stepped_bot(settings) -> Bot:
    return Bot(
        telegram_on(
            settings,
            approval_requires_sign_in=True,
            approval_link_template="https://web.example/aprobar.html?id={approval_id}&t={token}",
        )
    )


def test_with_step_up_the_chat_gets_a_link_to_sign_in_not_buttons(stepped_bot):
    request = request_for(linked(stepped_bot))

    present(stepped_bot, request.approval_id)

    [(_, rows)] = stepped_bot.messenger.buttons
    [[button]] = rows
    assert button.callback_data is None
    assert button.url and button.url.startswith("https://web.example/aprobar.html?id=")
    assert "con tu sesión iniciada" in stepped_bot.messenger.sent[-1][1]


def test_with_step_up_an_old_button_cannot_approve(stepped_bot):
    request = request_for(linked(stepped_bot))

    stepped_bot.press(approval_callback(request.approval_id, True))

    pending = stepped_bot.container.approvals.get(request.approval_id)
    assert pending is not None and pending.status == "pending"
    assert stepped_bot.messenger.answers[-1] == (
        "cb-1",
        "Por tu seguridad, decide desde el enlace, con tu sesión del banco.",
        True,
    )
