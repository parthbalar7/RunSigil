from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUNSIGIL_", env_file=".env", extra="ignore")

    environment: str = "development"
    control_api_url: str = "http://localhost:8000"
    internal_service_token: str = Field(min_length=32)
    gateway_service_token: str = Field(min_length=32)
    demo_provider_url: str = "http://localhost:8090/effects"
    demo_provider_audience: str = "runsigil-demo-provider"
    demo_provider_signing_key: str = Field(min_length=32)
    allow_private_demo_provider: bool = True
    gateway_request_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    gateway_response_max_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    protocol_public_base_url: str = "http://localhost:8080"
    protocol_allowed_origins: str = "http://localhost:8080,http://127.0.0.1:8080"
    protocol_control_response_max_bytes: int = Field(default=1_048_576, ge=65_536, le=8_388_608)
    a2a_blocking_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)

    @property
    def allowed_protocol_origins(self) -> frozenset[str]:
        return frozenset(
            value.strip().rstrip("/")
            for value in self.protocol_allowed_origins.split(",")
            if value.strip()
        )

    @model_validator(mode="after")
    def production_guards(self) -> GatewaySettings:
        public_url = urlsplit(self.protocol_public_base_url)
        if (
            public_url.scheme not in {"http", "https"}
            or not public_url.hostname
            or public_url.username is not None
            or public_url.password is not None
            or public_url.query
            or public_url.fragment
        ):
            raise ValueError("protocol public base URL must be an absolute HTTP(S) origin or path")
        if self.environment.lower() in {"production", "prod"}:
            if not self.demo_provider_url.startswith("https://"):
                raise ValueError("production provider URL must use HTTPS")
            if self.allow_private_demo_provider:
                raise ValueError(
                    "production cannot enable the development private-provider override"
                )
            if len(self.demo_provider_signing_key) < 32:
                raise ValueError("provider signing key must contain at least 32 characters")
            if public_url.scheme != "https":
                raise ValueError("production protocol public base URL must use HTTPS")
            if not self.allowed_protocol_origins:
                raise ValueError("production protocol origins must be explicitly configured")
        return self


@lru_cache
def get_gateway_settings() -> GatewaySettings:
    # Required values are supplied by BaseSettings from RUNSIGIL_* variables.
    return GatewaySettings()  # type: ignore[call-arg]
