"""Jev (TypeSafe AI) client through Cloudflare Workers AI.

Endpoint and payload: https://developers.cloudflare.com/ai/models/typesafe/jev/
Jev is billed from the AI Gateway balance (not the Workers AI free allocation):
without balance Cloudflare answers 402, raised as `JevBillingError`.
"""

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from decision_layer.base import Answer, Choice, DecisionError, DecisionResult, Noul, Question

MODEL_ID = "typesafe/jev"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# (url, headers, body, timeout_s) -> (status, body_bytes)
type Transport = Callable[[str, Mapping[str, str], bytes, float], tuple[int, bytes]]


class JevBillingError(DecisionError):
    """402: no AI Gateway balance. Not retried."""


class JevAuthError(DecisionError):
    """401/403: invalid token or missing Workers AI permission. Not retried."""


def urllib_transport(
    url: str, headers: Mapping[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method="POST", headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@dataclass
class JevClient:
    account_id: str
    api_token: str
    timeout_s: float = 5.0
    max_attempts: int = 3  # bounded retries
    backoff_s: float = 0.5
    transport: Transport = urllib_transport
    name: str = "jev"

    @property
    def url(self) -> str:
        return f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run"

    def decide(self, state: str, questions: Mapping[str, Question]) -> DecisionResult:
        body = json.dumps(
            {"model": MODEL_ID, "input": {"state": state, "questions": to_wire(questions)}}
        ).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        last_error = ""
        t0 = time.perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            try:
                status, raw = self.transport(self.url, headers, body, self.timeout_s)
            except OSError as e:  # URLError and TimeoutError are OSError subclasses
                status, raw, last_error = None, b"", f"network: {e}"
            if status == 200:
                latency_ms = (time.perf_counter() - t0) * 1000
                return parse_response(json.loads(raw), questions, latency_ms)
            if status == 402:
                raise JevBillingError(error_message(raw))
            if status in (401, 403):
                raise JevAuthError(error_message(raw))
            if status is not None and status not in RETRYABLE_STATUS:
                raise DecisionError(f"HTTP {status}: {error_message(raw)}")
            if status is not None:
                last_error = f"HTTP {status}: {error_message(raw)}"
            if attempt < self.max_attempts:
                time.sleep(self.backoff_s * 2 ** (attempt - 1))
        raise DecisionError(f"Jev failed after {self.max_attempts} attempts ({last_error})")


def to_wire(questions: Mapping[str, Question]) -> dict:
    wire = {}
    for key, q in questions.items():
        match q:
            case Noul():
                wire[key] = {
                    "type": "noul",
                    "instructions": q.instructions,
                    "criteria": {"true": q.true, "false": q.false},
                }
            case Choice():
                wire[key] = {
                    "type": "choice",
                    "instructions": q.instructions,
                    "criteria": dict(q.options),
                }
            case _:
                raise TypeError(f"Unsupported question type: {type(q).__name__}")
    return wire


def parse_response(
    payload: dict, questions: Mapping[str, Question], latency_ms: float
) -> DecisionResult:
    # Cloudflare usually wraps the body in {"result": ...}; Jev's docs show it unwrapped.
    res = payload.get("result", payload)
    answers_raw = res.get("answers", {})
    missing = set(questions) - set(answers_raw)
    if missing:
        raise DecisionError(f"Response is missing: {sorted(missing)}")

    answers = {}
    for key, q in questions.items():
        a = answers_raw[key]
        if isinstance(q, Noul):
            p = float(a["noul"])
            answers[key] = Answer(value=p >= 0.5, probability=p)
        else:
            probs = {k: float(v) for k, v in (a.get("probabilities") or {}).items()}
            choice = a["choice"]
            if choice not in q.options:
                raise DecisionError(f"{key}: unknown option {choice!r}")
            p = probs.get(choice, float(a.get("confidence", 0.0)))
            answers[key] = Answer(value=choice, probability=p, probabilities=probs)

    usage = res.get("usage") or {}
    return DecisionResult(
        answers=answers,
        model=res.get("model", MODEL_ID),
        latency_ms=latency_ms,
        input_tokens=usage.get("input_tokens"),
    )


def error_message(raw: bytes) -> str:
    try:
        errors = json.loads(raw).get("errors") or []
        message = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errors)
        return message or raw[:200].decode(errors="replace")
    except (ValueError, AttributeError):
        return raw[:200].decode(errors="replace")
