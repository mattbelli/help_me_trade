"""TypeSafe "System One" (Jev) — typed judgments instead of generated prose.

Jev evaluates a ``state`` against a map of typed ``questions`` and returns typed
``answers``: ``choice`` / ``score`` / ``noul``, each with ``probabilities`` and a
``confidence``. No text generation and therefore no parse step, no repair retry,
and no ``parse_fail`` class — the failure modes the text lanes pay for
(``ai/llm/structured.py``, ``research/reason.py``). Every question in one request
sees the same state and is evaluated independently, so adding questions does not
degrade the earlier ones.

**This is NOT an LLMClient.** It has no ``complete()``/``stream()``, and it is
deliberately absent from ``AI_PROVIDERS``/``VALID_PROVIDERS``: registering it there
would let ``get_llm_provider_name()`` return ``"typesafe"`` and
``_build_llm_client()`` fall through to ``MockLLM()`` — a stub chat brain. This
module is a sibling seam, called explicitly by lanes that want a typed judgment.

**Why this module exists over the vendor SDK.** The transport is
``typesafe_sdk`` (pinned in requirements.txt) — it owns the wire format, the retry
policy, and a typed error taxonomy we should not re-derive. This file is the
*seam*: call sites import from here, never from ``typesafe_sdk``, so the vendor
package stays swappable in one file. It also carries the house rules below, which
belong on our side of the boundary.

House rules that apply to every value returned here:

- **Never a displayable win%.** An answer's ``probabilities`` are a model claim,
  not calibration. ``confidence`` is derived from the spread of the distribution,
  **not from correctness** — a confidently wrong answer returns ``confidence 1.0``.
  Route on it; never present it to the founder as a chance of being right. A number
  becomes presentable only after the calibrator grades it against
  ``db/idea_outcomes.py`` rows (``signals/decision_scorer.py``).
- **Real token usage.** Unlike ``ai/llm/openai_compat.py:complete()``, which reads
  only ``choices[0]`` and discards ``usage``, the response here carries it, so
  ``db/llm_spend`` can finally count tokens on this lane. Persist before use.
- **Log the answering model.** The response echoes a versioned id
  (e.g. ``jev-1.13.0``); an alias moves under you, so pin the version once
  confidence thresholds are tuned against it.
- **Never truncate the state.** An oversized payload is refused loudly; see
  ``MAX_PAYLOAD_CHARS``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from typesafe_sdk import (
    ChoiceAnswer as _SdkChoiceAnswer,
)
from typesafe_sdk import (
    NoulAnswer as _SdkNoulAnswer,
)
from typesafe_sdk import (
    RetryPolicy,
    TypeSafeAPIError,
    TypeSafeClient,
    TypeSafeError,
)
from typesafe_sdk import (
    ScoreAnswer as _SdkScoreAnswer,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"

# The documented per-request budget is ~32,000 tokens shared by the state AND the
# questions (~150,000 chars of English). We REFUSE an oversized payload rather than
# shave it: silent truncation is a real accuracy leak in this codebase (see
# ai/research/setup_scan.py's payload[:48_000]) — the model would judge a partial
# state and never say so. Oversize is a state-assembly bug; fix the builder.
MAX_PAYLOAD_CHARS = 150_000

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 3


class SystemOneError(RuntimeError):
    """Base class for every failure this client raises."""


class SystemOneUnavailable(SystemOneError):
    """No API key configured. Callers treat this as 'lane unconfigured', not a fault."""


class SystemOneHTTPError(SystemOneError):
    """The API refused the call (after the SDK's own retries were exhausted)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"typesafe {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class SystemOnePayloadTooLarge(SystemOneError):
    """The serialized state + questions exceeded MAX_PAYLOAD_CHARS (see above)."""


# --- Question builders ---------------------------------------------------------
# The wire shape is dict-based and the SDK accepts either a typed question object or
# a raw dict with a "type" (typesafe_sdk/_core/questions.normalize_questions), so
# these stay dicts: a call site reads like the question it asks, printing one shows
# the wire form, and the payload-size guard can serialize them with plain json.
# `instructions` and the values inside `criteria` accept either a plain string OR a
# rich object ({"question":..., "focus":..., "compare":[...]}, {"what":...,
# "examples":[...]}) — the API takes both, and concrete descriptions measurably
# concentrate probability.


def noul(instructions: Any, *, criteria: dict[str, Any] | None = None) -> dict[str, Any]:
    """Yes/no. Returns ``noul`` in 0..1 (the probability the answer is yes)."""
    question: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if criteria is not None:
        question["criteria"] = criteria
    return question


def choice(instructions: Any, *, criteria: dict[str, Any]) -> dict[str, Any]:
    """Pick one option. Returns ``choice`` + a distribution over every option."""
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: Any, *, criteria: list[Any]) -> dict[str, Any]:
    """A position on an ordered rubric (2..10 described levels)."""
    if len(criteria) < 2:
        raise ValueError("score criteria needs at least 2 levels")
    if len(criteria) > 10:
        raise ValueError("score criteria takes at most 10 levels")
    return {"type": "score", "instructions": instructions, "criteria": criteria}


def normalize_score(answer: "ScoreAnswer", n_levels: int) -> float:
    """Put a Score on 0..1 so weights across differently-sized rubrics mean what they say.

    A 4-level rubric returns 0..3 and a 3-level one 0..2; without this the top of
    one outweighs the top of the other. Composite-scoring pattern, applied in code.
    """
    top = max(1, n_levels - 1)
    return max(0.0, min(1.0, answer.score / top))


# --- Answers -------------------------------------------------------------------


@dataclass(frozen=True)
class NoulAnswer:
    noul: float


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    legend: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SystemOneResponse:
    """One call's typed answers, the model that answered, and real token usage."""

    model: str
    answers: dict[str, NoulAnswer | ChoiceAnswer | ScoreAnswer]
    usage: dict[str, Any]
    latency_ms: int

    def noul(self, question_id: str) -> float:
        return self._get(question_id, NoulAnswer).noul

    def choice(self, question_id: str) -> str:
        return self._get(question_id, ChoiceAnswer).choice

    def score(self, question_id: str) -> float:
        return self._get(question_id, ScoreAnswer).score

    def confidence(self, question_id: str) -> float:
        answer = self.answers.get(question_id)
        if not isinstance(answer, (ChoiceAnswer, ScoreAnswer)):
            raise KeyError(f"{question_id}: no confidence (noul answers carry none)")
        return answer.confidence

    def _get(self, question_id: str, kind: type) -> Any:
        answer = self.answers.get(question_id)
        if answer is None:
            raise KeyError(f"{question_id}: no answer in response")
        if not isinstance(answer, kind):
            raise KeyError(f"{question_id}: expected {kind.__name__}, got {type(answer).__name__}")
        return answer


def _str_keyed(raw: Any) -> dict[str, Any]:
    """SDK score probabilities/legend are keyed by int level; str keys are JSON-safe."""
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


def _parse_answer(raw: Any) -> NoulAnswer | ChoiceAnswer | ScoreAnswer:
    if isinstance(raw, _SdkNoulAnswer):
        return NoulAnswer(noul=float(raw.noul))
    if isinstance(raw, _SdkChoiceAnswer):
        return ChoiceAnswer(
            choice=str(raw.choice),
            probabilities=_str_keyed(raw.probabilities),
            confidence=float(raw.confidence),
        )
    if isinstance(raw, _SdkScoreAnswer):
        return ScoreAnswer(
            score=float(raw.score),
            probabilities=_str_keyed(raw.probabilities),
            confidence=float(raw.confidence),
            legend=_str_keyed(raw.legend),
        )
    raise SystemOneError(f"unsupported answer object: {type(raw).__name__}")


def _usage_dict(usage: Any) -> dict[str, Any]:
    """Only the counters the provider actually reported — never a fabricated zero."""
    out: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            out[key] = value
    return out


# --- Client --------------------------------------------------------------------


class SystemOneClient:
    """Typed-judgment client. Holds one SDK client, so reuse the instance (or the cache)."""

    provider = "typesafe"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_policy: RetryPolicy | None = None,
        transport: Any | None = None,
    ) -> None:
        from config.settings import load_settings

        ai = load_settings().get("ai", {})
        self.api_key = (api_key if api_key is not None else ai.get("typesafe_api_key", "")).strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = (model or DEFAULT_MODEL).strip()
        self.timeout_s = timeout_s
        self.max_retries = max(0, max_retries)
        self._retry_policy = retry_policy
        self._transport = transport
        self._client: TypeSafeClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _sdk_client(self) -> TypeSafeClient:
        if self._client is None:
            if not self.configured:
                raise SystemOneUnavailable("no typesafe_api_key configured")
            self._client = TypeSafeClient(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
                timeout=self.timeout_s,
                retry=self._retry_policy or RetryPolicy(max_retries=self.max_retries),
                transport=self._transport,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "SystemOneClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _guard_payload(self, state: Any, questions: dict[str, Any], model: str) -> None:
        if not questions:
            raise ValueError("at least one question is required")
        try:
            encoded = json.dumps(
                {"state": state, "model": model, "questions": questions}, default=str
            )
        except (TypeError, ValueError) as exc:
            raise SystemOneError(f"state is not JSON-serializable: {exc}") from exc
        if len(encoded) > MAX_PAYLOAD_CHARS:
            raise SystemOnePayloadTooLarge(
                f"{len(encoded)} chars > {MAX_PAYLOAD_CHARS} budget "
                "(state and questions share ~32k tokens) — split the state, "
                "never let it be truncated silently"
            )

    def system_one(
        self,
        state: Any,
        questions: dict[str, Any],
        *,
        model: str | None = None,
    ) -> SystemOneResponse:
        """Evaluate every question against ``state``. One call, all answers."""
        resolved_model = (model or self.model).strip()
        self._guard_payload(state, questions, resolved_model)

        started = time.monotonic()
        try:
            raw = self._sdk_client().system_one(state, questions, model=resolved_model)
        except TypeSafeAPIError as exc:
            # The SDK already retried the retryable statuses; this is terminal.
            raise SystemOneHTTPError(exc.status, str(exc)) from exc
        except TypeSafeError as exc:
            raise SystemOneError(str(exc)) from exc

        answers: dict[str, NoulAnswer | ChoiceAnswer | ScoreAnswer] = {
            str(qid): _parse_answer(answer) for qid, answer in (raw.answers or {}).items()
        }
        missing = set(questions) - set(answers)
        if missing:
            # Two ways an answer goes absent: a partial response, or the SDK dropping
            # an answer type it does not model (it warns and skips for forward-compat).
            # Tolerated here — questions are speculative by design — but reading an
            # absent id raises KeyError, so the silence stays loud at the point of use
            # rather than turning into a fabricated negative.
            logger.warning("typesafe returned no answer for: %s", sorted(missing))

        return SystemOneResponse(
            model=str(getattr(raw, "model", "") or resolved_model),
            answers=answers,
            usage=_usage_dict(getattr(raw, "usage", None)),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def asystem_one(
        self,
        state: Any,
        questions: dict[str, Any],
        *,
        model: str | None = None,
    ) -> SystemOneResponse:
        return await asyncio.to_thread(self.system_one, state, questions, model=model)


# --- Module-level convenience (cached client, mirroring ai.llm.factory) ---------

_cached: SystemOneClient | None = None


def get_system_one_client() -> SystemOneClient:
    global _cached
    if _cached is None:
        _cached = SystemOneClient()
    return _cached


def clear_system_one_cache() -> None:
    """Drop the cached client so the next call re-reads settings (key changes)."""
    global _cached
    if _cached is not None:
        _cached.close()
    _cached = None


def is_configured() -> bool:
    return get_system_one_client().configured


def system_one(
    state: Any,
    questions: dict[str, Any],
    *,
    model: str | None = None,
    client: SystemOneClient | None = None,
) -> SystemOneResponse:
    return (client or get_system_one_client()).system_one(state, questions, model=model)


async def asystem_one(
    state: Any,
    questions: dict[str, Any],
    *,
    model: str | None = None,
    client: SystemOneClient | None = None,
) -> SystemOneResponse:
    return await (client or get_system_one_client()).asystem_one(state, questions, model=model)
