from __future__ import annotations

from types import MappingProxyType

from app.core.config import Settings
from app.models.api import PlannerCapability
from app.services.planners.base import CadPlanner, PlannerError
from app.services.planners.llm import OpenAICompatiblePlanner
from app.services.planners.rule_based import RuleBasedPlanner
from app.services.planners.standard_agent import StandardAwarePlanner


class PlannerFactory:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.rule = RuleBasedPlanner()
        self.agent = StandardAwarePlanner()
        self.llm = OpenAICompatiblePlanner(settings)
        self._registry = MappingProxyType(
            {
                "rule": self.rule,
                "agent": self.agent,
                "llm": self.llm,
            }
        )

    def resolve(self, requested: str) -> CadPlanner:
        mode = self.settings.planner_mode if requested == "auto" else requested
        if mode == "auto":
            return self.llm if self.settings.llm_is_configured else self.rule
        try:
            return self._registry[mode]
        except KeyError as exc:
            raise PlannerError(f"Unknown planner: {requested}") from exc

    def capabilities(self) -> list[PlannerCapability]:
        return [
            PlannerCapability(
                planner_id="rule",
                version="1",
                available=True,
                input_kind="prompt",
                description="Deterministic bounded prompt parser.",
            ),
            PlannerCapability(
                planner_id="agent",
                version="1",
                available=True,
                input_kind="standard_prompt",
                description="Standards-aware NEMA17 engineering planner.",
            ),
            PlannerCapability(
                planner_id="llm",
                version="1",
                available=self.settings.llm_is_configured,
                input_kind="prompt",
                description="OpenAI-compatible structured-output planner.",
            ),
        ]

    async def plan(self, prompt: str, requested: str):
        effective = self.settings.planner_mode if requested == "auto" else requested
        if effective == "agent" and self.agent.can_handle(prompt):
            return await self.agent.plan(prompt), self.agent.name
        if effective == "auto" and self.agent.can_handle(prompt):
            return await self.agent.plan(prompt), self.agent.name
        planner = self.resolve(requested)
        try:
            return await planner.plan(prompt), planner.name
        except PlannerError:
            if planner is self.llm and self.settings.llm_fallback_to_rule:
                return await self.rule.plan(prompt), f"{self.rule.name}-fallback"
            raise
