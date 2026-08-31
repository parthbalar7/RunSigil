from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from runsigil_control_api.models import (
    ActionBudgetReservation,
    Budget,
    BudgetReservation,
    BudgetScope,
)

SUPPORTED_RESOURCES = frozenset(
    {
        "currency:USD",
        "tokens",
        "requests",
        "concurrent_runs",
        "tool_actions",
        "model_calls",
    }
)
RELEASABLE_RESOURCES = frozenset({"concurrent_runs"})


@dataclass(frozen=True)
class BudgetContext:
    organization_id: UUID
    project_id: UUID
    environment_id: UUID
    agent_id: UUID
    actor_id: UUID
    actor_type: str
    model_route_id: UUID | None = None


def _scope_predicate(context: BudgetContext) -> ColumnElement[bool]:
    predicates = [BudgetScope.scope_type == "organization"]
    predicates.extend(
        [
            and_(
                BudgetScope.scope_type == "project",
                BudgetScope.project_id == context.project_id,
            ),
            and_(
                BudgetScope.scope_type == "environment",
                BudgetScope.environment_id == context.environment_id,
            ),
            and_(
                BudgetScope.scope_type == "agent",
                BudgetScope.agent_id == context.agent_id,
            ),
        ]
    )
    if context.actor_type == "user":
        predicates.append(
            and_(BudgetScope.scope_type == "user", BudgetScope.user_id == context.actor_id)
        )
    if context.model_route_id is not None:
        predicates.append(
            and_(
                BudgetScope.scope_type == "model_route",
                BudgetScope.model_route_id == context.model_route_id,
            )
        )
    return or_(*predicates)


def reserve_budgets(
    session: Session,
    *,
    context: BudgetContext,
    run_id: UUID,
    estimates: Mapping[str, int],
    now: datetime,
    ttl: timedelta = timedelta(minutes=30),
) -> list[BudgetReservation]:
    if not estimates or any(value <= 0 for value in estimates.values()):
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "Budget estimates must contain positive resource quantities.",
            status_code=422,
        )
    unsupported = set(estimates).difference(SUPPORTED_RESOURCES)
    if unsupported:
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "One or more budget resource units are unsupported.",
            status_code=422,
            details={"resource_keys": sorted(unsupported)},
        )

    budgets = list(
        session.scalars(
            select(Budget)
            .join(
                BudgetScope,
                and_(
                    BudgetScope.organization_id == Budget.organization_id,
                    BudgetScope.id == Budget.budget_scope_id,
                ),
            )
            .where(
                Budget.organization_id == context.organization_id,
                Budget.active.is_(True),
                Budget.resource_key.in_(sorted(estimates)),
                _scope_predicate(context),
            )
            .order_by(Budget.id)
            .with_for_update(of=Budget)
        )
    )
    covered = {budget.resource_key for budget in budgets}
    missing = set(estimates).difference(covered)
    if missing:
        raise RunSigilError(
            ErrorCode.BUDGET_EXHAUSTED,
            "Required active budget coverage is unavailable; the provider was not called.",
            status_code=409,
            details={"resource_keys": sorted(missing)},
        )

    for budget in budgets:
        estimate = estimates[budget.resource_key]
        if budget.spent_value + budget.reserved_value + estimate > budget.limit_value:
            raise RunSigilError(
                ErrorCode.BUDGET_EXHAUSTED,
                "An applicable budget is exhausted; the provider was not called.",
                status_code=409,
                details={"resource_key": budget.resource_key},
            )

    reservations: list[BudgetReservation] = []
    for budget in budgets:
        estimate = estimates[budget.resource_key]
        budget.reserved_value += estimate
        reservation = BudgetReservation(
            id=uuid4(),
            organization_id=context.organization_id,
            budget_id=budget.id,
            run_id=run_id,
            resource_key=budget.resource_key,
            estimated_value=estimate,
            actual_value=None,
            status="active",
            expires_at=now + ttl,
            reconciled_at=None,
        )
        session.add(reservation)
        reservations.append(reservation)
    return reservations


def link_action_reservations(
    session: Session,
    *,
    organization_id: UUID,
    action_id: UUID,
    reservations: list[BudgetReservation],
) -> None:
    for reservation in reservations:
        session.add(
            ActionBudgetReservation(
                organization_id=organization_id,
                action_id=action_id,
                budget_reservation_id=reservation.id,
            )
        )


def action_reservations(
    session: Session,
    *,
    organization_id: UUID,
    action_id: UUID,
    lock: bool = False,
) -> list[BudgetReservation]:
    statement = (
        select(BudgetReservation)
        .join(
            ActionBudgetReservation,
            and_(
                ActionBudgetReservation.organization_id == BudgetReservation.organization_id,
                ActionBudgetReservation.budget_reservation_id == BudgetReservation.id,
            ),
        )
        .where(
            ActionBudgetReservation.organization_id == organization_id,
            ActionBudgetReservation.action_id == action_id,
        )
        .order_by(BudgetReservation.id)
    )
    if lock:
        statement = statement.with_for_update(of=BudgetReservation)
    return list(session.scalars(statement))


def release_action_reservations(
    session: Session,
    *,
    organization_id: UUID,
    action_id: UUID,
    now: datetime,
) -> list[BudgetReservation]:
    reservations = action_reservations(
        session,
        organization_id=organization_id,
        action_id=action_id,
        lock=True,
    )
    budgets = {
        budget.id: budget
        for budget in session.scalars(
            select(Budget)
            .where(Budget.id.in_([row.budget_id for row in reservations]))
            .order_by(Budget.id)
            .with_for_update()
        )
    }
    for reservation in reservations:
        if reservation.status != "active":
            continue
        budget = budgets.get(reservation.budget_id)
        if budget is None or budget.reserved_value < reservation.estimated_value:
            raise RunSigilError(
                ErrorCode.INVALID_TRANSITION,
                "A budget reservation cannot be released safely.",
                status_code=409,
            )
        budget.reserved_value -= reservation.estimated_value
        reservation.actual_value = 0
        reservation.status = "released"
        reservation.reconciled_at = now
    return reservations


def settle_action_reservations(
    session: Session,
    *,
    organization_id: UUID,
    action_id: UUID,
    now: datetime,
    committed: bool,
    actual_usage: Mapping[str, int] | None = None,
) -> list[BudgetReservation]:
    reservations = action_reservations(
        session,
        organization_id=organization_id,
        action_id=action_id,
        lock=True,
    )
    budgets = {
        budget.id: budget
        for budget in session.scalars(
            select(Budget)
            .where(Budget.id.in_([row.budget_id for row in reservations]))
            .order_by(Budget.id)
            .with_for_update()
        )
    }
    usage = dict(actual_usage or {})
    for reservation in reservations:
        if reservation.status != "active":
            continue
        budget = budgets.get(reservation.budget_id)
        if budget is None or budget.reserved_value < reservation.estimated_value:
            raise RunSigilError(
                ErrorCode.INVALID_TRANSITION,
                "A budget reservation cannot be reconciled safely.",
                status_code=409,
            )
        budget.reserved_value -= reservation.estimated_value
        actual = usage.get(
            reservation.resource_key,
            reservation.estimated_value if committed else 0,
        )
        if actual < 0:
            raise RunSigilError(
                ErrorCode.INVALID_TRANSITION,
                "Actual budget usage cannot be negative.",
                status_code=409,
            )
        reservation.actual_value = actual
        reservation.reconciled_at = now
        if committed and reservation.resource_key not in RELEASABLE_RESOURCES:
            budget.spent_value += actual
            reservation.status = "committed"
        else:
            reservation.status = "released"
    return reservations
