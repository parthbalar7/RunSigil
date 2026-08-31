from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUNSIGIL_", env_file=".env", extra="ignore")

    environment: str = "development"
    worker_database_url: str = "postgresql+psycopg://runsigil_worker@localhost:5432/runsigil"
    gateway_url: str = "http://localhost:8080"
    internal_service_token: str = Field(min_length=32)
    action_encryption_key_b64: str
    evidence_ed25519_private_key_b64: str
    evidence_signing_key_id: str = "development-2026-01"
    action_lease_seconds: int = Field(default=30, ge=5, le=3_600)

    @model_validator(mode="after")
    def production_guards(self) -> WorkerSettings:
        if self.environment.lower() in {"production", "prod"}:
            if self.gateway_url.startswith("http://"):
                raise ValueError("production worker-to-gateway transport must use HTTPS")
            if len(self.internal_service_token) < 32:
                raise ValueError("production service token must contain at least 32 characters")
        return self


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()  # type: ignore[call-arg]
