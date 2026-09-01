from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from runsigil_contracts import (
    WorkflowDefinition,
    WorkflowValidationResult,
    validate_workflow_definition,
)
from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import select
from sqlalchemy.orm import Session

from runsigil_control_api.auth import AuthContext, require_scopes, tenant_session
from runsigil_control_api.models import (
    Evaluation,
    EvaluationDataset,
    Workflow,
    WorkflowSimulationProfile,
    WorkflowVersion,
    WorkflowWait,
)
from runsigil_control_api.schemas import RunDetail
from runsigil_control_api.services.evaluations import (
    create_evaluation_annotation,
    create_evaluation_dataset,
    evaluation_annotation_summary,
    evaluation_dataset_summary,
    evaluation_summary,
    start_evaluation,
)
from runsigil_control_api.services.governed_actions import run_detail
from runsigil_control_api.services.workflow_replays import create_workflow_replay
from runsigil_control_api.services.workflow_simulation import (
    create_simulation_profile,
    simulation_profile_summary,
)
from runsigil_control_api.services.workflow_waits import (
    resolve_workflow_wait,
    workflow_wait_summary,
)
from runsigil_control_api.services.workflows import (
    create_workflow,
    create_workflow_version,
    deploy_workflow_version,
    deployment_summary,
    fork_workflow_run,
    start_workflow_run,
    workflow_summary,
    workflow_version_summary,
)
from runsigil_control_api.workflow_schemas import (
    EvaluationAnnotationCreateInput,
    EvaluationAnnotationSummary,
    EvaluationCreateInput,
    EvaluationDatasetCreateInput,
    EvaluationDatasetSummary,
    EvaluationSummary,
    WorkflowCreateInput,
    WorkflowDeploymentInput,
    WorkflowDeploymentSummary,
    WorkflowForkInput,
    WorkflowReplayInput,
    WorkflowRunInput,
    WorkflowSimulationProfileCreateInput,
    WorkflowSimulationProfileSummary,
    WorkflowSummary,
    WorkflowVersionCreateInput,
    WorkflowVersionSummary,
    WorkflowWaitDecisionInput,
    WorkflowWaitEventInput,
    WorkflowWaitInformationInput,
    WorkflowWaitSummary,
)

router = APIRouter(prefix="/v1")


@router.post(
    "/workflow-simulation-profiles",
    response_model=WorkflowSimulationProfileSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_workflow_simulation_profile_endpoint(
    request: WorkflowSimulationProfileCreateInput,
    context: Annotated[AuthContext, Depends(require_scopes("workflow:write"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    row = create_simulation_profile(session, context=context, request=request)
    session.flush()
    return simulation_profile_summary(row)


@router.get(
    "/workflow-simulation-profiles",
    response_model=list[WorkflowSimulationProfileSummary],
)
def list_workflow_simulation_profiles(
    project_id: UUID,
    _context: Annotated[AuthContext, Depends(require_scopes("workflow:read"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(WorkflowSimulationProfile)
        .where(WorkflowSimulationProfile.project_id == project_id)
        .order_by(WorkflowSimulationProfile.created_at, WorkflowSimulationProfile.id)
    )
    return [simulation_profile_summary(row) for row in rows]


@router.post("/workflow-definitions/validate", response_model=WorkflowValidationResult)
def validate_workflow(
    definition: WorkflowDefinition,
    _context: Annotated[AuthContext, Depends(require_scopes("workflow:write"))],
) -> WorkflowValidationResult:
    return validate_workflow_definition(definition, for_deployment=True)


@router.post(
    "/workflows",
    response_model=WorkflowSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_workflow_endpoint(
    request: WorkflowCreateInput,
    context: Annotated[AuthContext, Depends(require_scopes("workflow:write"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    row = create_workflow(session, context=context, request=request)
    session.flush()
    return workflow_summary(session, row)


@router.get("/workflows/{workflow_id}", response_model=WorkflowSummary)
def get_workflow(
    workflow_id: UUID,
    _context: Annotated[AuthContext, Depends(require_scopes("workflow:read"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    row = session.get(Workflow, workflow_id)
    if row is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Workflow not found.", status_code=404)
    return workflow_summary(session, row)


@router.post(
    "/workflows/{workflow_id}/versions",
    response_model=WorkflowVersionSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_workflow_version_endpoint(
    workflow_id: UUID,
    request: WorkflowVersionCreateInput,
    context: Annotated[AuthContext, Depends(require_scopes("workflow:write"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    row = create_workflow_version(
        session,
        context=context,
        workflow_id=workflow_id,
        request=request,
    )
    session.flush()
    return workflow_version_summary(row)


@router.get("/workflow-versions/{version_id}", response_model=WorkflowVersionSummary)
def get_workflow_version(
    version_id: UUID,
    _context: Annotated[AuthContext, Depends(require_scopes("workflow:read"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    row = session.get(WorkflowVersion, version_id)
    if row is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Workflow version not found.", status_code=404)
    return workflow_version_summary(row)


@router.post(
    "/workflow-versions/{version_id}/deployments",
    response_model=WorkflowDeploymentSummary,
    status_code=status.HTTP_201_CREATED,
)
def deploy_workflow_endpoint(
    version_id: UUID,
    request: WorkflowDeploymentInput,
    context: Annotated[AuthContext, Depends(require_scopes("workflow:deploy"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    row = deploy_workflow_version(
        session,
        context=context,
        version_id=version_id,
        request=request,
    )
    session.flush()
    return deployment_summary(row)


@router.post(
    "/workflow-deployments/{deployment_id}/runs",
    response_model=RunDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_workflow_endpoint(
    deployment_id: UUID,
    request: WorkflowRunInput,
    context: Annotated[AuthContext, Depends(require_scopes("workflow:run"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    run = start_workflow_run(
        session,
        context=context,
        deployment_id=deployment_id,
        request=request,
    )
    session.flush()
    return run_detail(session, run.id)


@router.post(
    "/workflow-runs/{run_id}/forks",
    response_model=RunDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def fork_workflow_endpoint(
    run_id: UUID,
    request: WorkflowForkInput,
    context: Annotated[AuthContext, Depends(require_scopes("workflow:run"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    run = fork_workflow_run(
        session,
        context=context,
        run_id=run_id,
        request=request,
    )
    session.flush()
    return run_detail(session, run.id)


@router.post(
    "/workflow-runs/{run_id}/replays",
    response_model=RunDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def replay_workflow_endpoint(
    run_id: UUID,
    request: WorkflowReplayInput,
    context: Annotated[AuthContext, Depends(require_scopes("workflow:run"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    run = create_workflow_replay(
        session,
        context=context,
        source_run_id=run_id,
        request=request,
    )
    session.flush()
    return run_detail(session, run.id)


@router.get("/workflow-waits/{wait_id}", response_model=WorkflowWaitSummary)
def get_workflow_wait(
    wait_id: UUID,
    _context: Annotated[AuthContext, Depends(require_scopes("workflow:read"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    wait = session.get(WorkflowWait, wait_id)
    if wait is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Workflow wait not found.", status_code=404)
    return workflow_wait_summary(wait)


@router.post("/workflow-waits/{wait_id}/decision", response_model=WorkflowWaitSummary)
def decide_workflow_wait(
    wait_id: UUID,
    request: WorkflowWaitDecisionInput,
    context: Annotated[AuthContext, Depends(require_scopes("approval:decide"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    wait = resolve_workflow_wait(
        session,
        context=context,
        wait_id=wait_id,
        expected_type="approval",
        submitted_content_digest=request.content_digest,
        resolution=request.decision,
    )
    session.flush()
    return workflow_wait_summary(wait)


@router.post("/workflow-waits/{wait_id}/information", response_model=WorkflowWaitSummary)
def provide_workflow_information(
    wait_id: UUID,
    request: WorkflowWaitInformationInput,
    context: Annotated[AuthContext, Depends(require_scopes("workflow:signal"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    wait = resolve_workflow_wait(
        session,
        context=context,
        wait_id=wait_id,
        expected_type="request_information",
        submitted_content_digest=request.content_digest,
        resolution="received",
        payload=request.information,
    )
    session.flush()
    return workflow_wait_summary(wait)


@router.post("/workflow-waits/{wait_id}/event", response_model=WorkflowWaitSummary)
def publish_workflow_event(
    wait_id: UUID,
    request: WorkflowWaitEventInput,
    context: Annotated[AuthContext, Depends(require_scopes("workflow:signal"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    wait = resolve_workflow_wait(
        session,
        context=context,
        wait_id=wait_id,
        expected_type="event",
        submitted_content_digest=request.content_digest,
        resolution="received",
        payload=request.payload,
        event_key=request.event_key,
    )
    session.flush()
    return workflow_wait_summary(wait)


@router.post(
    "/evaluation-datasets",
    response_model=EvaluationDatasetSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_evaluation_dataset_endpoint(
    request: EvaluationDatasetCreateInput,
    context: Annotated[AuthContext, Depends(require_scopes("evaluation:write"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    row = create_evaluation_dataset(session, context=context, request=request)
    session.flush()
    return evaluation_dataset_summary(session, row)


@router.get("/evaluation-datasets/{dataset_id}", response_model=EvaluationDatasetSummary)
def get_evaluation_dataset(
    dataset_id: UUID,
    _context: Annotated[AuthContext, Depends(require_scopes("evaluation:read"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    row = session.get(EvaluationDataset, dataset_id)
    if row is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Evaluation dataset not found.", status_code=404)
    return evaluation_dataset_summary(session, row)


@router.post(
    "/evaluations",
    response_model=EvaluationSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_evaluation_endpoint(
    request: EvaluationCreateInput,
    context: Annotated[AuthContext, Depends(require_scopes("evaluation:run"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    row = start_evaluation(session, context=context, request=request)
    session.flush()
    return evaluation_summary(session, row)


@router.get("/evaluations/{evaluation_id}", response_model=EvaluationSummary)
def get_evaluation(
    evaluation_id: UUID,
    _context: Annotated[AuthContext, Depends(require_scopes("evaluation:read"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    row = session.get(Evaluation, evaluation_id)
    if row is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Evaluation not found.", status_code=404)
    return evaluation_summary(session, row)


@router.post(
    "/evaluation-results/{result_id}/annotations",
    response_model=EvaluationAnnotationSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_evaluation_annotation_endpoint(
    result_id: UUID,
    request: EvaluationAnnotationCreateInput,
    context: Annotated[AuthContext, Depends(require_scopes("evaluation:write"))],
    session: Annotated[Session, Depends(tenant_session)],
) -> dict[str, Any]:
    row = create_evaluation_annotation(
        session,
        context=context,
        result_id=result_id,
        request=request,
    )
    session.flush()
    return evaluation_annotation_summary(row)
