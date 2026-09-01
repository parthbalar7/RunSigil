from __future__ import annotations

import hmac
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from runsigil_contracts import WorkflowNode, canonical_digest
from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import select
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    Project,
    Run,
    Tool,
    Workflow,
    WorkflowDeployment,
    WorkflowExecution,
    WorkflowSimulationProfile,
    WorkflowToolSimulationCall,
    WorkflowVersion,
)
from runsigil_control_api.schemas import GovernedActionInput
from runsigil_control_api.services.governed_actions import TOOL_NAME, _audit, _trace
from runsigil_control_api.services.workflow_tools import tool_document
from runsigil_control_api.workflow_schemas import WorkflowSimulationProfileCreateInput

if TYPE_CHECKING:
    from runsigil_control_api.auth import AuthContext

SIMULATION_PROVIDER = "runsigil-deterministic-tool-simulator-v1"
SIMULATION_CONTRACT_VERSION = "1"


def simulation_profile_summary(row: WorkflowSimulationProfile) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "tool_id": row.tool_id,
        "name": row.name,
        "provider": row.provider,
        "contract_version": row.contract_version,
        "status": row.status,
        "content_digest": row.content_digest,
        "created_by": row.created_by,
        "created_at": row.created_at,
    }


def tool_simulation_call_summary(row: WorkflowToolSimulationCall) -> dict[str, Any]:
    return {
        "id": row.id,
        "workflow_execution_id": row.workflow_execution_id,
        "run_id": row.run_id,
        "simulation_profile_id": row.simulation_profile_id,
        "tool_id": row.tool_id,
        "node_id": row.node_id,
        "sequence": row.sequence,
        "result_state_key": row.result_state_key,
        "status": row.status,
        "arguments_digest": row.arguments_digest,
        "tool_digest": row.tool_digest,
        "profile_digest": row.profile_digest,
        "result_digest": row.result_digest,
        "content_digest": row.content_digest,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
    }


def create_simulation_profile(
    session: Session,
    *,
    context: AuthContext,
    request: WorkflowSimulationProfileCreateInput,
) -> WorkflowSimulationProfile:
    project = session.get(Project, request.project_id)
    tool = session.get(Tool, request.tool_id)
    if (
        project is None
        or tool is None
        or tool.name != TOOL_NAME
        or tool.effect_class != "transactional"
        or tool.connector != "runsigil-demo-provider-v1"
    ):
        raise RunSigilError(
            ErrorCode.NOT_FOUND,
            "The simulation project or supported tool was not found.",
            status_code=404,
        )
    content_digest = canonical_digest(
        {
            "organization_id": context.organization_id,
            "project_id": project.id,
            "tool_id": tool.id,
            "tool_digest": canonical_digest(tool_document(tool)),
            "name": request.name,
            "provider": SIMULATION_PROVIDER,
            "contract_version": SIMULATION_CONTRACT_VERSION,
        }
    )
    existing = session.scalar(
        select(WorkflowSimulationProfile).where(
            WorkflowSimulationProfile.project_id == project.id,
            WorkflowSimulationProfile.name == request.name,
        )
    )
    if existing is not None:
        if not hmac.compare_digest(existing.content_digest, content_digest):
            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "The simulation profile name belongs to different immutable content.",
                status_code=409,
            )
        return existing
    profile = WorkflowSimulationProfile(
        id=uuid4(),
        organization_id=context.organization_id,
        project_id=project.id,
        tool_id=tool.id,
        name=request.name,
        provider=SIMULATION_PROVIDER,
        contract_version=SIMULATION_CONTRACT_VERSION,
        status="active",
        content_digest=content_digest,
        created_by=context.actor_id,
    )
    session.add(profile)
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type="workflow.simulation_profile_created",
        subject_type="workflow_simulation_profile",
        subject_id=profile.id,
        content_digest=profile.content_digest,
        metadata={
            "project_id": str(project.id),
            "tool_id": str(tool.id),
            "provider": profile.provider,
            "raw_content_captured": False,
        },
    )
    return profile


def require_simulation_profile(
    session: Session,
    *,
    deployment: WorkflowDeployment,
    profile_id: UUID | None,
) -> WorkflowSimulationProfile | None:
    from runsigil_control_api.services.workflows import deployment_contains_effectful_tools

    effectful = deployment_contains_effectful_tools(session, deployment)
    if not effectful:
        if profile_id is not None:
            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "A simulation profile is valid only for a deployment containing tools.",
                status_code=422,
            )
        return None
    if profile_id is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "Effectful workflow execution requires an explicit simulation profile.",
            status_code=409,
        )
    profile = session.get(WorkflowSimulationProfile, profile_id)
    version = session.get(WorkflowVersion, deployment.workflow_version_id)
    workflow = session.get(Workflow, version.workflow_id) if version is not None else None
    if (
        profile is None
        or profile.status != "active"
        or profile.provider != SIMULATION_PROVIDER
        or profile.contract_version != SIMULATION_CONTRACT_VERSION
        or workflow is None
        or profile.project_id != workflow.project_id
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The simulation profile is unavailable or outside the workflow project.",
            status_code=409,
        )
    tool = session.get(Tool, profile.tool_id)
    if (
        tool is None
        or tool.name != TOOL_NAME
        or not hmac.compare_digest(
            profile.content_digest,
            canonical_digest(
                {
                    "organization_id": profile.organization_id,
                    "project_id": profile.project_id,
                    "tool_id": profile.tool_id,
                    "tool_digest": canonical_digest(tool_document(tool)),
                    "name": profile.name,
                    "provider": profile.provider,
                    "contract_version": profile.contract_version,
                }
            ),
        )
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The simulation profile content digest is stale or invalid.",
            status_code=409,
        )
    return profile


def deterministic_tool_simulation_result(
    *,
    arguments_digest: str,
    tool_digest: str,
    profile_digest: str,
) -> dict[str, Any]:
    return {
        "outcome": "simulated",
        "arguments_digest": arguments_digest,
        "tool_digest": tool_digest,
        "simulation_profile_digest": profile_digest,
        "receipt_preview": {
            "status": "simulated",
            "side_effect_performed": False,
        },
    }


def simulate_workflow_tool_call(
    session: Session,
    *,
    execution: WorkflowExecution,
    run: Run,
    node: WorkflowNode,
    state: dict[str, Any],
    now: datetime,
) -> tuple[WorkflowToolSimulationCall, dict[str, Any]]:
    if execution.execution_mode != "simulation" or execution.simulation_profile_id is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "A live workflow cannot use the simulation executor.",
            status_code=409,
        )
    profile = session.get(WorkflowSimulationProfile, execution.simulation_profile_id)
    tool_id = UUID(str(node.config["tool_id"]))
    tool = session.get(Tool, tool_id)
    if (
        profile is None
        or profile.status != "active"
        or profile.tool_id != tool_id
        or tool is None
        or tool.name != TOOL_NAME
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The tool simulation lineage is unavailable.",
            status_code=409,
        )
    raw_arguments = state.get(str(node.config["arguments_state_key"]))
    if not isinstance(raw_arguments, dict):
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "The simulated tool arguments state key must contain an object.",
            status_code=422,
        )
    try:
        GovernedActionInput.model_validate(
            {
                **raw_arguments,
                "project_id": run.project_id,
                "environment_id": run.environment_id,
                "agent_id": run.agent_id,
                "idempotency_key": (
                    f"workflow-tool-simulation:{execution.id}:{node.id}:{execution.step_count}"
                ),
            }
        )
    except ValidationError as exc:
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "The encrypted workflow state does not satisfy the simulated tool contract.",
            status_code=422,
            details={"issues": exc.errors(include_input=False, include_url=False)},
        ) from exc
    arguments_digest = canonical_digest(raw_arguments)
    tool_digest = canonical_digest(tool_document(tool))
    result = deterministic_tool_simulation_result(
        arguments_digest=arguments_digest,
        tool_digest=tool_digest,
        profile_digest=profile.content_digest,
    )
    result_digest = canonical_digest(result)
    content_digest = canonical_digest(
        {
            "organization_id": execution.organization_id,
            "workflow_execution_id": execution.id,
            "run_id": execution.run_id,
            "simulation_profile_id": profile.id,
            "profile_digest": profile.content_digest,
            "tool_id": tool.id,
            "tool_digest": tool_digest,
            "node_id": node.id,
            "sequence": execution.step_count,
            "arguments_digest": arguments_digest,
            "result_state_key": node.config["result_state_key"],
            "result_digest": result_digest,
        }
    )
    call = WorkflowToolSimulationCall(
        id=uuid4(),
        organization_id=execution.organization_id,
        workflow_execution_id=execution.id,
        run_id=execution.run_id,
        simulation_profile_id=profile.id,
        tool_id=tool.id,
        node_id=node.id,
        sequence=execution.step_count,
        result_state_key=str(node.config["result_state_key"]),
        status="completed",
        arguments_digest=arguments_digest,
        tool_digest=tool_digest,
        profile_digest=profile.content_digest,
        result_digest=result_digest,
        content_digest=content_digest,
        completed_at=now,
    )
    session.add(call)
    _trace(
        session,
        organization_id=execution.organization_id,
        run_id=execution.run_id,
        node_id=node.id,
        event_type="workflow.tool_simulated",
        status="completed",
        attributes={
            "workflow_tool_simulation_call_id": str(call.id),
            "simulation_profile_id": str(profile.id),
            "tool_id": str(tool.id),
            "arguments_digest": arguments_digest,
            "result_digest": result_digest,
            "side_effect_performed": False,
            "raw_content_captured": False,
        },
    )
    _audit(
        session,
        organization_id=execution.organization_id,
        actor_id=run.actor_id,
        event_type="workflow.tool_simulated",
        subject_type="workflow_tool_simulation_call",
        subject_id=call.id,
        content_digest=call.content_digest,
        metadata={
            "workflow_execution_id": str(execution.id),
            "simulation_profile_id": str(profile.id),
            "result_digest": result_digest,
            "side_effect_performed": False,
            "raw_content_captured": False,
        },
    )
    return call, result
