import pytest

from lir_agent.domain.case_intake import case_summary
from lir_agent.domain.telegram import (
    bot_language,
    bot_message,
    case_owner,
    split_message,
    start_payload,
)

PAYLOAD = {
    "language": "es",
    "description": "No reconozco este cargo de Spotify en mi tarjeta.",
    "transactions": [
        {
            "transaction_id": "TXN-D1-001",
            "amount": 179.0,
            "currency": "MXN",
            "merchant": "SPOTIFY P1A2B3",
            "occurred_at": "2026-03-14T09:12:00Z",
        },
        {
            "transaction_id": "TXN-D1-002",
            "amount": 245.5,
            "currency": "MXN",
            "merchant": "OXXO",
            "occurred_at": "2026-03-15T10:00:00Z",
        },
    ],
}


def test_summary_is_the_description_and_the_charge_references():
    assert case_summary(PAYLOAD, ["T1", "T2"]) == (
        "No reconozco este cargo de Spotify en mi tarjeta.\nCargos que reporto: T1, T2."
    )


def test_summary_never_carries_the_bank_records():
    summary = case_summary(PAYLOAD, ["T1", "T2"])

    for record in ("TXN-", "P1A2B3", "OXXO", "179", "245", "2026-03-1"):
        assert record not in summary


def test_summary_without_charges_is_the_description():
    assert case_summary(PAYLOAD) == PAYLOAD["description"]


def test_portuguese_summary():
    summary = case_summary({**PAYLOAD, "language": "pt"}, ["T1"])

    assert summary.endswith("Cobranças que reporto: T1.")


@pytest.mark.parametrize(
    ("text", "payload"),
    [
        ("/start abc_DEF-123", "abc_DEF-123"),
        ("  /start   tok  ", "tok"),
        ("/start", ""),
        ("hola", None),
        ("/starter tok", None),
        ("/help", None),
    ],
)
def test_start_payload(text, payload):
    assert start_payload(text) == payload


@pytest.mark.parametrize(
    ("language", "expected"), [("es", "es"), ("pt", "pt"), ("en", "es")]
)
def test_bot_language_maps_english_to_spanish(language, expected):
    assert bot_language(language) == expected


def test_bot_messages_are_localized():
    assert "LB-2026-ABC123" in bot_message("linked", "es", folio="LB-2026-ABC123")
    assert bot_message("use_form", "es") != bot_message("use_form", "pt")


def test_case_owner():
    assert case_owner("abc") == "case:abc"


def test_split_message_keeps_short_text_whole():
    assert split_message("hola") == ["hola"]


def test_split_message_cuts_at_the_limit():
    parts = split_message("a" * 4096 + "b" * 10)

    assert parts == ["a" * 4096, "b" * 10]
