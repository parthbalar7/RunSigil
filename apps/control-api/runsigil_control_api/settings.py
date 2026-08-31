from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUNSIGIL_", env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+psycopg://runsigil_app@localhost:5432/runsigil"
    gateway_authorization_database_url: str = (
        "postgresql+psycopg://runsigil_gateway_authorizer@localhost:5432/runsigil"
    )
    demo_provider_audience: str = "runsigil-demo-provider"
    gateway_service_token: str = Field(min_length=32)
    action_encryption_key_b64: str
    approval_ttl_seconds: int = Field(default=900, ge=30, le=86_400)
    content_capture_enabled: bool = False
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"

    @model_validator(mode="after")
    def validate_security(self) -> Settings:
        production = self.environment.lower() in {"production", "prod"}
        if production:
            if not self.database_url.startswith("postgresql+"):
                raise ValueError("production requires PostgreSQL")
            if len(self.gateway_service_token) < 32:
                raise ValueError("production service tokens must contain at least 32 characters")
            if self.content_capture_enabled:
                raise ValueError("Milestone 1 does not support production raw-content capture")
        return self


@lru_cache
def get_settings() -> Settings:
    # Required values are supplied by BaseSettings from RUNSIGIL_* variables.
    return Settings()  # type: ignore[call-arg]


class MigrationSettings(BaseSettings):
    """Credentials used only by the one-shot migration and seed container."""

    model_config = SettingsConfigDict(env_prefix="RUNSIGIL_", env_file=".env", extra="ignore")

    migration_database_url: str = "postgresql+psycopg://runsigil_owner@localhost:5432/runsigil"
    demo_provider_audience: str = "runsigil-demo-provider"


@lru_cache
def get_migration_settings() -> MigrationSettings:
    return MigrationSettings()
