from __future__ import annotations

import base64
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from runsigil_telemetry import Operation
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from runsigil_control_api.auth import AuthContext, require_scopes, tenant_session
from runsigil_control_api.models import ApprovalRequest, DeadLetter, EvidenceBundle, Run, TraceEvent
from runsigil_control_api.schemas import (
    ApprovalDecisionInput,
    ContextResponse,
    DeadLetterPage,
    DeadLetterRedriveInput,
    DeadLetterSummary,
    GovernedActionInput,
    RunDetail,
    RunListPage,
)
from runsigil_control_api.services.dead_letters import dead_letter_summary, redrive_dead_letter
from runsigil_control_api.services.governed_actions import (
    cancel_run,
    context_snapshot,
    create_governed_action,
    decide_approval,
    run_detail,
)

router = APIRouter(prefix="/v1")


@router.get("/context", response_model=ContextResponse)
def get_context(
    context: Annotated[AuthContext, Depends(require_scopes("context:read"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    return context_snapshot(session, context)


@router.post("/runs", response_model=RunDetail, status_code=status.HTTP_202_ACCEPTED)
def start_run(
    request: GovernedActionInput,
    context: Annotated[AuthContext, Depends(require_scopes("run:write"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    run = create_governed_action(session, context=context, request=request)
    session.flush()
    return run_detail(session, run.id)


RUN_STATUSES = frozenset(
    {
        "authorizing",
        "waiting_for_approval",
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
        "reconciliation_required",
        "dead_lettered",
    }
)


@router.get("/runs", response_model=RunListPage)
def list_runs(
    _context: Annotated[AuthContext, Depends(require_scopes("run:read"))],
    session: Annotated[Session, Depends(tenant_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
    statuses: Annotated[list[str] | None, Query(alias="status")] = None,
    project_id: UUID | None = None,
    agent_id: UUID | None = None,
    updated_after: datetime | None = None,
    terminal_kind: Literal["cancelled", "rejected"] | None = None,
) -> dict[str, Any]:
    requested_statuses = set(statuses or [])
    invalid_statuses = requested_statuses.difference(RUN_STATUSES)
    if invalid_statuses:
        from runsigil_contracts.errors import ErrorCode, RunSigilError

        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "One or more run statuses are invalid.",
            status_code=422,
            details={"invalid_statuses": sorted(invalid_statuses)},
        )

    filters: list[Any] = []
    if requested_statuses:
        filters.append(Run.status.in_(sorted(requested_statuses)))
    if project_id is not None:
        filters.append(Run.project_id == project_id)
    if agent_id is not None:
        filters.append(Run.agent_id == agent_id)
    if updated_after is not None:
        filters.append(Run.updated_at >= updated_after)
    if terminal_kind is not None:
        approval_rejected = (
            select(TraceEvent.id)
            .where(
                TraceEvent.organization_id == Run.organization_id,
                TraceEvent.run_id == Run.id,
                TraceEvent.event_type == "approval.denied",
            )
            .exists()
        )
        filters.append(approval_rejected if terminal_kind == "rejected" else ~approval_rejected)

    statement = select(Run).where(*filters)
    if cursor:
        try:
            created_at, row_id = _decode_cursor(cursor)
        except (ValueError, UnicodeDecodeError):
            from runsigil_contracts.errors import ErrorCode, RunSigilError

            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED, "The run cursor is invalid.", status_code=422
            ) from None
        statement = statement.where(
            or_(Run.created_at < created_at, and_(Run.created_at == created_at, Run.id < row_id))
        )

    rows = list(
        session.scalars(statement.order_by(Run.created_at.desc(), Run.id.desc()).limit(limit + 1))
    )
    page = rows[:limit]
    total = session.scalar(select(func.count()).select_from(Run).where(*filters)) or 0
    next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
    return {
        "items": [run_detail(session, run.id) for run in page],
        "next_cursor": next_cursor,
        "page_size": len(page),
        "total": total,
    }


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(
    run_id: UUID,
    _context: Annotated[AuthContext, Depends(require_scopes("run:read"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    return run_detail(session, run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunDetail)
def cancel_run_endpoint(
    run_id: UUID,
    context: Annotated[AuthContext, Depends(require_scopes("run:write"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    run = cancel_run(session, context=context, run_id=run_id)
    session.flush()
    return run_detail(session, run.id)


def _encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(value: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
    created, row_id = raw.split("|", 1)
    return datetime.fromisoformat(created), UUID(row_id)


@router.get("/approvals")
def list_approvals(
    _context: Annotated[AuthContext, Depends(require_scopes("approval:read"))],
    session: Annotated[Session, Depends(tenant_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
    approval_status: Annotated[str | None, Query(alias="status")] = "pending",
) -> dict[str, Any]:
    statement = select(ApprovalRequest)
    if approval_status:
        statement = statement.where(ApprovalRequest.status == approval_status)
    if cursor:
        created_at, row_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                ApprovalRequest.created_at < created_at,
                and_(ApprovalRequest.created_at == created_at, ApprovalRequest.id < row_id),
            )
        )
    rows = list(
        session.scalars(
            statement.order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc()).limit(
                limit + 1
            )
        )
    )
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
    return {
        "items": [
            {
                "id": row.id,
                "run_id": row.run_id,
                "status": row.status,
                "risk": row.risk,
                "reason": row.reason,
                "content_digest": row.content_digest,
                "request_preview": row.request_preview_json,
                "expires_at": row.expires_at,
                "created_at": row.created_at,
            }
            for row in page
        ],
        "next_cursor": next_cursor,
    }


@router.post("/approvals/{approval_id}/decision", response_model=RunDetail)
def approval_decision(
    approval_id: UUID,
    request: ApprovalDecisionInput,
    context: Annotated[AuthContext, Depends(require_scopes("approval:decide"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    with Operation(
        "runsigil.approval.decide",
        metric_name="runsigil.approval.decision.duration",
        attributes={
            "runsigil.approval.id": str(approval_id),
            "runsigil.approval.decision": request.decision,
            "runsigil.content_captured": False,
        },
    ):
        run = decide_approval(
            session,
            context=context,
            approval_id=approval_id,
            submitted_digest=request.content_digest,
            decision=request.decision,
            reason=request.reason,
        )
    session.flush()
    return run_detail(session, run.id)


@router.get("/dead-letters", response_model=DeadLetterPage)
def list_dead_letters(
    _context: Annotated[AuthContext, Depends(require_scopes("dlq:read"))],
    session: Annotated[Session, Depends(tenant_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: str | None = None,
    dead_letter_status: Annotated[str | None, Query(alias="status")] = "open",
) -> dict[str, Any]:
    statement = select(DeadLetter)
    if dead_letter_status:
        if dead_letter_status not in {"open", "redriven", "resolved"}:
            from runsigil_contracts.errors import ErrorCode, RunSigilError

            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "The dead-letter status is invalid.",
                status_code=422,
            )
        statement = statement.where(DeadLetter.status == dead_letter_status)
    if cursor:
        try:
            created_at, row_id = _decode_cursor(cursor)
        except (ValueError, UnicodeDecodeError):
            from runsigil_contracts.errors import ErrorCode, RunSigilError

            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "The dead-letter cursor is invalid.",
                status_code=422,
            ) from None
        statement = statement.where(
            or_(
                DeadLetter.created_at < created_at,
                and_(DeadLetter.created_at == created_at, DeadLetter.id < row_id),
            )
        )
    rows = list(
        session.scalars(
            statement.order_by(DeadLetter.created_at.desc(), DeadLetter.id.desc()).limit(limit + 1)
        )
    )
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
    return {"items": [dead_letter_summary(row) for row in page], "next_cursor": next_cursor}


@router.post("/dead-letters/{dead_letter_id}/redrive", response_model=DeadLetterSummary)
def redrive_dead_letter_endpoint(
    dead_letter_id: UUID,
    request: DeadLetterRedriveInput,
    context: Annotated[AuthContext, Depends(require_scopes("dlq:redrive"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, object]:
    row = redrive_dead_letter(
        session,
        context=context,
        dead_letter_id=dead_letter_id,
        expected_version=request.expected_version,
        reason=request.reason,
    )
    session.flush()
    return dead_letter_summary(row)


@router.get("/runs/{run_id}/evidence")
def export_evidence(
    run_id: UUID,
    _context: Annotated[AuthContext, Depends(require_scopes("evidence:read"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    run = session.get(Run, run_id)
    if run is None:
        from runsigil_contracts.errors import ErrorCode, RunSigilError

        raise RunSigilError(ErrorCode.NOT_FOUND, "Run not found.", status_code=404)
    bundle = session.scalar(select(EvidenceBundle).where(EvidenceBundle.run_id == run.id))
    if bundle is None:
        from runsigil_contracts.errors import ErrorCode, RunSigilError

        raise RunSigilError(ErrorCode.NOT_FOUND, "Evidence is not available yet.", status_code=404)
    return {
        "schema_version": 1,
        "manifest": bundle.manifest_json,
        "content_digest": bundle.content_digest,
        "signature_algorithm": bundle.signature_algorithm,
        "signing_key_id": bundle.signing_key_id,
        "public_key_b64": bundle.public_key_b64,
        "signature_b64": bundle.signature_b64,
    }
