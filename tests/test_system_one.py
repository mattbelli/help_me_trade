"""TypeSafe System One seam (ai/llm/system_one.py) — builders, guards, error mapping.

Offline only: the seam passes an httpx2 MockTransport into the vendor SDK, so no
test touches the network. Retry policies use zero backoff so exhaustion is instant.
The real key lives in data/settings.json, so tests always pass an explicit api_key.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx2
import pytest
from typesafe_sdk import RetryPolicy

from ai.llm import system_one as so

Handler = Callable[[httpx2.Request], httpx2.Response]


def _client(handler: Handler, **kwargs: Any) -> so.SystemOneClient:
    """A seam client whose SDK transport is a MockTransport, with instant retries."""
    kwargs.setdefault(
        "retry_policy", RetryPolicy(max_retries=0, backoff_initial=0, backoff_max=0)
    )
    kwargs.setdefault("api_key", "test-key")
    return so.SystemOneClient(transport=httpx2.MockTransport(handler), **kwargs)


def _ok(payload: dict[str, Any]) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=payload)

    return handler


def _recorder(payload: dict[str, Any], seen: list[httpx2.Request]) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=payload)

    return handler


def _status(status: int, body: dict[str, Any] | None = None) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=body or {"message": "nope"})

    return handler


_TRIAGE_PAYLOAD = {
    "model": "jev-1.13.0",
    "answers": {
        "department": {
            "type": "choice",
            "choice": "technical",
            "probabilities": {"billing": 0.05, "technical": 0.9, "sales": 0.05},
            "confidence": 0.83,
        },
        "frustration": {
            "type": "score",
            "score": 1.3,
            "legend": {"0": "calm", "1": "frustrated", "2": "very angry"},
            "probabilities": {"0": 0.0, "1": 0.7, "2": 0.3},
            "confidence": 0.54,
        },
        "is_urgent": {"type": "noul", "noul": 0.95},
    },
    "usage": {"input_tokens": 412, "output_tokens": 80},
}


# --- Question builders ---------------------------------------------------------


def test_question_builders_emit_wire_shape():
    assert so.noul("Is it urgent?") == {"type": "noul", "instructions": "Is it urgent?"}
    assert so.noul("Q", criteria={"true": "yes", "false": "no"})["criteria"] == {
        "true": "yes",
        "false": "no",
    }
    assert so.choice("Which?", criteria={"a": "desc a", "b": None}) == {
        "type": "choice",
        "instructions": "Which?",
        "criteria": {"a": "desc a", "b": None},
    }
    assert so.score("How bad?", criteria=["low", "mid", "high"])["type"] == "score"


def test_rich_instruction_objects_pass_through():
    instruction = {"question": "Which team?", "focus": "Classify the request."}
    assert so.choice(instruction, criteria={"x": "y"})["instructions"] is instruction


def test_score_criteria_bounds_are_enforced():
    with pytest.raises(ValueError):
        so.score("too few", criteria=["only one"])
    with pytest.raises(ValueError):
        so.score("too many", criteria=[f"level {i}" for i in range(11)])


def test_normalize_score_puts_rubrics_on_the_same_scale():
    def answer(value: float) -> so.ScoreAnswer:
        return so.ScoreAnswer(score=value, probabilities={}, confidence=1.0, legend={})

    assert so.normalize_score(answer(2.0), 3) == 1.0  # 3-level rubric spans 0..2
    assert so.normalize_score(answer(2.0), 5) == 0.5  # 5-level rubric spans 0..4
    assert so.normalize_score(answer(9.0), 3) == 1.0  # clamped
    assert so.normalize_score(answer(-1.0), 3) == 0.0


# --- Happy path ----------------------------------------------------------------


def test_system_one_parses_every_answer_type():
    resp = _client(_ok(_TRIAGE_PAYLOAD)).system_one(
        "a support ticket",
        {
            "department": so.choice("Which team?", criteria={"billing": "b", "technical": "t"}),
            "frustration": so.score("How frustrated?", criteria=["calm", "frustrated", "angry"]),
            "is_urgent": so.noul("Is it urgent?"),
        },
    )

    assert resp.model == "jev-1.13.0"  # versioned id, not the alias that was sent
    assert resp.choice("department") == "technical"
    assert resp.score("frustration") == pytest.approx(1.3)
    assert resp.noul("is_urgent") == pytest.approx(0.95)
    assert resp.confidence("department") == pytest.approx(0.83)
    assert resp.usage == {"input_tokens": 412, "output_tokens": 80}
    assert resp.latency_ms >= 0


def test_score_probabilities_and_legend_use_string_keys():
    """SDK keys score levels by int; str keys survive a JSON round-trip into the ledger."""
    resp = _client(_ok(_TRIAGE_PAYLOAD)).system_one("s", {"f": so.noul("?")})
    frustration = resp.answers["frustration"]
    assert isinstance(frustration, so.ScoreAnswer)
    assert set(frustration.probabilities) == {"0", "1", "2"}
    assert frustration.legend["1"] == "frustrated"
    assert json.loads(json.dumps(frustration.probabilities)) == frustration.probabilities


def test_request_carries_state_questions_model_and_bearer_auth():
    seen: list[httpx2.Request] = []
    _client(_recorder(_TRIAGE_PAYLOAD, seen)).system_one(
        "state text", {"is_urgent": so.noul("Is it urgent?")}
    )

    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["state"] == "state text"
    assert body["model"] == "jev-latest"  # the alias is the default
    assert body["questions"] == {"is_urgent": {"type": "noul", "instructions": "Is it urgent?"}}


def test_state_may_be_structured():
    seen: list[httpx2.Request] = []
    state = {"ticket": {"message": "hi"}, "open_orders": [{"id": "A-1"}]}
    _client(_recorder(_TRIAGE_PAYLOAD, seen)).system_one(state, {"q": so.noul("?")})
    assert json.loads(seen[0].content)["state"] == state


def test_explicit_model_pins_the_version():
    seen: list[httpx2.Request] = []
    _client(_recorder(_TRIAGE_PAYLOAD, seen), model="jev-1.13.0").system_one(
        "s", {"q": so.noul("?")}
    )
    assert json.loads(seen[0].content)["model"] == "jev-1.13.0"


def test_one_request_evaluates_every_question():
    """Speculative fan-out: N questions must not become N requests."""
    seen: list[httpx2.Request] = []
    _client(_recorder(_TRIAGE_PAYLOAD, seen)).system_one(
        "s", {"a": so.noul("?"), "b": so.noul("?"), "c": so.noul("?")}
    )
    assert len(seen) == 1
    assert len(json.loads(seen[0].content)["questions"]) == 3


# --- Answer access -------------------------------------------------------------


def test_accessor_type_mismatch_is_loud():
    resp = _client(_ok(_TRIAGE_PAYLOAD)).system_one("s", {"q": so.noul("?")})

    with pytest.raises(KeyError):
        resp.choice("is_urgent")  # it is a noul
    with pytest.raises(KeyError):
        resp.confidence("is_urgent")  # noul answers carry no confidence
    with pytest.raises(KeyError):
        resp.noul("nope")


def test_missing_answer_is_a_loud_read_not_a_silent_no(caplog):
    payload = {"model": "jev-1.13.0", "usage": {}, "answers": {"asked": {"type": "noul", "noul": 1.0}}}
    with caplog.at_level("WARNING"):
        resp = _client(_ok(payload)).system_one(
            "s", {"asked": so.noul("?"), "not_asked": so.noul("?")}
        )

    assert "not_asked" in caplog.text
    assert resp.noul("asked") == 1.0
    with pytest.raises(KeyError):
        resp.noul("not_asked")  # never a fabricated 0.0


def test_unknown_answer_type_is_dropped_and_then_loud(caplog):
    """The SDK skips answer types it does not model (forward-compat); we stay loud."""
    payload = {"model": "m", "usage": {}, "answers": {"q": {"type": "vibe", "vibe": 0.5}}}
    with caplog.at_level("WARNING"):
        resp = _client(_ok(payload)).system_one("s", {"q": so.noul("?")})

    assert resp.answers == {}
    with pytest.raises(KeyError):
        resp.noul("q")


def test_null_usage_counters_are_omitted_not_zeroed():
    payload = {"model": "m", "usage": {}, "answers": {"q": {"type": "noul", "noul": 0.1}}}
    resp = _client(_ok(payload)).system_one("s", {"q": so.noul("?")})
    assert resp.usage == {}


def test_missing_usage_block_is_a_contract_violation():
    payload = {"model": "m", "answers": {"q": {"type": "noul", "noul": 0.1}}}
    with pytest.raises(so.SystemOneError):
        _client(_ok(payload)).system_one("s", {"q": so.noul("?")})


def test_malformed_answer_field_is_a_contract_violation():
    payload = {"model": "m", "usage": {}, "answers": {"q": {"type": "choice", "choice": 7}}}
    with pytest.raises(so.SystemOneError):
        _client(_ok(payload)).system_one("s", {"q": so.noul("?")})


# --- Guardrails ----------------------------------------------------------------


def test_unconfigured_client_raises_unavailable():
    client = so.SystemOneClient(api_key="")
    assert client.configured is False
    with pytest.raises(so.SystemOneUnavailable):
        client.system_one("s", {"q": so.noul("?")})


def test_oversized_state_is_refused_never_truncated():
    """Silent truncation is an accuracy leak — oversize must be a loud state-bug."""
    with pytest.raises(so.SystemOnePayloadTooLarge, match="split the state"):
        _client(_ok(_TRIAGE_PAYLOAD)).system_one(
            "x" * (so.MAX_PAYLOAD_CHARS + 1), {"q": so.noul("?")}
        )


def test_empty_question_map_is_rejected():
    with pytest.raises(ValueError):
        _client(_ok(_TRIAGE_PAYLOAD)).system_one("s", {})


def test_circular_state_raises_a_typed_error():
    state: dict[str, Any] = {}
    state["self"] = state
    with pytest.raises(so.SystemOneError, match="JSON-serializable"):
        _client(_ok(_TRIAGE_PAYLOAD)).system_one(state, {"q": so.noul("?")})


# --- Retries and error mapping (the SDK owns retry; we map the outcome) --------


def test_retries_on_429_then_succeeds():
    attempts = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx2.Response(429, json={"message": "slow down"})
        return httpx2.Response(200, json=_TRIAGE_PAYLOAD)

    policy = RetryPolicy(max_retries=2, backoff_initial=0, backoff_max=0)
    resp = _client(handler, retry_policy=policy).system_one("s", {"is_urgent": so.noul("?")})
    assert attempts["n"] == 2
    assert resp.model == "jev-1.13.0"


def test_retries_on_529_overloaded():
    attempts = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            return httpx2.Response(529, json={"message": "overloaded"})
        return httpx2.Response(200, json=_TRIAGE_PAYLOAD)

    policy = RetryPolicy(max_retries=3, backoff_initial=0, backoff_max=0)
    _client(handler, retry_policy=policy).system_one("s", {"is_urgent": so.noul("?")})
    assert attempts["n"] == 3


def test_retries_exhausted_maps_to_http_error():
    policy = RetryPolicy(max_retries=2, backoff_initial=0, backoff_max=0)
    with pytest.raises(so.SystemOneHTTPError) as excinfo:
        _client(_status(429, {"message": "slow down"}), retry_policy=policy).system_one(
            "s", {"q": so.noul("?")}
        )
    assert excinfo.value.status_code == 429
    assert "slow down" in excinfo.value.detail


def test_server_error_maps_to_http_error():
    policy = RetryPolicy(max_retries=1, backoff_initial=0, backoff_max=0)
    with pytest.raises(so.SystemOneHTTPError) as excinfo:
        _client(_status(503, {"message": "unavailable"}), retry_policy=policy).system_one(
            "s", {"q": so.noul("?")}
        )
    assert excinfo.value.status_code == 503


def test_auth_failure_is_not_retried():
    attempts = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        attempts["n"] += 1
        return httpx2.Response(401, json={"message": "invalid api key"})

    policy = RetryPolicy(max_retries=3, backoff_initial=0, backoff_max=0)
    with pytest.raises(so.SystemOneHTTPError) as excinfo:
        _client(handler, retry_policy=policy).system_one("s", {"q": so.noul("?")})
    assert attempts["n"] == 1  # a bad key is a bug, not a transient
    assert excinfo.value.status_code == 401


def test_validation_error_detail_is_surfaced():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            422, json={"error": {"message": "criteria: need at least 2 levels"}}
        )

    policy = RetryPolicy(max_retries=0, backoff_initial=0, backoff_max=0)
    with pytest.raises(so.SystemOneHTTPError, match="at least 2 levels"):
        _client(handler, retry_policy=policy).system_one("s", {"q": so.noul("?")})


def test_connection_failure_is_typed():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route to host")

    policy = RetryPolicy(max_retries=0, backoff_initial=0, backoff_max=0)
    with pytest.raises(so.SystemOneError):
        _client(handler, retry_policy=policy).system_one("s", {"q": so.noul("?")})


# --- Lifetime ------------------------------------------------------------------


def test_client_is_reused_across_calls():
    """One SDK client per seam client: connections are reused, not rebuilt per call."""
    seen: list[httpx2.Request] = []
    client = _client(_recorder(_TRIAGE_PAYLOAD, seen))
    client.system_one("s", {"is_urgent": so.noul("?")})
    client.system_one("s", {"is_urgent": so.noul("?")})
    assert len(seen) == 2
    assert client._client is not None


def test_close_is_idempotent():
    client = _client(_ok(_TRIAGE_PAYLOAD))
    client.system_one("s", {"q": so.noul("?")})
    client.close()
    client.close()
    assert client._client is None


def test_context_manager_closes():
    with _client(_ok(_TRIAGE_PAYLOAD)) as client:
        client.system_one("s", {"q": so.noul("?")})
    assert client._client is None


def test_module_level_client_is_cached():
    so.clear_system_one_cache()
    first = so.get_system_one_client()
    assert so.get_system_one_client() is first
    so.clear_system_one_cache()
    assert so.get_system_one_client() is not first


# --- Wiring guards -------------------------------------------------------------


def test_typesafe_is_not_a_chat_provider():
    """No complete()/stream(): registering it would yield a silent MockLLM chat brain."""
    from config.ai_providers import SECRET_AI_KEY_FIELDS, VALID_PROVIDERS

    assert "typesafe" not in VALID_PROVIDERS
    assert "typesafe_api_key" in SECRET_AI_KEY_FIELDS


def test_key_is_masked_on_settings_read():
    from config.settings import mask_settings

    masked = mask_settings({"ai": {"typesafe_api_key": "ts_secret"}})
    assert masked["ai"]["typesafe_api_key"] == "********"


def test_no_key_is_ever_logged(caplog):
    """A retry must not leak the bearer token into logs."""
    policy = RetryPolicy(max_retries=1, backoff_initial=0, backoff_max=0)
    with caplog.at_level("DEBUG"), pytest.raises(so.SystemOneHTTPError):
        _client(_status(429), api_key="ts_supersecret", retry_policy=policy).system_one(
            "s", {"q": so.noul("?")}
        )
    assert "ts_supersecret" not in caplog.text


def test_vendor_types_do_not_leak_past_the_seam():
    """Call sites must not need typesafe_sdk: answers are our own dataclasses."""
    resp = _client(_ok(_TRIAGE_PAYLOAD)).system_one("s", {"q": so.noul("?")})
    for answer in resp.answers.values():
        assert type(answer).__module__ == "ai.llm.system_one"


# --- Async ---------------------------------------------------------------------


async def test_async_path_matches_sync():
    resp = await _client(_ok(_TRIAGE_PAYLOAD)).asystem_one("s", {"is_urgent": so.noul("?")})
    assert resp.noul("is_urgent") == pytest.approx(0.95)
