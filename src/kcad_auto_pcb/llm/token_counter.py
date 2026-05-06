from __future__ import annotations


class TokenBudget:
    """Track token consumption and enforce budget limits.

    Each pipeline run has a hard budget. LLM calls are pre-checked
    against remaining budget. If budget is exhausted, algorithmic
    fallback is used.
    """

    def __init__(self, budget: int = 10000):
        self.budget = budget
        self.used = 0
        self.calls = 0

    def can_call(self, estimated_tokens: int) -> bool:
        """Check if there's enough budget for an estimated call."""
        return (self.used + estimated_tokens) <= self.budget

    def consume(self, tokens: int):
        """Record token consumption from a completed call."""
        self.used += tokens
        self.calls += 1

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.budget

    def report(self) -> dict:
        return {
            "budget": self.budget,
            "used": self.used,
            "remaining": self.remaining,
            "calls": self.calls,
            "exhausted": self.exhausted,
        }
