import base64
import copy
import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from lir_agent.application.ports import CasePublishError
from lir_agent.container import build_container
from lir_agent.domain.case_intake import CaseStart
from lir_agent.infrastructure.audit import InMemoryAuditSink
from lir_agent.infrastructure.case_store import InMemoryCaseStore
from lir_agent.interface.http import create_app

CASE_ID = "6f1c2d3e-4a5b-4c6d-8e7f-0123456789ab"
CUSTOMER = "CLI-DEMO-001"
BOT = "lir_test_bot"
PAYLOAD = {
    "schema_version": "1.1",
    "case_id": CASE_ID,
    "submitted_at": "2026-06-17T15:04:05.000Z",
    "language": "es",
    "channel": "web",
    "customer": {
        "customer_id": CUSTOMER,
        "country": "MX",
        "preferred_contact": {"channel": "telegram", "value": "@lir_customer"},
    },
    "category": "unrecognized_charge",
    "intent_hint": "cargo_no_reconocido",
    "fraud_suspected": True,
    "priority_hint": "high",
    "transactions": [
        {
            "transaction_id": "TXN-D1-001",
            "amount": 179.0,
            "currency": "MXN",
            "merchant": "SPOTIFY P1A2B3",
            "occurred_at": "2026-03-14T09:12:00Z",
        }
    ],
    "cards": [{"last4": "1234", "type": "credit"}],
    "incident": {
        "occurred_at": None,
        "location": None,
        "card_in_possession": "yes",
        "shared_credentials": "no",
    },
    "freeze_card_requested": False,
    "description": "No reconozco este cargo de Spotify en mi tarjeta.",
    "consent": True,
}


def userinfo(claims: dict, padded: bool = False) -> str:
    """The API Gateway header: base64url JSON of the JWT payload."""
    raw = json.dumps(claims, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).decode()
    return encoded if padded else encoded.rstrip("=")


def headers(customer: str = CUSTOMER, key: str = CASE_ID) -> dict[str, str]:
    return {
        "X-Apigateway-Api-Userinfo": userinfo({"sub": customer}),
        "Idempotency-Key": key,
    }


def case(**changes) -> dict:
    payload = copy.deepcopy(PAYLOAD)
    payload.update(changes)
    return payload


class RecordingInbox:
    def __init__(self) -> None:
        self.puts: list[tuple[str, dict, dict[str, str]]] = []

    def put(self, case_id: str, payload: dict, attributes: dict[str, str]) -> None:
        self.puts.append((case_id, payload, attributes))


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[dict, dict[str, str], str]] = []
        self.fail = False

    def publish(
        self, payload: dict, attributes: dict[str, str], ordering_key: str
    ) -> None:
        if self.fail:
            raise CasePublishError("lir-cases unavailable")
        self.published.append((payload, attributes, ordering_key))


class NoConversations:
    async def start(self, *args, **kwargs):
        raise AssertionError("not used")

    async def send(self, *args, **kwargs):
        raise AssertionError("not used")

    async def converse(self, *args, **kwargs):
        raise AssertionError("not used")


class Intake:
    """The app with recording adapters, plus handles to inspect them."""

    def __init__(self, settings) -> None:
        self.inbox = RecordingInbox()
        self.store = InMemoryCaseStore()
        self.audit = InMemoryAuditSink()
        self.publisher = RecordingPublisher()
        container = build_container(
            settings,
            audit=self.audit,
            case_inbox=self.inbox,
            case_store=self.store,
            case_publisher=self.publisher,
        )
        app = create_app(settings, conversations=NoConversations(), container=container)
        self.client = TestClient(app)

    def post(self, payload: dict, request_headers: dict[str, str] | None = None):
        return self.client.post(
            "/v1/cases",
            json=payload,
            headers=headers() if request_headers is None else request_headers,
        )


@pytest.fixture
def intake(settings) -> Intake:
    return Intake(settings.model_copy(update={"telegram_bot_username": BOT}))


def start_token(url: str) -> str:
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "t.me", f"/{BOT}")
    return parse_qs(parts.query)["start"][0]


def test_telegram_case_is_accepted_with_a_start_link(intake):
    response = intake.post(case())

    assert response.status_code == 202
    body = response.json()
    assert body["case_id"] == CASE_ID
    assert body["folio"] == "LB-2026-6F1C2D"
    assert body["status"] == "received"
    token = start_token(body["telegram_start_url"])
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", token)
    start = intake.store.consume_start_token(token, datetime.now(UTC))
    assert start == CaseStart(CASE_ID, "LB-2026-6F1C2D", "es")


ATTRIBUTES = {
    "category": "unrecognized_charge",
    "intent_hint": "cargo_no_reconocido",
    "fraud_suspected": "true",
    "priority_hint": "high",
    "country": "MX",
    "language": "es",
    "schema_version": "1.1",
}


def test_accepted_case_is_stored_with_its_attributes(intake):
    intake.post(case())

    assert intake.inbox.puts == [(CASE_ID, PAYLOAD, ATTRIBUTES)]


def test_accepted_case_is_published_in_order_per_customer(intake):
    intake.post(case())

    assert intake.publisher.published == [(PAYLOAD, ATTRIBUTES, CUSTOMER)]


def test_a_failed_publish_is_unavailable_and_retried_with_the_same_key(intake):
    intake.publisher.fail = True

    response = intake.post(case())

    assert response.status_code == 503
    assert intake.store.get_receipt(CASE_ID) is None
    assert "case_received" not in intake.audit.events()

    intake.publisher.fail = False
    assert intake.post(case()).status_code == 202
    assert len(intake.publisher.published) == 1


def test_accepted_case_is_audited_without_the_token(intake):
    body = intake.post(case()).json()

    entry = next(e for e in intake.audit.entries if e["event"] == "case_received")
    assert entry["case_id"] == CASE_ID
    assert entry["category"] == "unrecognized_charge"
    assert entry["customer_id"] == CUSTOMER
    token = start_token(body["telegram_start_url"])
    assert token not in json.dumps(intake.audit.entries)


def test_other_channels_get_no_start_link(intake):
    whatsapp = case(
        customer={
            "customer_id": CUSTOMER,
            "country": "MX",
            "preferred_contact": {"channel": "whatsapp", "value": "+52 33 1234 5678"},
        }
    )
    response = intake.post(whatsapp)

    assert response.status_code == 202
    assert response.json()["telegram_start_url"] is None


def test_a_charge_without_a_merchant_is_accepted(intake):
    transfer = {**PAYLOAD["transactions"][0], "merchant": None}
    response = intake.post(case(transactions=[transfer]))

    assert response.status_code == 202
    assert intake.inbox.puts[0][1]["transactions"][0]["merchant"] is None


def test_an_empty_merchant_is_still_invalid(intake):
    blank = {**PAYLOAD["transactions"][0], "merchant": ""}
    response = intake.post(case(transactions=[blank]))

    assert response.status_code == 422
    assert "transaction_ids" in response.json()["errors"]


def test_no_start_link_without_a_configured_bot(settings):
    response = Intake(settings).post(case())

    assert response.status_code == 202
    assert response.json()["telegram_start_url"] is None


def test_a_repeated_key_replays_the_original_response(intake):
    first = intake.post(case())
    second = intake.post(case())

    assert second.status_code == 202
    assert second.json() == first.json()
    assert len(intake.inbox.puts) == 1
    assert len(intake.publisher.published) == 1


def test_a_key_still_being_accepted_is_a_conflict(intake):
    now = datetime.now(UTC)
    intake.store.claim_key(CASE_ID, now, now + timedelta(minutes=5))

    response = intake.post(case())

    assert response.status_code == 409
    assert intake.inbox.puts == []
    assert intake.publisher.published == []


def test_a_failed_attempt_is_not_replayed(intake):
    assert intake.post(case(description="short")).status_code == 422

    assert intake.post(case()).status_code == 202


def test_a_key_replayed_by_another_customer_is_forbidden(intake):
    intake.post(case())

    response = intake.post(case(), headers("CLI-DEMO-002"))

    assert response.status_code == 403
    assert len(intake.inbox.puts) == 1


@pytest.mark.parametrize(
    "request_headers",
    [
        headers(key="00000000-0000-4000-8000-000000000000"),
        {"X-Apigateway-Api-Userinfo": userinfo({"sub": CUSTOMER})},
    ],
    ids=["other-key", "no-key"],
)
def test_the_idempotency_key_must_be_the_case_id(intake, request_headers):
    response = intake.post(case(), request_headers)

    assert response.status_code == 400
    assert intake.inbox.puts == []


def test_another_customers_case_is_forbidden(intake):
    response = intake.post(case(), headers("CLI-DEMO-002"))

    assert response.status_code == 403
    assert intake.inbox.puts == []


def test_unknown_customer_is_not_found(intake):
    payload = case()
    payload["customer"]["customer_id"] = "CLI-UNKNOWN"

    response = intake.post(payload, headers("CLI-UNKNOWN"))

    assert response.status_code == 404


def test_another_customers_transaction_is_unknown(intake):
    payload = case()
    payload["transactions"][0]["transaction_id"] = "TXN-D2-001"

    response = intake.post(payload)

    assert response.status_code == 422
    assert response.json() == {"errors": {"transaction_ids": "unknown"}}
    assert intake.inbox.puts == []


@pytest.mark.parametrize(
    ("changes", "errors"),
    [
        ({"description": "too short"}, {"description": "too_short"}),
        ({"description": "x" * 1001}, {"description": "too_long"}),
        ({"category": "lottery"}, {"category": "invalid"}),
        ({"consent": False}, {"declaration": "invalid"}),
        ({"transactions": []}, {"transaction_ids": "required"}),
    ],
)
def test_schema_errors_name_the_form_field(intake, changes, errors):
    response = intake.post(case(**changes))

    assert response.status_code == 422
    assert response.json() == {"errors": errors}
    assert intake.inbox.puts == []


def test_missing_fields_are_required(intake):
    payload = case()
    del payload["description"]
    del payload["customer"]["preferred_contact"]["value"]

    response = intake.post(payload)

    assert response.status_code == 422
    assert response.json() == {
        "errors": {"description": "required", "contact_value": "required"}
    }


@pytest.mark.parametrize(
    "payload",
    [case(intent_hint="otra_queja"), case(schema_version="1.0"), ["not", "a", "case"]],
)
def test_errors_without_a_form_field_are_a_plain_bad_request(intake, payload):
    response = intake.post(payload)

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid case"}


def test_a_body_that_is_not_json_is_a_bad_request(intake):
    response = intake.client.post("/v1/cases", content=b"{not json", headers=headers())

    assert response.status_code == 400


@pytest.mark.parametrize("padded", [True, False])
def test_userinfo_is_decoded_with_or_without_padding(intake, padded):
    encoded = userinfo({"sub": CUSTOMER, "iss": "gateway"}, padded=padded)
    assert encoded.endswith("=") == padded

    response = intake.post(
        case(), {"X-Apigateway-Api-Userinfo": encoded, "Idempotency-Key": CASE_ID}
    )

    assert response.status_code == 202


def test_the_customer_claim_is_configurable(settings):
    intake = Intake(settings.model_copy(update={"customer_claim": "customer_id"}))
    encoded = userinfo({"sub": "someone-else", "customer_id": CUSTOMER})

    response = intake.post(
        case(), {"X-Apigateway-Api-Userinfo": encoded, "Idempotency-Key": CASE_ID}
    )

    assert response.status_code == 202


@pytest.mark.parametrize(
    "identity",
    [None, "%%%not-base64%%%", userinfo({"email": "x@example.com"})],
    ids=["missing", "undecodable", "no-claim"],
)
def test_requests_without_a_customer_identity_are_rejected(intake, identity):
    request_headers = {"Idempotency-Key": CASE_ID}
    if identity is not None:
        request_headers["X-Apigateway-Api-Userinfo"] = identity

    response = intake.post(case(), request_headers)

    assert response.status_code == 401
    assert intake.inbox.puts == []


def test_local_runs_trust_the_payload_customer(settings):
    intake = Intake(settings.model_copy(update={"require_identity": False}))

    response = intake.post(case(), {"Idempotency-Key": CASE_ID})

    assert response.status_code == 202


PREFLIGHT = {
    "Origin": "http://localhost:5500",
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type,idempotency-key,authorization",
}


def test_cors_preflight_is_answered_for_configured_origins(settings) -> None:
    intake = Intake(
        settings.model_copy(update={"cors_origins": "http://localhost:5500"})
    )

    response = intake.client.options("/v1/cases", headers=PREFLIGHT)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5500"
    allowed = response.headers["access-control-allow-headers"].lower()
    assert {"content-type", "idempotency-key", "authorization"} <= {
        h.strip() for h in allowed.split(",")
    }


def test_cors_is_off_by_default(settings) -> None:
    response = Intake(settings).client.options("/v1/cases", headers=PREFLIGHT)

    assert "access-control-allow-origin" not in response.headers


def test_my_transactions_come_from_the_signed_in_customer(settings):
    from fastapi.testclient import TestClient

    from lir_agent.interface.http import create_app

    client = TestClient(create_app(settings, conversations=NoConversations()))
    signed_in = {"X-Apigateway-Api-Userinfo": userinfo({"sub": "CLI-DEMO-001"})}
    body = client.get("/v1/me/transactions?limit=3", headers=signed_in).json()
    assert body["customer_id"] == "CLI-DEMO-001"
    assert 0 < len(body["transactions"]) <= 3
    assert {"transaction_id", "occurred_at", "merchant", "amount"} <= set(body["transactions"][0])
    assert "fraud_score" not in body["transactions"][0]


def test_my_transactions_need_the_bank_sign_in(settings):
    from fastapi.testclient import TestClient

    from lir_agent.interface.http import create_app

    local = settings.model_copy(update={"require_identity": False})
    client = TestClient(create_app(local, conversations=NoConversations()))
    assert client.get("/v1/me/transactions").status_code == 401
    unknown = {"X-Apigateway-Api-Userinfo": userinfo({"sub": "CLI-NOPE"})}
    assert client.get("/v1/me/transactions", headers=unknown).status_code == 404
    signed_in = {"X-Apigateway-Api-Userinfo": userinfo({"sub": "CLI-DEMO-001"})}
    assert client.get("/v1/me/transactions?limit=0", headers=signed_in).status_code == 422
