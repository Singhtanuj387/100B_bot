from typing import Dict, Any
from loguru import logger


class CostTracker:
    """Manages token counts, spending accumulation, and safety limits."""

    def __init__(self, config: Dict[str, Any]):
        cost_limits = config.get("cost_limits", {})
        self.max_cost = float(cost_limits.get("max_cost_per_run_usd", cost_limits.get("max_cost_per_day_usd", 2.00)))
        self.max_calls = int(cost_limits.get("max_llm_calls_per_run", 100))

        self.current_cost_usd = 0.0
        self.current_calls = 0

    def add_call(self, cost_usd: float) -> None:
        """Record an LLM call cost."""
        self.current_calls += 1
        self.current_cost_usd += cost_usd

    def is_exceeded(self) -> bool:
        """Check if any cost or call limit has been violated."""
        if self.current_cost_usd >= self.max_cost:
            logger.error(f"Cost cap exceeded: ${self.current_cost_usd:.4f} >= ${self.max_cost:.2f}")
            return True
        if self.current_calls >= self.max_calls:
            logger.error(f"Call limit reached: {self.current_calls} >= {self.max_calls}")
            return True
        return False

    def get_summary(self) -> Dict[str, Any]:
        """Return formatted cost and call statistics."""
        return {
            "calls": self.current_calls,
            "max_calls": self.max_calls,
            "cost_usd": round(self.current_cost_usd, 4),
            "max_cost_usd": self.max_cost,
        }

