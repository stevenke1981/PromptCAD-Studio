from __future__ import annotations

from app.core.config import Settings
from app.services.planners.base import CadPlanner, PlannerError
from app.services.planners.llm import OpenAICompatiblePlanner
from app.services.planners.rule_based import RuleBasedPlanner


class PlannerFactory:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.rule = RuleBasedPlanner()
        self.llm = OpenAICompatiblePlanner(settings)

    def resolve(self, requested: str) -> CadPlanner:
        mode = self.settings.planner_mode if requested == "auto" else requested
        if mode == "rule":
            return self.rule
        if mode == "llm":
            return self.llm
        if mode == "auto":
            return self.llm if self.settings.llm_is_configured else self.rule
        raise PlannerError(f"Unknown planner: {requested}")

    async def plan(self, prompt: str, requested: str):
        planner = self.resolve(requested)
        try:
            return await planner.plan(prompt), planner.name
        except PlannerError:
            if planner is self.llm and self.settings.llm_fallback_to_rule:
                return await self.rule.plan(prompt), f"{self.rule.name}-fallback"
            raise
