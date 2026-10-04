from datetime import datetime

import pytest

from lir_agent.domain.pseudonyms import (
    REDACTED,
    TEXT_KINDS,
    UNKNOWN_PLACEHOLDER,
    Kind,
    Pseudonyms,
    redact_identifiers,
)
from lir_agent.domain.session import SessionState


@pytest.fixture
def pseudonyms() -> Pseudonyms:
    return Pseudonyms(SessionState({}))


def test_one_placeholder_per_value_and_kind(pseudonyms):
    first = pseudonyms.placeholder(Kind.MERCHANT, "OXXO LAS AGUILAS")

    assert first == "[[COMERCIO_1]]"
    assert pseudonyms.placeholder(Kind.MERCHANT, "OXXO LAS AGUILAS") == first
    assert pseudonyms.placeholder(Kind.MERCHANT, "SPOTIFY") == "[[COMERCIO_2]]"
    assert pseudonyms.placeholder(Kind.AMOUNT, 245.5) == "[[MONTO_1]]"


def test_missing_values_stay_missing(pseudonyms):
    assert pseudonyms.placeholder(Kind.MERCHANT, None) is None
    assert pseudonyms.placeholder(Kind.MERCHANT, "") == ""


def test_values_resolve_to_what_the_customer_reads(pseudonyms):
    amount = pseudonyms.placeholder(Kind.AMOUNT, 245.5)
    day = pseudonyms.placeholder(Kind.DATE, datetime(2026, 6, 10, 19, 43))

    assert pseudonyms.reveal(f"{amount} MXN el {day}") == ("245.50 MXN el 10/06/2026", [])


def test_a_placeholder_never_issued_is_reported_and_not_shown(pseudonyms):
    text, unknown = pseudonyms.reveal("Cargo en [[COMERCIO_9]].")

    assert text == f"Cargo en {UNKNOWN_PLACEHOLDER}."
    assert unknown == ["[[COMERCIO_9]]"]


def test_redacted_identifiers_are_not_unknown_placeholders(pseudonyms):
    assert pseudonyms.reveal(f"Tu tarjeta {REDACTED}") == (f"Tu tarjeta {REDACTED}", [])


def test_conceal_turns_known_values_back_into_placeholders(pseudonyms):
    merchant = pseudonyms.placeholder(Kind.MERCHANT, "OXXO LAS AGUILAS")
    amount = pseudonyms.placeholder(Kind.AMOUNT, 245.5)

    concealed = pseudonyms.conceal("Fue en Oxxo las Aguilas por 245.50 MXN.")

    assert concealed == f"Fue en {merchant} por {amount} MXN."


def test_conceal_prefers_the_longest_value(pseudonyms):
    eats = pseudonyms.placeholder(Kind.MERCHANT, "UBER EATS")
    uber = pseudonyms.placeholder(Kind.MERCHANT, "UBER")

    assert pseudonyms.conceal("UBER EATS y UBER") == f"{eats} y {uber}"


def test_conceal_only_matches_whole_values(pseudonyms):
    pseudonyms.placeholder(Kind.AMOUNT, 179.0)

    assert pseudonyms.conceal("Pagaste 1179.00 y 179.001") == "Pagaste 1179.00 y 179.001"


def test_customer_amounts_stay_readable_for_the_search(pseudonyms):
    merchant = pseudonyms.placeholder(Kind.MERCHANT, "OXXO LAS AGUILAS")
    pseudonyms.placeholder(Kind.AMOUNT, 245.5)

    text = pseudonyms.conceal("Me cobraron 245.50 en OXXO LAS AGUILAS", TEXT_KINDS)

    assert text == f"Me cobraron 245.50 en {merchant}"


def test_nested_tool_values_are_resolved_and_concealed(pseudonyms):
    merchant = pseudonyms.placeholder(Kind.MERCHANT, "SPOTIFY")
    args = {"summary": f"Cargo de {merchant}", "open_questions": [f"¿Reconoce {merchant}?"]}

    revealed = pseudonyms.reveal_value(args)

    assert revealed == {"summary": "Cargo de SPOTIFY", "open_questions": ["¿Reconoce SPOTIFY?"]}
    assert pseudonyms.conceal_value(revealed) == args


def test_customer_text_loses_identifiers_and_known_merchants(pseudonyms):
    merchant = pseudonyms.placeholder(Kind.MERCHANT, "SPOTIFY")

    protected = pseudonyms.protect_customer_text(
        "Mi tarjeta 4152 3138 0000 1234 tiene un cargo de SPOTIFY por 179"
    )

    assert protected == f"Mi tarjeta {REDACTED} tiene un cargo de {merchant} por 179"


@pytest.mark.parametrize(
    "identifier",
    [
        "4152313800001234",  # card
        "4152-3138-0000-1234",  # card, grouped
        "012180001234567891",  # CLABE
        "+52 33 1234 5678",  # phone
        "123.456.789-09",  # CPF
        "ana.perez@correo.com",  # e-mail
        "PEGA850101HJCRRN09",  # CURP
        "PEGA850101AB1",  # RFC
    ],
)
def test_identifiers_are_redacted(identifier):
    assert redact_identifiers(f"mi dato es {identifier}, gracias") == (
        f"mi dato es {REDACTED}, gracias",
        1,
    )


@pytest.mark.parametrize(
    "text",
    [
        "me cobraron 179 en Spotify",
        "un cargo de 1.500.000 COP",
        "fueron 245,50 MXN el 10/06/2026",
        "el martes 2026-06-10",
        "un cargo de 12000000.00 COP",
        "fueron 123456789,50 ARS",
    ],
)
def test_amounts_and_dates_are_not_identifiers(text):
    assert redact_identifiers(text) == (text, 0)
