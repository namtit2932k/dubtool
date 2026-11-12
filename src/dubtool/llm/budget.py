"""Per-run LLM request budget (AGENT_UPDATE_6.md 5.2).

`llm.max_requests_per_run` (config.yaml, default 40) caps EVERY Gemini
request in one pipeline run: stage-3 translate chunks, the enforcement
pass, and each stage-5 shortening round. The pipeline binds one
RequestBudget at the start of `run_dub` (a contextvar, so worker threads
inheriting the context share the same counter). When the budget runs out
the client raises BudgetExhausted immediately WITHOUT a network call;
stages treat it as "finish the run with what exists" (log a clear line,
leave remaining segments at their last good state).

The counter is deliberately not persisted: the budget is per run, the
quota is per day+model, and those are different things.
"""

from __future__ import annotations

import contextvars
import threading
from typing import Any

from dubtool.llm.base import LLMError

DEFAULT_MAX_REQUESTS_PER_RUN = 120
DEFAULT_MAX_KEY_MULTIPLIER = 4


class BudgetExhausted(LLMError):
    """The run's request budget is used up; stages skip further LLM work."""


class RequestBudget:
    def __init__(self, limit: int = DEFAULT_MAX_REQUESTS_PER_RUN):
        self.limit = max(1, int(limit))
        self.used = 0
        self._lock = threading.Lock()
        self._logged_exhausted = False

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self.used >= self.limit

    def try_consume(self, what: str = "request") -> bool:
        """Consume one slot atomically; False (no request made) when exhausted."""
        with self._lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            return True

    def claim_exhausted_log(self) -> bool:
        """Return True once per exhausted budget so parallel workers don't spam."""
        with self._lock:
            if self._logged_exhausted:
                return False
            self._logged_exhausted = True
            return True


_current_budget: contextvars.ContextVar[RequestBudget | None] = contextvars.ContextVar(
    "dubtool_llm_budget", default=None
)


def bind(budget: RequestBudget | None):
    """Install the run budget; returns a reset token (see contextvars)."""
    return _current_budget.set(budget)


def reset(token: Any) -> None:
    _current_budget.reset(token)


def current() -> RequestBudget | None:
    return _current_budget.get()


def from_config(config, key_count: int = 1) -> RequestBudget:
    llm_cfg = getattr(config, "llm", {}) or {}
    base = int(
        llm_cfg.get("max_requests_per_run", DEFAULT_MAX_REQUESTS_PER_RUN)
        or DEFAULT_MAX_REQUESTS_PER_RUN
    )
    scale = max(1, min(int(key_count or 1), DEFAULT_MAX_KEY_MULTIPLIER))
    return RequestBudget(base * scale)


def raise_if_exhausted() -> None:
    budget = current()
    if budget is not None and budget.exhausted:
        raise BudgetExhausted(
            f"LLM request budget used up ({budget.limit} requests this run) — "
            "finishing the run with what exists (stages are resumable)"
        )
