from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.services.planners.base import CadPlanner, PlannerError
from app.services.planners.intent import LLMIntent, intent_to_document

_SYSTEM_PROMPT = """You are a mechanical CAD planning compiler.
Convert the user's Chinese or English request into the supplied JSON schema.
All dimensions must be millimeters. Do not emit Python, shell commands, markdown, or paths.
Choose exactly one supported template: plate, cylinder, ring, l_bracket, enclosure.
Use null for parameters that were not provided. Put every inferred/defaulted choice in assumptions.
For M-thread holes: choose tapped only when the user explicitly asks for thread/tapping; otherwise choose clearance.
Coordinates use the part center as XY origin and bottom as Z=0.
Set review_required=true for ambiguity, inferred dimensions, unsafe geometry, or low confidence.
If the request cannot be represented, choose the closest template, explain limitations in notes, and require review.
"""


class OpenAICompatiblePlanner(CadPlanner):
    name = "llm-openai-compatible"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def plan(self, prompt: str):
        if not self.settings.llm_is_configured:
            raise PlannerError("LLM planner is not configured")

        schema = LLMIntent.model_json_schema()
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": 0,
        }
        if self.settings.llm_structured_mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "cad_intent", "strict": True, "schema": schema},
            }
        elif self.settings.llm_structured_mode == "json_object":
            messages[0]["content"] += " Return one JSON object only."
            payload["response_format"] = {"type": "json_object"}
        else:
            messages[0]["content"] += " Return one JSON object matching this schema: " + json.dumps(schema)

        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"

        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                content = self._extract_content(data)
                intent = LLMIntent.model_validate_json(self._strip_fences(content))
                return intent_to_document(intent, prompt, self.name)
            except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                if attempt < self.settings.llm_max_retries:
                    await asyncio.sleep(min(2**attempt, 4))
        raise PlannerError(f"LLM planning failed: {last_error}")

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        message = data["choices"][0]["message"]
        if message.get("refusal"):
            raise PlannerError(f"LLM refused the request: {message['refusal']}")
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [part.get("text", "") for part in content if isinstance(part, dict)]
            return "".join(parts)
        raise PlannerError("LLM response did not contain text JSON")

    @staticmethod
    def _strip_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return text.strip()
