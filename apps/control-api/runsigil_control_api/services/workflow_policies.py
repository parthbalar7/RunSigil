from __future__ import annotations

import hmac
from datetime import datetime
from typing import Any, Literal, cast
from uuid import uuid4

from runsigil_contracts import (
    DecisionEffect,
    PolicyContext,
    WorkflowNode,
    canonical_digest,
)
from runsigil_contracts.errors import ErrorCode, RunSigilError
from runsigil_policy import PolicyEvaluationError, evaluate
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    Environment,
    PolicyBundle,
    Run,
    Workflow,
    WorkflowDeployment,
    WorkflowExecution,
    WorkflowPolicyDecision,
    WorkflowVersion,
)
from runsigil_control_api.services.governed_actions import _audit, _trace


def workflow_policy_decision_summary(row: WorkflowPolicyDecision) -> dict[str, Any]:
    return {
        "id": row.id,
        "node_id": row.node_id,
        "sequence": row.sequence,
        "evaluation": row.evaluation,
        "policy_bundle_id": row.policy_bundle_id,
        "effect": row.effect,
        "reason_code": row.reason_code,
        "input_digest": row.input_digest,
        "policy_digest": row.policy_digest,
        "content_digest": row.content_digest,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
    }


def evaluate_workflow_node_policy(
    session: Session,
    *,
    execution: WorkflowExecution,
    run: Run,
    node: WorkflowNode,
    now: datetime,
) -> WorkflowPolicyDecision | None:
    if node.policy_bundle_id is None:
        return None
    bundle = session.get(PolicyBundle, node.policy_bundle_id)
    deployment = session.get(WorkflowDeployment, execution.deployment_id)
    version = session.get(WorkflowVersion, execution.workflow_version_id)
    workflow = session.get(Workflow, version.workflow_id) if version is not None else None
    environment = (
        session.get(Environment, deployment.environment_id) if deployment is not None else None
    )
    if (
        bundle is None
        or bundle.status != "active"
        or deployment is None
        or workflow is None
        or environment is None
        or bundle.project_id != workflow.project_id
        or not hmac.compare_digest(bundle.content_digest, canonical_digest(bundle.document_json))
    ):
        raise PolicyEvaluationError(
            ErrorCode.POLICY_UNAVAILABLE,
            "The workflow node policy is missing, stale, or outside the workflow project.",
            status_code=503,
        )
    risk_value = str(node.config.get("risk", "low"))
    risk = cast(
        Literal["low", "medium", "high", "critical"],
        risk_value if risk_value in {"low", "medium", "high", "critical"} else "low",
    )
    classification_value = str(node.config.get("data_classification", "internal"))
    data_classification = cast(
        Literal["public", "internal", "confidential", "restricted"],
        (
            classification_value
            if classification_value in {"public", "internal", "confidential", "restricted"}
            else "internal"
        ),
    )
    policy_context = PolicyContext(
        action_type="workflow.node.execute",
        resource=f"workflow-node:{node.type.value}",
        environment=environment.environment_type,
        risk=risk,
        data_classification=data_classification,
        actor_type="workload",
        amount_minor=0,
        occurred_at=now,
    )
    stable_context = policy_context.model_dump(mode="json", exclude={"occurred_at"})
    input_digest = canonical_digest(
        {
            "policy_context": stable_context,
            "workflow_execution_id": execution.id,
            "node_id": node.id,
            "sequence": execution.step_count,
            "state_digest": execution.state_digest,
        }
    )
    latest = session.scalar(
        select(WorkflowPolicyDecision)
        .where(
            WorkflowPolicyDecision.workflow_execution_id == execution.id,
            WorkflowPolicyDecision.node_id == node.id,
            WorkflowPolicyDecision.sequence == execution.step_count,
        )
        .order_by(WorkflowPolicyDecision.evaluation.desc())
        .limit(1)
    )
    if (
        latest is not None
        and latest.expires_at > now
        and hmac.compare_digest(latest.policy_digest, bundle.content_digest)
        and hmac.compare_digest(latest.input_digest, input_digest)
    ):
        return latest
    decision = evaluate(bundle.document_json, policy_context)
    if not hmac.compare_digest(decision.policy_digest, bundle.content_digest):
        raise PolicyEvaluationError(
            ErrorCode.POLICY_UNAVAILABLE,
            "The workflow node policy digest does not match its active bundle.",
            status_code=503,
        )
    evaluation_number = (
        session.scalar(
            select(func.coalesce(func.max(WorkflowPolicyDecision.evaluation), 0)).where(
                WorkflowPolicyDecision.workflow_execution_id == execution.id,
                WorkflowPolicyDecision.node_id == node.id,
                WorkflowPolicyDecision.sequence == execution.step_count,
            )
        )
        or 0
    ) + 1
    content_digest = canonical_digest(
        {
            "organization_id": execution.organization_id,
            "workflow_execution_id": execution.id,
            "run_id": execution.run_id,
            "node_id": node.id,
            "sequence": execution.step_count,
            "evaluation": evaluation_number,
            "policy_bundle_id": bundle.id,
            "effect": decision.effect,
            "reason_code": decision.reason_code,
            "input_digest": input_digest,
            "policy_digest": decision.policy_digest,
            "expires_at": decision.expires_at,
        }
    )
    row = WorkflowPolicyDecision(
        id=uuid4(),
        organization_id=execution.organization_id,
        workflow_execution_id=execution.id,
        run_id=execution.run_id,
        node_id=node.id,
        sequence=execution.step_count,
        evaluation=evaluation_number,
        policy_bundle_id=bundle.id,
        effect=decision.effect.value,
        reason_code=decision.reason_code,
        input_digest=input_digest,
        policy_digest=decision.policy_digest,
        content_digest=content_digest,
        expires_at=decision.expires_at,
    )
    session.add(row)
    _trace(
        session,
        organization_id=execution.organization_id,
        run_id=execution.run_id,
        node_id=node.id,
        event_type="workflow.policy_decision",
        status=decision.effect.value,
        attributes={
            "workflow_policy_decision_id": str(row.id),
            "effect": decision.effect.value,
            "reason_code": decision.reason_code,
            "policy_digest": decision.policy_digest,
            "raw_content_captured": False,
        },
    )
    _audit(
        session,
        organization_id=execution.organization_id,
        actor_id=run.actor_id,
        event_type="workflow.policy_decision",
        subject_type="workflow_policy_decision",
        subject_id=row.id,
        content_digest=content_digest,
        metadata={
            "node_id": node.id,
            "effect": decision.effect.value,
            "reason_code": decision.reason_code,
            "raw_content_captured": False,
        },
    )
    return row


def require_executable_policy_effect(row: WorkflowPolicyDecision | None) -> None:
    if row is not None and row.effect != DecisionEffect.ALLOW.value:
        raise RunSigilError(
            ErrorCode.POLICY_DENIED,
            f"Workflow node policy returned non-executable effect '{row.effect}'.",
            status_code=403,
        )
