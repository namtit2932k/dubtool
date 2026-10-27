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
from typing import Any

from dubtool.llm.base import LLMError

DEFAULT_MAX_REQUESTS_PER_RUN = 40


class BudgetExhausted(LLMError):
    """The run's request budget is used up; stages skip further LLM work."""


class RequestBudget:
    def __init__(self, limit: int = DEFAULT_MAX_REQUESTS_PER_RUN):
        self.limit = max(1, int(limit))
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def try_consume(self, what: str = "request") -> bool:
        """Consume one slot; False (and no request made) when exhausted."""
        if self.exhausted:
            return False
        self.used += 1
        if self.used == self.limit:
            pass  # the last request still goes through; the next one fails
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


def from_config(config) -> RequestBudget:
    llm_cfg = getattr(config, "llm", {}) or {}
    limit = int(
        llm_cfg.get("max_requests_per_run", DEFAULT_MAX_REQUESTS_PER_RUN)
        or DEFAULT_MAX_REQUESTS_PER_RUN
    )
    return RequestBudget(limit)


def raise_if_exhausted() -> None:
    budget = current()
    if budget is not None and budget.exhausted:
        raise BudgetExhausted(
            f"LLM request budget used up ({budget.limit} requests this run) — "
            "finishing the run with what exists (stages are resumable)"
        )
