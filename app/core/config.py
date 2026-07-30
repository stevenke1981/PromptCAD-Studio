from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="PROMPTCAD_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "PromptCAD Studio"
    env: Literal["development", "test", "production"] = "development"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: Path = Path("generated")

    planner_mode: Literal["auto", "agent", "rule", "llm"] = "auto"
    render_backend: Literal["auto", "cadquery", "openscad", "source_only"] = "auto"
    allow_source_fallback: bool = True
    render_timeout_seconds: int = Field(default=120, ge=5, le=1800)
    max_prompt_chars: int = Field(default=4000, ge=100, le=20000)
    max_jobs_returned: int = Field(default=50, ge=1, le=500)

    api_token: str | None = None
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-5.6"
    llm_structured_mode: Literal["json_schema", "json_object", "prompt_only"] = "json_schema"
    llm_timeout_seconds: int = Field(default=90, ge=5, le=600)
    llm_max_retries: int = Field(default=1, ge=0, le=5)
    llm_fallback_to_rule: bool = True

    @field_validator("api_token", "llm_api_key", mode="before")
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def llm_is_configured(self) -> bool:
        local = self.llm_base_url.startswith(("http://localhost", "http://127.0.0.1", "http://host.docker.internal"))
        return bool(self.llm_model and (self.llm_api_key or local))

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
