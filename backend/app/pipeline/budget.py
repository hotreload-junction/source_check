"""Time and cost budget of ONE analysis (goals: <= ~120 s, <= ~0.6 USD per article).

The claim checks (extraction, search, verification of the claims' own sources) are never cut: they are the result. What can
be skipped is the EXTRA depth: following what the sources cite (depth >= 2) and searching for a source a passage names. When the
time or the LLM spend is used up, those stages do not start (or, for a running hop, are cut to the time that is left), and
`audit.skipped` lists what was left out, so a shallower result is never mistaken for a complete one. The first hop (depth 2) is
only ever cut by the time budget; the cost budget stops the extras beyond it (chain search, hops from depth 3).

Cost = what the LLM calls of this analysis cost (llm.Usage). Exa and Context.dev are not metered yet.
"""
from __future__ import annotations

import threading
import time

from ..config import Settings
from ..llm import LLMClient, Usage


class RunBudget:
    def __init__(self, settings: Settings, llm: LLMClient | None, t0: float | None = None):
        self.s = settings
        self.llm = llm if hasattr(llm, "usage_since") else None
        self.t0 = time.monotonic() if t0 is None else t0
        self.base = self.llm.usage_snapshot() if self.llm else None
        self.skipped: list[str] = []
        self._lock = threading.Lock()

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def time_left(self) -> float:
        return max(self.s.time_budget_s - self.elapsed(), 0.0) if self.s.time_budget_s > 0 else float("inf")

    def usage(self) -> Usage:
        return self.llm.usage_since(self.base) if self.llm else Usage()

    def over(self) -> str | None:
        """Why the extras must not start now ('time' | 'cost'), or None."""
        if self.s.time_budget_s > 0 and self.elapsed() >= self.s.time_budget_s:
            return "time"
        if self.s.cost_budget_usd > 0 and self.usage().usd >= self.s.cost_budget_usd:
            return "cost"
        return None

    def skip(self, stage: str, why: str) -> None:
        note = f"{stage} ({why} budget)"
        with self._lock:
            if note not in self.skipped:
                self.skipped.append(note)

    def allow(self, stage: str) -> bool:
        """True when `stage` may start. Otherwise it is recorded as skipped."""
        why = self.over()
        if why:
            self.skip(stage, why)
            return False
        return True

    def allow_first_hop(self) -> bool:
        """The first source-chain hop (what the claims' sources cite) is the point of the product: only a time budget that is
        really used up stops it, never the cost budget."""
        if self.s.time_budget_s > 0 and self.elapsed() >= self.s.time_budget_s:
            self.skip("source chain", "time")
            return False
        return True

    def allow_chain_search(self) -> bool:
        if not self.allow("chain search"):
            return False
        if self.s.chain_start_limit_s > 0 and self.elapsed() >= self.s.chain_start_limit_s:
            self.skip("chain search", "time")
            return False
        return True
