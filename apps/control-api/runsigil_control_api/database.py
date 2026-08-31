from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from runsigil_control_api.settings import get_settings


def build_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    return create_engine(url or settings.database_url, pool_pre_ping=True, future=True)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
gateway_authorization_engine = build_engine(get_settings().gateway_authorization_database_url)
GatewayAuthorizationSessionLocal = sessionmaker(
    bind=gateway_authorization_engine, expire_on_commit=False, autoflush=False
)


def set_tenant_context(session: Session, organization_id: UUID) -> None:
    session.execute(
        text("SELECT set_config('runsigil.organization_id', :organization_id, true)"),
        {"organization_id": str(organization_id)},
    )


@contextmanager
def tenant_transaction(organization_id: UUID) -> Generator[Session, None, None]:
    with SessionLocal() as session, session.begin():
        set_tenant_context(session, organization_id)
        yield session
