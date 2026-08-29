"""Centralised typed configuration (pydantic-settings).

New code should read configuration from :data:`settings` instead of calling
``os.getenv`` directly — missing/invalid values fail fast at startup with a
clear error, and every knob is documented in one place.

Legacy modules still using ``os.getenv`` are migrated incrementally; the
environment variable names are unchanged, so ``.env`` files keep working.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]

_ENV_FILE = BACKEND_DIR.parent / ".env"


class TeachingSettings(BaseSettings):
    """Teaching pipeline knobs (NLI, storage isolation, scoring queue)."""

    model_config = SettingsConfigDict(env_prefix="SIMLAW_", env_file=_ENV_FILE, extra="ignore")

    nli_model_disabled: bool = Field(False, alias="SIMLAW_NLI_MODEL_DISABLED")
    nli_model_name: str = Field("", alias="SIMLAW_NLI_MODEL_NAME")
    teaching_profiles_dir: Path | None = Field(None, alias="SIMLAW_TEACHING_PROFILES_DIR")
    teaching_skill_cards_dir: Path | None = Field(None, alias="SIMLAW_TEACHING_SKILL_CARDS_DIR")
    scoring_db_path: Path | None = Field(None, alias="SIMLAW_SCORING_DB_PATH")
    scoring_workers: int = Field(2, ge=1, le=16, alias="SIMLAW_SCORING_WORKERS")
    scoring_max_attempts: int = Field(3, ge=1, le=10, alias="SIMLAW_SCORING_MAX_ATTEMPTS")
    instant_citation_check: bool = Field(True, alias="SIMLAW_INSTANT_CITATION_CHECK")


class EmbeddingSettings(BaseSettings):
    """Dense retrieval embedding endpoint (OpenAI-compatible /embeddings)."""

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    api_key: str = Field("", alias="EMBEDDING_API_KEY")
    base_url: str = Field("", alias="EMBEDDING_BASE_URL")
    model_name: str = Field("text-embedding-v4", alias="EMBEDDING_MODEL_NAME")

    @property
    def available(self) -> bool:
        return bool(self.api_key.strip())


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    url: str = Field("", alias="DATABASE_URL")
    pool_size: int = Field(5, ge=1, alias="DATABASE_POOL_SIZE")
    max_overflow: int = Field(10, ge=0, alias="DATABASE_MAX_OVERFLOW")


class ModelSettings(BaseSettings):
    """LLM runtime (OpenAI-compatible endpoint used via camel-ai)."""

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    api_key: str = Field("", alias="OPENAI_API_KEY")
    model_name: str = Field("", alias="OPENAI_MODEL_NAME")
    base_url: str = Field("", alias="OPENAI_API_BASE_URL")
    prompt_profile: str = Field("prod", alias="SIMLAW_PROMPT_PROFILE")


@lru_cache(maxsize=1)
def get_settings() -> TeachingSettings:
    """Process-wide cached teaching settings (tests: clear the cache)."""
    return TeachingSettings()


@lru_cache(maxsize=1)
def get_embedding_settings() -> EmbeddingSettings:
    return EmbeddingSettings()


@lru_cache(maxsize=1)
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()


@lru_cache(maxsize=1)
def get_model_settings() -> ModelSettings:
    return ModelSettings()


# Convenient module-level aliases for common reads.
settings = get_settings()
embedding_settings = get_embedding_settings()
database_settings = get_database_settings()
model_settings = get_model_settings()
