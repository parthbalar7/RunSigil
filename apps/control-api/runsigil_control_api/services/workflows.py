from __future__ import annotations

import hmac
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

from runsigil_contracts import (
    WorkflowDefinition,
    WorkflowNodeType,
    WorkflowValidationResult,
    canonical_digest,
    canonical_json_value,
    validate_workflow_definition,
)
from runsigil_contracts.crypto import decode_aes256_key, open_json, seal_json
from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    Agent,
    AISystem,
    Environment,
    ModelCall,
    ModelRoute,
    OutboxEvent,
    PolicyBundle,
    Project,
    Run,
    RunCheckpoint,
    Tool,
    Workflow,
    WorkflowDeployment,
    WorkflowExecution,
    WorkflowNodeAttempt,
    WorkflowPolicyDecision,
    WorkflowReplay,
    WorkflowSubworkflowCall,
    WorkflowToolCall,
    WorkflowToolSimulationCall,
    WorkflowVersion,
    WorkflowWait,
)
from runsigil_control_api.services.governed_actions import _audit, _trace, database_now
from runsigil_control_api.services.workflow_tools import cancel_pending_workflow_tool_call
from runsigil_control_api.settings import get_settings
from runsigil_control_api.workflow_schemas import (
    WorkflowCreateInput,
    WorkflowDeploymentInput,
    WorkflowForkInput,
    WorkflowRunInput,
    WorkflowVersionCreateInput,
)

if TYPE_CHECKING:
    from runsigil_control_api.auth import AuthContext


class WorkflowCryptoSettings(Protocol):
    action_encryption_key_b64: str


class WorkflowActorContext(Protocol):
    @property
    def organization_id(self) -> UUID: ...

    @property
    def actor_id(self) -> UUID: ...

    @property
    def actor_type(self) -> str: ...


def _execution_aad(
    organization_id: UUID, execution_id: UUID, content_digest: str
) -> dict[str, str]:
    return {
        "organization_id": str(organization_id),
        "workflow_execution_id": str(execution_id),
        "content_digest": content_digest,
    }


def _checkpoint_aad(
    organization_id: UUID, checkpoint_id: UUID, content_digest: str
) -> dict[str, str]:
    return {
        "organization_id": str(organization_id),
        "checkpoint_id": str(checkpoint_id),
        "content_digest": content_digest,
    }


def encrypt_execution_state(
    state: dict[str, Any],
    execution: WorkflowExecution,
    settings: WorkflowCryptoSettings | None = None,
) -> str:
    resolved = settings or get_settings()
    return seal_json(
        state,
        key=decode_aes256_key(resolved.action_encryption_key_b64),
        associated_data=_execution_aad(
            execution.organization_id, execution.id, execution.content_digest
        ),
    )


def decrypt_execution_state(
    execution: WorkflowExecution, settings: WorkflowCryptoSettings | None = None
) -> dict[str, Any]:
    resolved = settings or get_settings()
    state = open_json(
        execution.encrypted_state,
        key=decode_aes256_key(resolved.action_encryption_key_b64),
        associated_data=_execution_aad(
            execution.organization_id, execution.id, execution.content_digest
        ),
    )
    if not isinstance(state, dict) or canonical_digest(state) != execution.state_digest:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The durable workflow state is invalid or has been modified.",
            status_code=409,
        )
    return state


def decrypt_checkpoint_state(
    checkpoint: RunCheckpoint, settings: WorkflowCryptoSettings | None = None
) -> dict[str, Any]:
    resolved = settings or get_settings()
    state = open_json(
        checkpoint.encrypted_state,
        key=decode_aes256_key(resolved.action_encryption_key_b64),
        associated_data=_checkpoint_aad(
            checkpoint.organization_id, checkpoint.id, checkpoint.content_digest
        ),
    )
    if not isinstance(state, dict) or canonical_digest(state) != checkpoint.state_digest:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The durable workflow checkpoint is invalid or has been modified.",
            status_code=409,
        )
    return state


def create_checkpoint(
    session: Session,
    *,
    execution: WorkflowExecution,
    state: dict[str, Any],
    node_id: str,
    parent_checkpoint_id: UUID | None = None,
    settings: WorkflowCryptoSettings | None = None,
) -> RunCheckpoint:
    resolved = settings or get_settings()
    if parent_checkpoint_id is None:
        previous = session.scalar(
            select(RunCheckpoint)
            .where(RunCheckpoint.workflow_execution_id == execution.id)
            .order_by(RunCheckpoint.sequence.desc())
            .limit(1)
        )
        parent_checkpoint_id = previous.id if previous is not None else None
    sequence = execution.step_count
    state_digest = canonical_digest(state)
    content_digest = canonical_digest(
        {
            "organization_id": execution.organization_id,
            "run_id": execution.run_id,
            "workflow_execution_id": execution.id,
            "workflow_version_id": execution.workflow_version_id,
            "sequence": sequence,
            "node_id": node_id,
            "state_digest": state_digest,
            "active_nodes": execution.current_nodes_json,
            "completed_nodes": execution.completed_nodes_json,
            "path": execution.path_json,
            "loop_counts": execution.loop_counts_json,
            "parent_checkpoint_id": parent_checkpoint_id,
        }
    )
    checkpoint_id = uuid4()
    encrypted_state = seal_json(
        state,
        key=decode_aes256_key(resolved.action_encryption_key_b64),
        associated_data=_checkpoint_aad(execution.organization_id, checkpoint_id, content_digest),
    )
    checkpoint = RunCheckpoint(
        id=checkpoint_id,
        organization_id=execution.organization_id,
        workflow_execution_id=execution.id,
        run_id=execution.run_id,
        sequence=sequence,
        node_id=node_id,
        encrypted_state=encrypted_state,
        state_digest=state_digest,
        active_nodes_json=list(execution.current_nodes_json),
        completed_nodes_json=list(execution.completed_nodes_json),
        path_json=list(execution.path_json),
        loop_counts_json=dict(execution.loop_counts_json),
        content_digest=content_digest,
        parent_checkpoint_id=parent_checkpoint_id,
    )
    session.add(checkpoint)
    return checkpoint


def _validation_payload(result: WorkflowValidationResult) -> list[dict[str, Any]]:
    return [issue.model_dump(mode="json") for issue in result.issues]


def workflow_version_summary(row: WorkflowVersion) -> dict[str, Any]:
    definition = WorkflowDefinition.model_validate(row.definition_json)
    validation = validate_workflow_definition(definition)
    return {
        "id": row.id,
        "workflow_id": row.workflow_id,
        "version": row.version,
        "status": row.status,
        "definition": definition,
        "definition_digest": row.definition_digest,
        "validation": validation,
        "created_by": row.created_by,
        "created_at": row.created_at,
    }


def workflow_summary(session: Session, row: Workflow) -> dict[str, Any]:
    latest = session.scalar(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == row.id)
        .order_by(WorkflowVersion.version.desc())
        .limit(1)
    )
    if latest is None:
        raise RuntimeError("workflow has no immutable version")
    return {
        "id": row.id,
        "project_id": row.project_id,
        "slug": row.slug,
        "name": row.name,
        "description": row.description,
        "latest_version": workflow_version_summary(latest),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def create_workflow(
    session: Session, *, context: AuthContext, request: WorkflowCreateInput
) -> Workflow:
    project = session.get(Project, request.project_id)
    if project is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Project not found.", status_code=404)
    existing = session.scalar(
        select(Workflow).where(
            Workflow.project_id == project.id,
            Workflow.slug == request.slug,
        )
    )
    if existing is not None:
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "A workflow with this slug already exists in the project.",
            status_code=409,
        )
    validation = validate_workflow_definition(request.definition)
    workflow = Workflow(
        id=uuid4(),
        organization_id=context.organization_id,
        project_id=project.id,
        slug=request.slug,
        name=request.name,
        description=request.description,
    )
    session.add(workflow)
    session.flush()
    version = WorkflowVersion(
        id=uuid4(),
        organization_id=context.organization_id,
        workflow_id=workflow.id,
        version=1,
        status="validated" if validation.valid else "invalid",
        definition_json=canonical_json_value(request.definition),
        definition_digest=validation.definition_digest,
        validation_json=_validation_payload(validation),
        created_by=context.actor_id,
    )
    session.add(version)
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type="workflow.created",
        subject_type="workflow",
        subject_id=workflow.id,
        content_digest=validation.definition_digest,
        metadata={"version": 1, "valid": validation.valid, "executable": validation.executable},
    )
    return workflow


def create_workflow_version(
    session: Session,
    *,
    context: AuthContext,
    workflow_id: UUID,
    request: WorkflowVersionCreateInput,
) -> WorkflowVersion:
    workflow = session.get(Workflow, workflow_id)
    if workflow is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Workflow not found.", status_code=404)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"runsigil-workflow-version:{workflow.id}"},
    )
    latest = session.scalar(
        select(func.coalesce(func.max(WorkflowVersion.version), 0)).where(
            WorkflowVersion.workflow_id == workflow.id
        )
    )
    next_version = int(latest or 0) + 1
    validation = validate_workflow_definition(request.definition)
    row = WorkflowVersion(
        id=uuid4(),
        organization_id=context.organization_id,
        workflow_id=workflow.id,
        version=next_version,
        status="validated" if validation.valid else "invalid",
        definition_json=canonical_json_value(request.definition),
        definition_digest=validation.definition_digest,
        validation_json=_validation_payload(validation),
        created_by=context.actor_id,
    )
    session.add(row)
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type="workflow.version_created",
        subject_type="workflow_version",
        subject_id=row.id,
        content_digest=row.definition_digest,
        metadata={
            "workflow_id": str(workflow.id),
            "version": next_version,
            "valid": validation.valid,
            "executable": validation.executable,
        },
    )
    return row


def deploy_workflow_version(
    session: Session,
    *,
    context: AuthContext,
    version_id: UUID,
    request: WorkflowDeploymentInput,
) -> WorkflowDeployment:
    version = session.get(WorkflowVersion, version_id)
    environment = session.get(Environment, request.environment_id)
    agent = session.get(Agent, request.agent_id)
    if version is None or environment is None or agent is None:
        raise RunSigilError(
            ErrorCode.NOT_FOUND,
            "Workflow version, environment, or agent not found.",
            status_code=404,
        )
    workflow = session.get(Workflow, version.workflow_id)
    system = session.get(AISystem, agent.system_id)
    if workflow is None or system is None or system.project_id != workflow.project_id:
        raise RunSigilError(
            ErrorCode.NOT_FOUND,
            "The workflow and agent do not belong to the same project.",
            status_code=404,
        )
    definition = WorkflowDefinition.model_validate(version.definition_json)
    validation = validate_workflow_definition(definition, for_deployment=True)
    reference_issues = (
        _policy_reference_issues(
            session,
            root_workflow=workflow,
            definition=definition,
        )
        + _model_route_reference_issues(
            session,
            root_workflow=workflow,
            definition=definition,
        )
        + _tool_reference_issues(
            session,
            definition=definition,
        )
        + _subworkflow_reference_issues(
            session,
            root_version=version,
            root_workflow=workflow,
            definition=definition,
            environment_id=environment.id,
            agent_id=agent.id,
        )
        if validation.executable
        else []
    )
    if not validation.executable or reference_issues:
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "The workflow version is not executable.",
            status_code=422,
            details={"issues": _validation_payload(validation) + reference_issues},
        )
    existing = session.scalar(
        select(WorkflowDeployment).where(
            WorkflowDeployment.workflow_version_id == version.id,
            WorkflowDeployment.environment_id == environment.id,
            WorkflowDeployment.agent_id == agent.id,
        )
    )
    if existing is not None:
        return existing
    prior = list(
        session.scalars(
            select(WorkflowDeployment)
            .join(
                WorkflowVersion,
                WorkflowVersion.id == WorkflowDeployment.workflow_version_id,
            )
            .where(
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowDeployment.environment_id == environment.id,
                WorkflowDeployment.agent_id == agent.id,
                WorkflowDeployment.status == "active",
            )
            .with_for_update(of=WorkflowDeployment)
        )
    )
    for deployment in prior:
        deployment.status = "superseded"
    now = database_now(session)
    deployment = WorkflowDeployment(
        id=uuid4(),
        organization_id=context.organization_id,
        workflow_version_id=version.id,
        environment_id=environment.id,
        agent_id=agent.id,
        status="active",
        deployed_by=context.actor_id,
        deployed_at=now,
    )
    version.status = "deployed"
    session.add(deployment)
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type="workflow.deployed",
        subject_type="workflow_deployment",
        subject_id=deployment.id,
        content_digest=version.definition_digest,
        metadata={
            "workflow_version_id": str(version.id),
            "environment_id": str(environment.id),
            "agent_id": str(agent.id),
        },
    )
    return deployment


def _policy_reference_issues(
    session: Session,
    *,
    root_workflow: Workflow,
    definition: WorkflowDefinition,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for node in definition.nodes:
        if node.policy_bundle_id is None:
            continue
        bundle = session.get(PolicyBundle, node.policy_bundle_id)
        if (
            bundle is None
            or bundle.status != "active"
            or bundle.project_id != root_workflow.project_id
            or not hmac.compare_digest(
                bundle.content_digest,
                canonical_digest(bundle.document_json),
            )
        ):
            issues.append(
                {
                    "code": "workflow_policy_unavailable",
                    "message": (
                        "The node policy bundle must be active, digest-valid, and belong "
                        "to the workflow project."
                    ),
                    "location": f"workflow.nodes.{node.id}.policy_bundle_id",
                    "severity": "error",
                }
            )
    return issues


def _tool_reference_issues(
    session: Session,
    *,
    definition: WorkflowDefinition,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for node in definition.nodes:
        if node.type != WorkflowNodeType.TOOL:
            continue
        try:
            tool_id = UUID(str(node.config["tool_id"]))
        except (KeyError, TypeError, ValueError):
            continue
        tool = session.get(Tool, tool_id)
        if (
            tool is None
            or tool.name != "demo.invoice.send"
            or tool.effect_class != "transactional"
            or tool.connector != "runsigil-demo-provider-v1"
        ):
            issues.append(
                {
                    "code": "workflow_tool_unavailable",
                    "message": (
                        "The tool reference must resolve to the supported transactional "
                        "RunSigil demo-provider catalog entry."
                    ),
                    "location": f"workflow.nodes.{node.id}.config.tool_id",
                    "severity": "error",
                }
            )
    return issues


def _model_route_reference_issues(
    session: Session,
    *,
    root_workflow: Workflow,
    definition: WorkflowDefinition,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for node in definition.nodes:
        if node.type != WorkflowNodeType.AGENT or node.model_route_id is None:
            continue
        route = session.get(ModelRoute, node.model_route_id)
        if (
            route is None
            or route.status != "active"
            or route.project_id != root_workflow.project_id
            or route.provider != "demo"
            or route.model != "demo-governed-model"
        ):
            issues.append(
                {
                    "code": "workflow_model_route_unavailable",
                    "message": (
                        "The agent model route must resolve to the active RunSigil demo "
                        "model route in the workflow project."
                    ),
                    "location": f"workflow.nodes.{node.id}.model_route_id",
                    "severity": "error",
                }
            )
    return issues


def deployment_contains_effectful_tools(
    session: Session,
    deployment: WorkflowDeployment,
) -> bool:
    visited: set[UUID] = set()

    def visit(current: WorkflowDeployment) -> bool:
        version = session.get(WorkflowVersion, current.workflow_version_id)
        if version is None or version.id in visited:
            return False
        visited.add(version.id)
        definition = WorkflowDefinition.model_validate(version.definition_json)
        if any(node.type == WorkflowNodeType.TOOL for node in definition.nodes):
            return True
        for node in definition.nodes:
            if node.type != WorkflowNodeType.SUBWORKFLOW:
                continue
            try:
                child = session.get(WorkflowDeployment, UUID(str(node.config["deployment_id"])))
            except (KeyError, TypeError, ValueError):
                continue
            if child is not None and visit(child):
                return True
        return False

    return visit(deployment)


def deployment_summary(row: WorkflowDeployment) -> dict[str, Any]:
    return {
        "id": row.id,
        "workflow_version_id": row.workflow_version_id,
        "environment_id": row.environment_id,
        "agent_id": row.agent_id,
        "status": row.status,
        "deployed_by": row.deployed_by,
        "deployed_at": row.deployed_at,
    }


def _subworkflow_reference_issues(
    session: Session,
    *,
    root_version: WorkflowVersion,
    root_workflow: Workflow,
    definition: WorkflowDefinition,
    environment_id: UUID,
    agent_id: UUID,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    def visit(
        current: WorkflowDefinition,
        *,
        version_path: tuple[UUID, ...],
        location_prefix: str,
        depth: int,
    ) -> None:
        if depth > 0 and any(
            node.type in {WorkflowNodeType.TOOL, WorkflowNodeType.AGENT} for node in current.nodes
        ):
            issues.append(
                {
                    "code": "nested_external_node_not_supported",
                    "message": (
                        "Referenced subworkflows cannot contain effectful tool or agent "
                        "nodes until descendant external-call cancellation is fenced."
                    ),
                    "location": location_prefix,
                    "severity": "error",
                }
            )
            return
        if depth > 8:
            issues.append(
                {
                    "code": "subworkflow_depth_exceeded",
                    "message": "Referenced subworkflows may be nested at most eight levels.",
                    "location": location_prefix,
                    "severity": "error",
                }
            )
            return
        for node in current.nodes:
            if node.type != WorkflowNodeType.SUBWORKFLOW:
                continue
            location = f"{location_prefix}.nodes.{node.id}"
            try:
                deployment_id = UUID(str(node.config["deployment_id"]))
            except (KeyError, TypeError, ValueError):
                continue
            deployment = session.get(WorkflowDeployment, deployment_id)
            child_version = (
                session.get(WorkflowVersion, deployment.workflow_version_id)
                if deployment is not None
                else None
            )
            child_workflow = (
                session.get(Workflow, child_version.workflow_id)
                if child_version is not None
                else None
            )
            if (
                deployment is None
                or deployment.status != "active"
                or child_version is None
                or child_workflow is None
            ):
                issues.append(
                    {
                        "code": "subworkflow_deployment_unavailable",
                        "message": "The referenced subworkflow deployment is not active.",
                        "location": f"{location}.config.deployment_id",
                        "severity": "error",
                    }
                )
                continue
            if (
                child_workflow.project_id != root_workflow.project_id
                or deployment.environment_id != environment_id
                or deployment.agent_id != agent_id
            ):
                issues.append(
                    {
                        "code": "subworkflow_scope_mismatch",
                        "message": (
                            "The referenced subworkflow must use the same project, "
                            "environment, and agent as its parent deployment."
                        ),
                        "location": f"{location}.config.deployment_id",
                        "severity": "error",
                    }
                )
                continue
            if child_version.id in version_path:
                issues.append(
                    {
                        "code": "subworkflow_reference_cycle",
                        "message": "Referenced subworkflow deployments cannot form a cycle.",
                        "location": f"{location}.config.deployment_id",
                        "severity": "error",
                    }
                )
                continue
            child_definition = WorkflowDefinition.model_validate(child_version.definition_json)
            if (
                canonical_digest(child_definition) != child_version.definition_digest
                or not validate_workflow_definition(
                    child_definition, for_deployment=True
                ).executable
            ):
                issues.append(
                    {
                        "code": "subworkflow_definition_invalid",
                        "message": "The referenced subworkflow definition is no longer valid.",
                        "location": f"{location}.config.deployment_id",
                        "severity": "error",
                    }
                )
                continue
            visit(
                child_definition,
                version_path=version_path + (child_version.id,),
                location_prefix=location,
                depth=depth + 1,
            )

    visit(
        definition,
        version_path=(root_version.id,),
        location_prefix="workflow",
        depth=0,
    )
    return issues


def _create_execution_records(
    session: Session,
    *,
    context: WorkflowActorContext,
    deployment: WorkflowDeployment,
    state: dict[str, Any],
    idempotency_key: str,
    evaluation_id: UUID | None = None,
    evaluation_scenario_id: UUID | None = None,
    forked_from_checkpoint_id: UUID | None = None,
    initial_nodes: list[str] | None = None,
    completed_nodes: list[str] | None = None,
    path: list[str] | None = None,
    loop_counts: dict[str, int] | None = None,
    execution_purpose: str = "run",
    execution_mode: str = "live",
    simulation_profile_id: UUID | None = None,
    settings: WorkflowCryptoSettings | None = None,
) -> Run:
    resolved = settings or get_settings()
    version = session.get(WorkflowVersion, deployment.workflow_version_id)
    if version is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The deployed workflow version is unavailable.",
            status_code=409,
        )
    workflow = session.get(Workflow, version.workflow_id)
    if workflow is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The deployed workflow is unavailable.",
            status_code=409,
        )
    definition = WorkflowDefinition.model_validate(version.definition_json)
    state_digest = canonical_digest(state)
    input_digest = canonical_digest(
        {
            "deployment_id": deployment.id,
            "workflow_version_id": version.id,
            "definition_digest": version.definition_digest,
            "state_digest": state_digest,
            "forked_from_checkpoint_id": forked_from_checkpoint_id,
            "evaluation_id": evaluation_id,
            "evaluation_scenario_id": evaluation_scenario_id,
            "execution_purpose": execution_purpose,
            "execution_mode": execution_mode,
            "simulation_profile_id": simulation_profile_id,
        }
    )
    existing = session.scalar(select(Run).where(Run.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.run_kind != "workflow" or not hmac.compare_digest(
            existing.input_digest, input_digest
        ):
            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "The idempotency key belongs to different workflow content.",
                status_code=409,
            )
        return existing
    now = database_now(session)
    run_id = uuid4()
    execution_id = uuid4()
    active_nodes = list(initial_nodes if initial_nodes is not None else [definition.entry_node_id])
    content_digest = canonical_digest(
        {
            "organization_id": context.organization_id,
            "run_id": run_id,
            "workflow_execution_id": execution_id,
            "workflow_version_id": version.id,
            "deployment_id": deployment.id,
            "definition_digest": version.definition_digest,
            "input_digest": input_digest,
            "execution_purpose": execution_purpose,
            "execution_mode": execution_mode,
            "simulation_profile_id": simulation_profile_id,
        }
    )
    run = Run(
        id=run_id,
        organization_id=context.organization_id,
        project_id=workflow.project_id,
        environment_id=deployment.environment_id,
        agent_id=deployment.agent_id,
        actor_id=context.actor_id,
        actor_type=context.actor_type,
        run_kind="workflow",
        status="queued",
        idempotency_key=idempotency_key,
        input_digest=input_digest,
        active_node=sorted(active_nodes)[0] if active_nodes else None,
    )
    execution = WorkflowExecution(
        id=execution_id,
        organization_id=context.organization_id,
        run_id=run.id,
        workflow_version_id=version.id,
        deployment_id=deployment.id,
        evaluation_id=evaluation_id,
        evaluation_scenario_id=evaluation_scenario_id,
        forked_from_checkpoint_id=forked_from_checkpoint_id,
        execution_mode=execution_mode,
        simulation_profile_id=simulation_profile_id,
        status="queued",
        version=1,
        content_digest=content_digest,
        encrypted_state="pending",
        state_digest=state_digest,
        current_nodes_json=active_nodes,
        completed_nodes_json=list(completed_nodes or []),
        path_json=list(path or []),
        loop_counts_json=dict(loop_counts or {}),
        step_count=len(path or []),
        max_steps=definition.limits.max_steps,
        deadline_at=now + timedelta(seconds=definition.limits.max_duration_seconds),
    )
    execution.encrypted_state = encrypt_execution_state(state, execution, resolved)
    session.add_all([run, execution])
    session.flush()
    create_checkpoint(
        session,
        execution=execution,
        state=state,
        node_id="__fork__" if forked_from_checkpoint_id else "__start__",
        parent_checkpoint_id=forked_from_checkpoint_id,
        settings=resolved,
    )
    session.add(
        OutboxEvent(
            id=uuid4(),
            organization_id=context.organization_id,
            topic="workflow.ready",
            aggregate_type="workflow_execution",
            aggregate_id=execution.id,
            deduplication_key=f"workflow.ready:{execution.id}:1",
            payload_json={
                "workflow_execution_id": str(execution.id),
                "content_digest": content_digest,
            },
            available_at=now,
            attempts=0,
        )
    )
    _trace(
        session,
        organization_id=context.organization_id,
        run_id=run.id,
        node_id=run.active_node or "workflow",
        event_type="workflow.queued",
        status="queued",
        attributes={
            "workflow_execution_id": str(execution.id),
            "workflow_version_id": str(version.id),
            "definition_digest": version.definition_digest,
            "state_digest": state_digest,
            "raw_content_captured": False,
            "execution_purpose": execution_purpose,
            "execution_mode": execution_mode,
            "simulation_profile_id": (
                str(simulation_profile_id) if simulation_profile_id else None
            ),
        },
    )
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type="workflow.run_created",
        subject_type="run",
        subject_id=run.id,
        content_digest=content_digest,
        metadata={
            "workflow_execution_id": str(execution.id),
            "workflow_version_id": str(version.id),
            "forked_from_checkpoint_id": (
                str(forked_from_checkpoint_id) if forked_from_checkpoint_id else None
            ),
            "raw_content_captured": False,
            "execution_purpose": execution_purpose,
            "execution_mode": execution_mode,
            "simulation_profile_id": (
                str(simulation_profile_id) if simulation_profile_id else None
            ),
        },
    )
    return run


def start_workflow_run(
    session: Session,
    *,
    context: AuthContext,
    deployment_id: UUID,
    request: WorkflowRunInput,
) -> Run:
    deployment = session.get(WorkflowDeployment, deployment_id)
    if deployment is None or deployment.status != "active":
        raise RunSigilError(
            ErrorCode.NOT_FOUND, "Active workflow deployment not found.", status_code=404
        )
    return _create_execution_records(
        session,
        context=context,
        deployment=deployment,
        state=request.input,
        idempotency_key=request.idempotency_key,
    )


def cancel_workflow_run(
    session: Session,
    *,
    context: WorkflowActorContext,
    run: Run,
) -> Run:
    execution = session.scalar(
        select(WorkflowExecution).where(WorkflowExecution.run_id == run.id).with_for_update()
    )
    if execution is None or execution.status not in {"queued", "waiting"}:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "Only an idle queued or waiting workflow can be cancelled safely.",
            status_code=409,
        )
    now = database_now(session)
    model_calls = list(
        session.scalars(select(ModelCall).where(ModelCall.workflow_execution_id == execution.id))
    )
    if model_calls:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "A workflow cannot be cancelled after an agent model call is queued.",
            status_code=409,
        )
    tool_calls = list(
        session.scalars(
            select(WorkflowToolCall)
            .where(WorkflowToolCall.workflow_execution_id == execution.id)
            .with_for_update()
        )
    )
    if any(call.status != "pending_approval" for call in tool_calls):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "A workflow cannot be cancelled after a tool effect became dispatchable.",
            status_code=409,
        )
    for tool_call in tool_calls:
        cancel_pending_workflow_tool_call(
            session,
            call=tool_call,
            actor_id=context.actor_id,
            now=now,
        )
    waits = list(
        session.scalars(
            select(WorkflowWait)
            .where(
                WorkflowWait.workflow_execution_id == execution.id,
                WorkflowWait.status == "pending",
            )
            .with_for_update()
        )
    )
    for wait in waits:
        wait.status = "cancelled"
        wait.resolution = "cancelled"
        wait.resolved_at = now
    subworkflow_calls = list(
        session.scalars(
            select(WorkflowSubworkflowCall)
            .where(
                WorkflowSubworkflowCall.parent_workflow_execution_id == execution.id,
                WorkflowSubworkflowCall.status == "pending",
            )
            .with_for_update()
        )
    )
    for call in subworkflow_calls:
        call.status = "cancelled"
        call.resolved_at = now
        session.add(
            OutboxEvent(
                id=uuid4(),
                organization_id=execution.organization_id,
                topic="workflow.ready",
                aggregate_type="workflow_execution",
                aggregate_id=call.child_workflow_execution_id,
                deduplication_key=f"subworkflow.call:{call.id}:cancel-child",
                payload_json={
                    "workflow_execution_id": str(call.child_workflow_execution_id),
                    "parent_subworkflow_call_id": str(call.id),
                    "content_digest": call.content_digest,
                },
                available_at=now,
                attempts=0,
            )
        )
    execution.status = "cancelled"
    execution.error_code = "workflow_cancelled"
    execution.completed_at = now
    execution.claim_token_hash = None
    execution.lease_expires_at = None
    execution.version += 1
    run.status = "cancelled"
    run.error_code = "workflow_cancelled"
    run.active_node = None
    run.completed_at = now
    session.add(
        OutboxEvent(
            id=uuid4(),
            organization_id=execution.organization_id,
            topic="workflow.finalize",
            aggregate_type="workflow_execution",
            aggregate_id=execution.id,
            deduplication_key=f"workflow.finalize:{execution.id}:cancelled",
            payload_json={
                "workflow_execution_id": str(execution.id),
                "content_digest": execution.content_digest,
            },
            available_at=now,
            attempts=0,
        )
    )
    _trace(
        session,
        organization_id=execution.organization_id,
        run_id=run.id,
        node_id="workflow",
        event_type="workflow.cancelled",
        status="cancelled",
        attributes={
            "workflow_execution_id": str(execution.id),
            "cancelled_wait_count": len(waits),
            "cancelled_subworkflow_count": len(subworkflow_calls),
            "cancelled_tool_call_count": len(tool_calls),
            "side_effect_started": False,
            "raw_content_captured": False,
        },
    )
    _audit(
        session,
        organization_id=execution.organization_id,
        actor_id=context.actor_id,
        event_type="workflow.cancelled",
        subject_type="run",
        subject_id=run.id,
        content_digest=execution.content_digest,
        metadata={
            "workflow_execution_id": str(execution.id),
            "cancelled_wait_count": len(waits),
            "cancelled_subworkflow_count": len(subworkflow_calls),
            "cancelled_tool_call_count": len(tool_calls),
            "side_effect_started": False,
        },
    )
    return run


def fork_workflow_run(
    session: Session,
    *,
    context: AuthContext,
    run_id: UUID,
    request: WorkflowForkInput,
) -> Run:
    execution = session.scalar(select(WorkflowExecution).where(WorkflowExecution.run_id == run_id))
    checkpoint = session.get(RunCheckpoint, request.checkpoint_id)
    if (
        execution is None
        or checkpoint is None
        or checkpoint.workflow_execution_id != execution.id
        or checkpoint.run_id != run_id
    ):
        raise RunSigilError(
            ErrorCode.NOT_FOUND, "Workflow checkpoint not found for this run.", status_code=404
        )
    deployment = session.get(WorkflowDeployment, execution.deployment_id)
    if deployment is None or deployment.status not in {"active", "superseded"}:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The checkpoint deployment is unavailable.",
            status_code=409,
        )
    from runsigil_control_api.services.workflow_simulation import require_simulation_profile

    profile = require_simulation_profile(
        session,
        deployment=deployment,
        profile_id=request.simulation_profile_id,
    )
    if not checkpoint.active_nodes_json:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "A terminal checkpoint has no remaining execution to fork.",
            status_code=409,
        )
    state = decrypt_checkpoint_state(checkpoint)
    return _create_execution_records(
        session,
        context=context,
        deployment=deployment,
        state=state,
        idempotency_key=request.idempotency_key,
        forked_from_checkpoint_id=checkpoint.id,
        initial_nodes=checkpoint.active_nodes_json,
        completed_nodes=checkpoint.completed_nodes_json,
        path=checkpoint.path_json,
        loop_counts=checkpoint.loop_counts_json,
        execution_purpose="fork",
        execution_mode="simulation" if profile is not None else "live",
        simulation_profile_id=profile.id if profile is not None else None,
    )


def workflow_execution_summary(session: Session, execution: WorkflowExecution) -> dict[str, Any]:
    attempts = list(
        session.scalars(
            select(WorkflowNodeAttempt)
            .where(WorkflowNodeAttempt.workflow_execution_id == execution.id)
            .order_by(WorkflowNodeAttempt.created_at, WorkflowNodeAttempt.id)
        )
    )
    checkpoints = list(
        session.scalars(
            select(RunCheckpoint)
            .where(RunCheckpoint.workflow_execution_id == execution.id)
            .order_by(RunCheckpoint.sequence, RunCheckpoint.created_at)
        )
    )
    waits = list(
        session.scalars(
            select(WorkflowWait)
            .where(WorkflowWait.workflow_execution_id == execution.id)
            .order_by(WorkflowWait.sequence, WorkflowWait.created_at, WorkflowWait.id)
        )
    )
    subworkflows = list(
        session.scalars(
            select(WorkflowSubworkflowCall)
            .where(WorkflowSubworkflowCall.parent_workflow_execution_id == execution.id)
            .order_by(
                WorkflowSubworkflowCall.sequence,
                WorkflowSubworkflowCall.created_at,
                WorkflowSubworkflowCall.id,
            )
        )
    )
    tool_calls = list(
        session.scalars(
            select(WorkflowToolCall)
            .where(WorkflowToolCall.workflow_execution_id == execution.id)
            .order_by(
                WorkflowToolCall.sequence,
                WorkflowToolCall.created_at,
                WorkflowToolCall.id,
            )
        )
    )
    tool_simulations = list(
        session.scalars(
            select(WorkflowToolSimulationCall)
            .where(WorkflowToolSimulationCall.workflow_execution_id == execution.id)
            .order_by(
                WorkflowToolSimulationCall.sequence,
                WorkflowToolSimulationCall.created_at,
                WorkflowToolSimulationCall.id,
            )
        )
    )
    model_calls = list(
        session.scalars(
            select(ModelCall)
            .where(ModelCall.workflow_execution_id == execution.id)
            .order_by(ModelCall.sequence, ModelCall.created_at, ModelCall.id)
        )
    )
    policy_decisions = list(
        session.scalars(
            select(WorkflowPolicyDecision)
            .where(WorkflowPolicyDecision.workflow_execution_id == execution.id)
            .order_by(
                WorkflowPolicyDecision.sequence,
                WorkflowPolicyDecision.evaluation,
                WorkflowPolicyDecision.id,
            )
        )
    )
    replay = session.scalar(
        select(WorkflowReplay).where(WorkflowReplay.replay_workflow_execution_id == execution.id)
    )
    from runsigil_control_api.services.workflow_models import model_call_summary
    from runsigil_control_api.services.workflow_policies import (
        workflow_policy_decision_summary,
    )
    from runsigil_control_api.services.workflow_replays import workflow_replay_summary
    from runsigil_control_api.services.workflow_simulation import tool_simulation_call_summary
    from runsigil_control_api.services.workflow_subworkflows import subworkflow_call_summary
    from runsigil_control_api.services.workflow_tools import workflow_tool_call_summary
    from runsigil_control_api.services.workflow_waits import workflow_wait_summary

    return {
        "id": execution.id,
        "workflow_version_id": execution.workflow_version_id,
        "deployment_id": execution.deployment_id,
        "execution_mode": execution.execution_mode,
        "simulation_profile_id": execution.simulation_profile_id,
        "status": execution.status,
        "content_digest": execution.content_digest,
        "state_digest": execution.state_digest,
        "current_nodes": execution.current_nodes_json,
        "completed_nodes": execution.completed_nodes_json,
        "path": execution.path_json,
        "step_count": execution.step_count,
        "max_steps": execution.max_steps,
        "deadline_at": execution.deadline_at,
        "forked_from_checkpoint_id": execution.forked_from_checkpoint_id,
        "error_code": execution.error_code,
        "attempts": [
            {
                "id": row.id,
                "node_id": row.node_id,
                "node_type": row.node_type,
                "attempt": row.attempt,
                "status": row.status,
                "input_digest": row.input_digest,
                "output_digest": row.output_digest,
                "started_at": row.started_at,
                "completed_at": row.completed_at,
                "error_code": row.error_code,
            }
            for row in attempts
        ],
        "checkpoints": [
            {
                "id": row.id,
                "sequence": row.sequence,
                "node_id": row.node_id,
                "state_digest": row.state_digest,
                "active_nodes": row.active_nodes_json,
                "completed_nodes": row.completed_nodes_json,
                "path": row.path_json,
                "content_digest": row.content_digest,
                "parent_checkpoint_id": row.parent_checkpoint_id,
                "created_at": row.created_at,
            }
            for row in checkpoints
        ],
        "waits": [workflow_wait_summary(row) for row in waits],
        "subworkflows": [subworkflow_call_summary(row) for row in subworkflows],
        "tool_calls": [workflow_tool_call_summary(row) for row in tool_calls],
        "tool_simulations": [tool_simulation_call_summary(row) for row in tool_simulations],
        "model_calls": [model_call_summary(row) for row in model_calls],
        "policy_decisions": [workflow_policy_decision_summary(row) for row in policy_decisions],
        "replay": workflow_replay_summary(replay) if replay is not None else None,
    }
