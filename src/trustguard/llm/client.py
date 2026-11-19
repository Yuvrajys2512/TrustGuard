"""Rotating multi-provider LLM client.

Phase 1 needs one working model. Phases 2–8 need *thousands* of calls
(attacks x pipelines x trials x retries), which is where per-provider rate
limits become the bottleneck. So the rotation lives here from day one rather
than being retrofitted once the experiments are already slow.

Behaviour: try the first configured backend; on a rate-limit or transient
error, fall through to the next; wrap around with exponential backoff. A
backend whose API key is absent is skipped at construction time, so a machine
with only ``GROQ_API_KEY`` set works with no config changes.

**On reproducibility — read this before trusting a `seed`.** Measured on Groq
`openai/gpt-oss-120b` at ``temperature=0`` with a fixed seed, three identical
long-form requests returned 8956 / 8653 / 8858 characters. Two different seeds
returned byte-identical short completions. The provider accepts ``seed`` and
does not honour it in any way we can rely on. So:

* ``seed`` is plumbed through and recorded because it costs nothing and the
  OpenAI backend *does* honour it — but it does **not** buy bit-exact replay
  on Groq, and no amount of client-side code can make it.
* What *is* stable is pipeline **structure** — the sequence of delegations,
  tool calls and memory operations. Regression checks should assert on that,
  not on generated text.
* Because replay is unavailable, runs are instead made **attributable**: the
  client counts calls per backend so a trace records which model(s) actually
  served it. Silent mid-run rotation to a different model is the single
  largest source of output variance (5753 vs 9942 characters across the three
  configured Groq models on one prompt), and a trace that hid it would
  misattribute that variance to whatever the experiment was manipulating.
"""

from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Backend:
    """One provider/model pair the client may route to."""

    provider: str
    model: str
    api_key_env: str
    temperature: float = 0.0
    max_tokens: int = 2048
    # Best-effort only; see the module docstring. Honoured by OpenAI, accepted
    # but not honoured by Groq, and unsupported by the Anthropic API.
    seed: int | None = None
    _client: Any = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        return bool(os.getenv(self.api_key_env))

    @property
    def supports_seed(self) -> bool:
        """Whether the provider's API accepts a seed at all.

        Accepting one is not the same as honouring it — Groq does the former
        and not the latter. Anthropic offers no seed parameter whatsoever, so
        passing one there would be an error rather than a no-op.
        """
        return self.provider in ("groq", "openai")

    def build(self) -> Any:
        if self._client is not None:
            return self._client

        if self.provider == "groq":
            from langchain_groq import ChatGroq

            # ChatGroq has no first-class `seed` field, but the Groq API
            # accepts one on the request body, so it goes through model_kwargs.
            extra = {"seed": self.seed} if self.seed is not None else {}
            self._client = ChatGroq(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                model_kwargs=extra,
            )
        elif self.provider == "openai":
            from langchain_openai import ChatOpenAI

            self._client = ChatOpenAI(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                seed=self.seed,
            )
        elif self.provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            # No seed parameter exists on this API; nothing to pass.
            self._client = ChatAnthropic(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

        return self._client


# Ordered by preference. Groq first because it is fast and free-tier generous,
# which matters when a single experiment sweep is a few thousand calls.
#
# Model availability is per-account on Groq. Verify with:
#   python scripts/check_backends.py
# and update this list rather than letting runs fail with model_not_found.
DEFAULT_BACKENDS: list[Backend] = [
    Backend("groq", "openai/gpt-oss-120b", "GROQ_API_KEY"),
    Backend("groq", "qwen/qwen3.6-27b", "GROQ_API_KEY"),
    Backend("groq", "openai/gpt-oss-20b", "GROQ_API_KEY"),
    Backend("openai", "gpt-4o-mini", "OPENAI_API_KEY"),
    Backend("anthropic", "claude-sonnet-4-5-20250929", "ANTHROPIC_API_KEY"),
]

_RETRYABLE_MARKERS = (
    "rate limit",
    "rate_limit",
    "429",
    "too many requests",
    "overloaded",
    "timeout",
    "timed out",
    "503",
    "502",
    "connection",
    "temporarily unavailable",
)


def _is_retryable(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _RETRYABLE_MARKERS)


# Reasoning models (qwen3, deepseek-r1, ...) emit their chain of thought in
# <think> tags ahead of the answer. Left in, it breaks JSON parsing in the
# agents and pollutes the trace with text the agent did not "say". Stripped
# centrally so no caller has to know which backend it happened to get.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THINK = re.compile(r"^.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(text: str) -> str:
    cleaned = _THINK_BLOCK.sub("", text)
    if "</think>" in cleaned.lower():
        cleaned = _UNCLOSED_THINK.sub("", cleaned, count=1)
    return cleaned.strip()


class RotatingLLMClient:
    """Calls an LLM, rotating across backends when one is rate-limited."""

    def __init__(
        self,
        backends: list[Backend] | None = None,
        temperature: float = 0.0,
        max_attempts: int = 6,
        seed: int | None = None,
    ) -> None:
        candidates = backends if backends is not None else DEFAULT_BACKENDS
        # Copy rather than mutate. DEFAULT_BACKENDS is a module-level list of
        # mutable dataclasses that also caches a built provider client; setting
        # temperature/seed on it in place would leak this client's settings
        # into every other client in the process and hand back a stale cached
        # client built under the old ones.
        self.backends = [
            replace(b, temperature=temperature, seed=seed, _client=None)
            for b in candidates
            if b.available
        ]
        if not self.backends:
            raise RuntimeError(
                "No LLM backend available. Set at least one of: "
                + ", ".join(sorted({b.api_key_env for b in candidates}))
                + " (a .env file in the project root works — see .env.example)."
            )
        self.temperature = temperature
        self.seed = seed
        self.max_attempts = max_attempts
        self._index = 0
        # Backends retired mid-session: a wrong model name or a revoked key is
        # permanent, so retrying it every call wastes the retry budget that
        # genuine rate-limits need.
        self._dead: set[str] = set()
        # Per-backend successful-call counts. Written into the trace so a run
        # says which model(s) actually produced it — see the module docstring
        # on why silent rotation must not stay invisible.
        self.calls: dict[str, int] = {}

    def _key(self, backend: Backend) -> str:
        return f"{backend.provider}:{backend.model}"

    @property
    def _live(self) -> list[Backend]:
        live = [b for b in self.backends if self._key(b) not in self._dead]
        return live or self.backends

    @property
    def active(self) -> Backend:
        live = self._live
        return live[self._index % len(live)]

    def _rotate(self) -> None:
        self._index = (self._index + 1) % max(len(self._live), 1)

    def complete(self, system: str, user: str) -> str:
        """Single-turn completion. Returns the model's text response."""
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [SystemMessage(content=system), HumanMessage(content=user)]
        last_error: Exception | None = None

        for attempt in range(self.max_attempts):
            backend = self.active
            try:
                response = backend.build().invoke(messages)
                content = response.content
                # Some providers return content as a list of blocks.
                if isinstance(content, list):
                    content = "".join(
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in content
                    )
                key = self._key(backend)
                self.calls[key] = self.calls.get(key, 0) + 1
                return _strip_reasoning(str(content))
            except Exception as exc:  # noqa: BLE001 - we classify below
                last_error = exc
                if not _is_retryable(exc):
                    # Permanent for this backend (bad model name, bad key,
                    # malformed request). Retire it; if it was the last one
                    # standing there is nothing left to try.
                    self._dead.add(self._key(backend))
                    if len(self._dead) >= len(self.backends):
                        raise
                n_live = len(self._live)
                self._rotate()
                # Full jitter backoff; only sleep once we have cycled every
                # live backend, since rotating is itself the cheapest mitigation.
                if attempt >= n_live - 1:
                    delay = min(2 ** (attempt - n_live + 1), 30)
                    time.sleep(delay * (0.5 + random.random() / 2))

        raise RuntimeError(
            f"All {len(self.backends)} backend(s) failed after "
            f"{self.max_attempts} attempts. Last error: {last_error}"
        ) from last_error

    def describe(self) -> str:
        return ", ".join(f"{b.provider}:{b.model}" for b in self.backends)

    def usage_snapshot(self) -> dict[str, int]:
        """Per-backend call counts as of now.

        Pass to ``usage_summary(since=...)`` to attribute a *single* run rather
        than the whole client lifetime. Taken at the start of a run by
        ``run_pipeline``.
        """
        return dict(self.calls)

    def usage_summary(self, since: dict[str, int] | None = None) -> dict[str, Any]:
        """What actually served one run, for the trace.

        ``rotated`` is the flag that matters when reading results: if more than
        one backend served a single run, differences in that run's output may
        come from the model change rather than from the condition under test.

        **``since`` is what makes this per-run rather than per-process.** The
        counters on this client are cumulative and one client can serve many
        runs — which is exactly what an experiment sweep does. Called with no
        ``since``, this returns lifetime totals, and on the second run of a
        sweep those totals describe the sweep so far, not the run being
        written. That produced traces whose ``rotated`` flag was true because
        some *earlier* run had rotated, which is worse than no flag at all: it
        is a field that reads like per-run attribution and silently is not.
        Discovered in Phase 3, fixed here, logged in the decision log
        (2026-08-21).
        """
        base = since or {}
        delta = {
            key: count - base.get(key, 0)
            for key, count in self.calls.items()
            if count - base.get(key, 0) > 0
        }
        return {
            "calls_by_backend": delta,
            "rotated": len(delta) > 1,
            "retired_backends": sorted(self._dead),
            # Kept alongside so a reader can still see the whole picture, and
            # so a sweep is diagnosable after the fact.
            "calls_by_backend_client_total": dict(self.calls),
        }
