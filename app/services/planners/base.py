from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.cad import CadDocument


class PlannerError(RuntimeError):
    pass


class CadPlanner(ABC):
    name: str

    @abstractmethod
    async def plan(self, prompt: str) -> CadDocument:
        raise NotImplementedError
