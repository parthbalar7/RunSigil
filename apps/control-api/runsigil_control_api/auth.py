from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header
from pydantic import BaseModel
from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import text
from sqlalchemy.orm import Session

from runsigil_control_api.database import (
    GatewayAuthorizationSessionLocal,
    SessionLocal,
    set_tenant_context,
)
from runsigil_control_api.settings import get_settings


class AuthContext(BaseModel):
    organization_id: UUID
    api_key_id: UUID
    actor_id: UUID
    actor_type: Literal["user", "service", "workload"]
    scopes: frozenset[str]
    key_hash: str


def _bearer_value(authorization: str | None) -> str:
    if not authorization:
        raise RunSigilError(
            ErrorCode.AUTH_REQUIRED, "Bearer authentication is required.", status_code=401
        )
    scheme, separator, value = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not value.strip():
        raise RunSigilError(
            ErrorCode.AUTH_INVALID, "Bearer authentication is invalid.", status_code=401
        )
    return value.strip()


def get_auth_context(authorization: Annotated[str | None, Header()] = None) -> AuthContext:
    key = _bearer_value(authorization)
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    with SessionLocal() as session:
        session.execute(
            text("SELECT set_config('runsigil.api_key_hash', :key_hash, true)"),
            {"key_hash": key_hash},
        )
        row = (
            session.execute(
                text(
                    "SELECT organization_id, id AS api_key_id, actor_id, actor_type, "
                    "scopes_json AS scopes FROM api_keys "
                    "WHERE key_hash = :key_hash AND active = true "
                    "AND (expires_at IS NULL OR expires_at > clock_timestamp())"
                ),
                {"key_hash": key_hash},
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise RunSigilError(
            ErrorCode.AUTH_INVALID, "The API key is invalid or expired.", status_code=401
        )
    return AuthContext(
        organization_id=row["organization_id"],
        api_key_id=row["api_key_id"],
        actor_id=row["actor_id"],
        actor_type=row["actor_type"],
        scopes=frozenset(row["scopes"] or []),
        key_hash=key_hash,
    )


def require_scopes(*required: str) -> Callable[[AuthContext], AuthContext]:
    def dependency(context: Annotated[AuthContext, Depends(get_auth_context)]) -> AuthContext:
        missing = set(required).difference(context.scopes)
        if missing and "*" not in context.scopes:
            raise RunSigilError(
                ErrorCode.SCOPE_DENIED,
                "The API key does not have the required scope.",
                status_code=403,
                details={"required_scopes": sorted(required)},
            )
        return context

    return dependency


def tenant_session(
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> Generator[Session, None, None]:
    with SessionLocal() as session:
        try:
            session.execute(
                text("SELECT set_config('runsigil.api_key_hash', :key_hash, true)"),
                {"key_hash": context.key_hash},
            )
            set_tenant_context(session, context.organization_id)
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def verify_internal_token(value: str | None, *, expected: str, code: str) -> None:
    if value is None or not hmac.compare_digest(value, expected):
        raise RunSigilError(
            ErrorCode.AUTH_INVALID, f"Invalid {code} service credential.", status_code=401
        )


@contextmanager
def internal_action_session(
    action_id: UUID,
    service_token: str | None,
) -> Generator[Session, None, None]:
    settings = get_settings()
    verify_internal_token(service_token, expected=settings.gateway_service_token, code="gateway")
    with GatewayAuthorizationSessionLocal() as session:
        try:
            organization_id = session.scalar(
                text("SELECT organization_id FROM actions WHERE id = :action_id"),
                {"action_id": action_id},
            )
            if organization_id is None:
                raise RunSigilError(ErrorCode.NOT_FOUND, "Action not found.", status_code=404)
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


@contextmanager
def internal_model_call_session(
    model_call_id: UUID,
    service_token: str | None,
) -> Generator[Session, None, None]:
    settings = get_settings()
    verify_internal_token(service_token, expected=settings.gateway_service_token, code="gateway")
    with GatewayAuthorizationSessionLocal() as session:
        try:
            organization_id = session.scalar(
                text("SELECT organization_id FROM model_calls WHERE id = :model_call_id"),
                {"model_call_id": model_call_id},
            )
            if organization_id is None:
                raise RunSigilError(ErrorCode.NOT_FOUND, "Model call not found.", status_code=404)
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
