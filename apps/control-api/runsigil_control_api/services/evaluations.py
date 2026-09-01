from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

from runsigil_contracts import canonical_digest, canonical_json_value
from runsigil_contracts.crypto import decode_aes256_key, open_json, seal_json
from runsigil_contracts.errors import ErrorCode, RunSigilError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from runsigil_control_api.models import (
    Evaluation,
    EvaluationAnnotation,
    EvaluationDataset,
    EvaluationDatasetVersion,
    EvaluationResult,
    EvaluationScenario,
    Project,
    Workflow,
    WorkflowDeployment,
    WorkflowExecution,
    WorkflowPolicyDecision,
    WorkflowVersion,
)
from runsigil_control_api.services.governed_actions import _audit, _trace, database_now
from runsigil_control_api.services.workflow_simulation import require_simulation_profile
from runsigil_control_api.services.workflows import _create_execution_records
from runsigil_control_api.settings import get_settings
from runsigil_control_api.workflow_schemas import (
    EvaluationAnnotationCreateInput,
    EvaluationCreateInput,
    EvaluationDatasetCreateInput,
)

if TYPE_CHECKING:
    from runsigil_control_api.auth import AuthContext


class EvaluationCryptoSettings(Protocol):
    action_encryption_key_b64: str


def _scenario_aad(organization_id: UUID, scenario_id: UUID, content_digest: str) -> dict[str, str]:
    return {
        "organization_id": str(organization_id),
        "evaluation_scenario_id": str(scenario_id),
        "content_digest": content_digest,
    }


def _decrypt_scenario(
    scenario: EvaluationScenario,
    settings: EvaluationCryptoSettings | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    resolved = settings or get_settings()
    payload = open_json(
        scenario.encrypted_payload,
        key=decode_aes256_key(resolved.action_encryption_key_b64),
        associated_data=_scenario_aad(
            scenario.organization_id, scenario.id, scenario.content_digest
        ),
    )
    if not isinstance(payload, dict):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The evaluation scenario payload is invalid.",
            status_code=409,
        )
    input_value = payload.get("input")
    expected_output = payload.get("expected_output")
    assertions = payload.get("assertions", {})
    if (
        not isinstance(input_value, dict)
        or not isinstance(expected_output, dict)
        or not isinstance(assertions, dict)
        or canonical_digest(input_value) != scenario.input_digest
        or canonical_digest(expected_output) != scenario.expected_output_digest
    ):
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The evaluation scenario payload does not match its immutable digests.",
            status_code=409,
        )
    return input_value, expected_output, assertions


def create_evaluation_dataset(
    session: Session,
    *,
    context: AuthContext,
    request: EvaluationDatasetCreateInput,
) -> EvaluationDataset:
    project = session.get(Project, request.project_id)
    if project is None:
        raise RunSigilError(ErrorCode.NOT_FOUND, "Project not found.", status_code=404)
    existing = session.scalar(
        select(EvaluationDataset).where(
            EvaluationDataset.project_id == project.id,
            EvaluationDataset.slug == request.slug,
        )
    )
    if existing is not None:
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "An evaluation dataset with this slug already exists in the project.",
            status_code=409,
        )
    keys = [scenario.key for scenario in request.scenarios]
    if len(keys) != len(set(keys)):
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "Evaluation scenario keys must be unique.",
            status_code=422,
        )
    for scenario in request.scenarios:
        required = scenario.assertions.required_policy_nodes
        forbidden = scenario.assertions.forbidden_nodes
        if len(required) != len(set(required)) or len(forbidden) != len(set(forbidden)):
            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "Evaluation assertion node identifiers must be unique.",
                status_code=422,
            )
        if set(required) & set(forbidden):
            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "A node cannot be both policy-required and forbidden.",
                status_code=422,
            )
    dataset = EvaluationDataset(
        id=uuid4(),
        organization_id=context.organization_id,
        project_id=project.id,
        slug=request.slug,
        name=request.name,
        description=request.description,
    )
    session.add(dataset)
    session.flush()
    scenario_documents = [
        {
            "key": scenario.key,
            "name": scenario.name,
            "input_digest": canonical_digest(scenario.input),
            "expected_output_digest": canonical_digest(scenario.expected_output),
            "expected_path": scenario.expected_path,
            "metadata": scenario.metadata,
            "assertions": scenario.assertions,
        }
        for scenario in request.scenarios
    ]
    version_digest = canonical_digest(
        {
            "dataset_id": dataset.id,
            "version": 1,
            "scenarios": scenario_documents,
        }
    )
    version = EvaluationDatasetVersion(
        id=uuid4(),
        organization_id=context.organization_id,
        dataset_id=dataset.id,
        version=1,
        status="active",
        content_digest=version_digest,
        scenario_count=len(request.scenarios),
        created_by=context.actor_id,
    )
    session.add(version)
    session.flush()
    key = decode_aes256_key(get_settings().action_encryption_key_b64)
    for scenario, document in zip(request.scenarios, scenario_documents, strict=True):
        scenario_id = uuid4()
        content_digest = canonical_digest(
            {
                "dataset_version_id": version.id,
                **document,
            }
        )
        encrypted_payload = seal_json(
            {
                "input": scenario.input,
                "expected_output": scenario.expected_output,
                "assertions": scenario.assertions.model_dump(mode="json"),
            },
            key=key,
            associated_data=_scenario_aad(context.organization_id, scenario_id, content_digest),
        )
        session.add(
            EvaluationScenario(
                id=scenario_id,
                organization_id=context.organization_id,
                dataset_version_id=version.id,
                scenario_key=scenario.key,
                name=scenario.name,
                encrypted_payload=encrypted_payload,
                input_digest=document["input_digest"],
                expected_output_digest=document["expected_output_digest"],
                expected_path_json=scenario.expected_path,
                metadata_json=canonical_json_value(scenario.metadata),
                content_digest=content_digest,
            )
        )
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type="evaluation.dataset_created",
        subject_type="evaluation_dataset",
        subject_id=dataset.id,
        content_digest=version_digest,
        metadata={
            "dataset_version_id": str(version.id),
            "scenario_count": len(request.scenarios),
            "raw_content_captured": False,
        },
    )
    return dataset


def evaluation_dataset_summary(session: Session, dataset: EvaluationDataset) -> dict[str, Any]:
    version = session.scalar(
        select(EvaluationDatasetVersion)
        .where(EvaluationDatasetVersion.dataset_id == dataset.id)
        .order_by(EvaluationDatasetVersion.version.desc())
        .limit(1)
    )
    if version is None:
        raise RuntimeError("evaluation dataset has no immutable version")
    return {
        "id": dataset.id,
        "project_id": dataset.project_id,
        "slug": dataset.slug,
        "name": dataset.name,
        "description": dataset.description,
        "version_id": version.id,
        "version": version.version,
        "scenario_count": version.scenario_count,
        "content_digest": version.content_digest,
        "created_at": dataset.created_at,
    }


def start_evaluation(
    session: Session,
    *,
    context: AuthContext,
    request: EvaluationCreateInput,
) -> Evaluation:
    deployment = session.get(WorkflowDeployment, request.deployment_id)
    dataset_version = session.get(EvaluationDatasetVersion, request.dataset_version_id)
    if deployment is None or deployment.status != "active" or dataset_version is None:
        raise RunSigilError(
            ErrorCode.NOT_FOUND,
            "Active workflow deployment or evaluation dataset version not found.",
            status_code=404,
        )
    profile = require_simulation_profile(
        session,
        deployment=deployment,
        profile_id=request.simulation_profile_id,
    )
    workflow_version = session.get(WorkflowVersion, deployment.workflow_version_id)
    dataset = session.get(EvaluationDataset, dataset_version.dataset_id)
    workflow = (
        session.get(Workflow, workflow_version.workflow_id)
        if workflow_version is not None
        else None
    )
    if (
        workflow_version is None
        or workflow is None
        or dataset is None
        or dataset.project_id != workflow.project_id
    ):
        raise RunSigilError(
            ErrorCode.VALIDATION_FAILED,
            "The workflow and dataset do not belong to the same project.",
            status_code=422,
        )
    baseline: Evaluation | None = None
    if request.baseline_evaluation_id is not None:
        baseline = session.get(Evaluation, request.baseline_evaluation_id)
        if (
            baseline is None
            or baseline.status != "completed"
            or baseline.score_milli is None
            or baseline.dataset_version_id != dataset_version.id
            or baseline.simulation_profile_id != (profile.id if profile is not None else None)
        ):
            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "The baseline evaluation must be completed against the same dataset version.",
                status_code=422,
            )
    content_digest = canonical_digest(
        {
            "deployment_id": deployment.id,
            "workflow_version_id": workflow_version.id,
            "workflow_definition_digest": workflow_version.definition_digest,
            "dataset_version_id": dataset_version.id,
            "dataset_digest": dataset_version.content_digest,
            "minimum_score_milli": request.minimum_score_milli,
            "maximum_regression_milli": request.maximum_regression_milli,
            "baseline_evaluation_id": request.baseline_evaluation_id,
            "simulation_profile_id": profile.id if profile is not None else None,
        }
    )
    existing = session.scalar(
        select(Evaluation).where(Evaluation.idempotency_key == request.idempotency_key)
    )
    if existing is not None:
        if existing.content_digest != content_digest:
            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "The idempotency key belongs to different evaluation content.",
                status_code=409,
            )
        return existing
    evaluation = Evaluation(
        id=uuid4(),
        organization_id=context.organization_id,
        workflow_version_id=workflow_version.id,
        dataset_version_id=dataset_version.id,
        deployment_id=deployment.id,
        baseline_evaluation_id=request.baseline_evaluation_id,
        simulation_profile_id=profile.id if profile is not None else None,
        actor_id=context.actor_id,
        idempotency_key=request.idempotency_key,
        status="running",
        minimum_score_milli=request.minimum_score_milli,
        maximum_regression_milli=request.maximum_regression_milli,
        score_milli=None,
        baseline_score_milli=baseline.score_milli if baseline is not None else None,
        score_delta_milli=None,
        regression_status=None,
        release_gate_status="pending",
        content_digest=content_digest,
        completed_at=None,
    )
    session.add(evaluation)
    session.flush()
    scenarios = list(
        session.scalars(
            select(EvaluationScenario)
            .where(EvaluationScenario.dataset_version_id == dataset_version.id)
            .order_by(EvaluationScenario.scenario_key)
        )
    )
    if len(scenarios) != dataset_version.scenario_count or not scenarios:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "The evaluation dataset version is incomplete.",
            status_code=409,
        )
    for scenario in scenarios:
        input_value, _expected, _assertions = _decrypt_scenario(scenario)
        _create_execution_records(
            session,
            context=context,
            deployment=deployment,
            state=input_value,
            idempotency_key=f"evaluation:{evaluation.id}:{scenario.id}",
            evaluation_id=evaluation.id,
            evaluation_scenario_id=scenario.id,
            execution_purpose="evaluation",
            execution_mode="simulation" if profile is not None else "live",
            simulation_profile_id=profile.id if profile is not None else None,
        )
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type="evaluation.started",
        subject_type="evaluation",
        subject_id=evaluation.id,
        content_digest=content_digest,
        metadata={
            "workflow_version_id": str(workflow_version.id),
            "dataset_version_id": str(dataset_version.id),
            "scenario_count": len(scenarios),
            "simulation_profile_id": str(profile.id) if profile is not None else None,
        },
    )
    return evaluation


def settle_evaluation_execution(
    session: Session,
    *,
    execution: WorkflowExecution,
    state: dict[str, Any],
    failed_error: str | None = None,
    settings: EvaluationCryptoSettings | None = None,
) -> None:
    if execution.evaluation_id is None or execution.evaluation_scenario_id is None:
        return
    if (
        session.scalar(
            select(EvaluationResult).where(
                EvaluationResult.evaluation_id == execution.evaluation_id,
                EvaluationResult.scenario_id == execution.evaluation_scenario_id,
            )
        )
        is not None
    ):
        return
    evaluation = session.scalar(
        select(Evaluation).where(Evaluation.id == execution.evaluation_id).with_for_update()
    )
    scenario = session.get(EvaluationScenario, execution.evaluation_scenario_id)
    if evaluation is None or scenario is None:
        raise RunSigilError(
            ErrorCode.INVALID_TRANSITION,
            "Evaluation execution lineage is incomplete.",
            status_code=409,
        )
    task_passed = (
        failed_error is None and canonical_digest(state) == scenario.expected_output_digest
    )
    path_matches = (
        not scenario.expected_path_json or execution.path_json == scenario.expected_path_json
    )
    trajectory_passed = failed_error is None and path_matches
    environment_passed = (
        failed_error is None
        and execution.workflow_version_id == evaluation.workflow_version_id
        and scenario.dataset_version_id == evaluation.dataset_version_id
    )
    _input, _expected, assertions = _decrypt_scenario(scenario, settings)
    required_policy_nodes = set(assertions.get("required_policy_nodes", []))
    forbidden_nodes = set(assertions.get("forbidden_nodes", []))
    maximum_steps = assertions.get("maximum_steps")
    policy_rows = list(
        session.scalars(
            select(WorkflowPolicyDecision).where(
                WorkflowPolicyDecision.workflow_execution_id == execution.id
            )
        )
    )
    allowed_policy_nodes = {row.node_id for row in policy_rows if row.effect == "allow"}
    policy_passed = required_policy_nodes.issubset(allowed_policy_nodes)
    safety_passed = (
        failed_error is None
        and forbidden_nodes.isdisjoint(execution.path_json)
        and (maximum_steps is None or execution.step_count <= int(maximum_steps))
    )
    grader_values = [
        task_passed,
        trajectory_passed,
        environment_passed,
        policy_passed,
        safety_passed,
    ]
    score_milli = sum(1_000 if passed else 0 for passed in grader_values) // len(grader_values)
    now = database_now(session)
    result = EvaluationResult(
        id=uuid4(),
        organization_id=execution.organization_id,
        evaluation_id=evaluation.id,
        scenario_id=scenario.id,
        workflow_execution_id=execution.id,
        run_id=execution.run_id,
        status="passed" if all(grader_values) else "failed",
        score_milli=score_milli,
        task_outcome="passed" if task_passed else "failed",
        trajectory_outcome="passed" if trajectory_passed else "failed",
        deterministic_environment_outcome="passed" if environment_passed else "failed",
        policy_outcome="passed" if policy_passed else "failed",
        safety_outcome="passed" if safety_passed else "failed",
        output_digest=canonical_digest(state),
        trajectory_digest=canonical_digest(execution.path_json),
        graders_json=[
            {"grader": "task_outcome", "score_milli": 1_000 if task_passed else 0},
            {"grader": "trajectory", "score_milli": 1_000 if trajectory_passed else 0},
            {
                "grader": "deterministic_environment",
                "score_milli": 1_000 if environment_passed else 0,
            },
            {"grader": "policy", "score_milli": 1_000 if policy_passed else 0},
            {"grader": "safety", "score_milli": 1_000 if safety_passed else 0},
        ],
    )
    session.add(result)
    session.flush()
    _trace(
        session,
        organization_id=execution.organization_id,
        run_id=execution.run_id,
        node_id="evaluation",
        event_type="evaluation.scenario_completed",
        status=result.status,
        attributes={
            "evaluation_id": str(evaluation.id),
            "scenario_id": str(scenario.id),
            "score_milli": score_milli,
            "raw_content_captured": False,
        },
    )
    expected_count = session.scalar(
        select(func.count())
        .select_from(EvaluationScenario)
        .where(EvaluationScenario.dataset_version_id == evaluation.dataset_version_id)
    )
    results = list(
        session.scalars(
            select(EvaluationResult).where(EvaluationResult.evaluation_id == evaluation.id)
        )
    )
    if expected_count is None or len(results) != expected_count:
        return
    evaluation.score_milli = sum(row.score_milli for row in results) // len(results)
    if evaluation.baseline_evaluation_id is not None:
        baseline = session.get(Evaluation, evaluation.baseline_evaluation_id)
        if baseline is None or baseline.score_milli is None:
            raise RunSigilError(
                ErrorCode.INVALID_TRANSITION,
                "The evaluation baseline is unavailable during settlement.",
                status_code=409,
            )
        evaluation.baseline_score_milli = baseline.score_milli
        evaluation.score_delta_milli = evaluation.score_milli - baseline.score_milli
        evaluation.regression_status = (
            "passed"
            if evaluation.score_delta_milli >= -evaluation.maximum_regression_milli
            else "failed"
        )
    else:
        evaluation.regression_status = "not_configured"
    gate_passed = evaluation.score_milli >= evaluation.minimum_score_milli and (
        evaluation.regression_status in {"passed", "not_configured"}
    )
    evaluation.release_gate_status = "passed" if gate_passed else "failed"
    evaluation.status = "completed"
    evaluation.completed_at = now
    _audit(
        session,
        organization_id=evaluation.organization_id,
        actor_id=evaluation.actor_id,
        event_type="evaluation.completed",
        subject_type="evaluation",
        subject_id=evaluation.id,
        content_digest=evaluation.content_digest,
        metadata={
            "score_milli": evaluation.score_milli,
            "regression_status": evaluation.regression_status,
            "release_gate_status": evaluation.release_gate_status,
            "result_count": len(results),
        },
    )


def evaluation_summary(session: Session, evaluation: Evaluation) -> dict[str, Any]:
    results = list(
        session.scalars(
            select(EvaluationResult)
            .where(EvaluationResult.evaluation_id == evaluation.id)
            .order_by(EvaluationResult.created_at, EvaluationResult.id)
        )
    )
    annotations = list(
        session.scalars(
            select(EvaluationAnnotation)
            .where(EvaluationAnnotation.evaluation_id == evaluation.id)
            .order_by(EvaluationAnnotation.created_at, EvaluationAnnotation.id)
        )
    )
    annotations_by_result: dict[UUID, list[EvaluationAnnotation]] = {}
    for annotation in annotations:
        annotations_by_result.setdefault(annotation.evaluation_result_id, []).append(annotation)
    return {
        "id": evaluation.id,
        "workflow_version_id": evaluation.workflow_version_id,
        "dataset_version_id": evaluation.dataset_version_id,
        "deployment_id": evaluation.deployment_id,
        "baseline_evaluation_id": evaluation.baseline_evaluation_id,
        "simulation_profile_id": evaluation.simulation_profile_id,
        "status": evaluation.status,
        "minimum_score_milli": evaluation.minimum_score_milli,
        "maximum_regression_milli": evaluation.maximum_regression_milli,
        "score_milli": evaluation.score_milli,
        "baseline_score_milli": evaluation.baseline_score_milli,
        "score_delta_milli": evaluation.score_delta_milli,
        "regression_status": evaluation.regression_status,
        "release_gate_status": evaluation.release_gate_status,
        "content_digest": evaluation.content_digest,
        "completed_at": evaluation.completed_at,
        "results": [
            {
                "id": row.id,
                "scenario_id": row.scenario_id,
                "run_id": row.run_id,
                "status": row.status,
                "score_milli": row.score_milli,
                "task_outcome": row.task_outcome,
                "trajectory_outcome": row.trajectory_outcome,
                "deterministic_environment_outcome": row.deterministic_environment_outcome,
                "policy_outcome": row.policy_outcome,
                "safety_outcome": row.safety_outcome,
                "output_digest": row.output_digest,
                "trajectory_digest": row.trajectory_digest,
                "graders": row.graders_json,
                "annotations": [
                    evaluation_annotation_summary(annotation)
                    for annotation in annotations_by_result.get(row.id, [])
                ],
            }
            for row in results
        ],
    }


def evaluation_annotation_summary(annotation: EvaluationAnnotation) -> dict[str, Any]:
    return {
        "id": annotation.id,
        "evaluation_result_id": annotation.evaluation_result_id,
        "evaluation_id": annotation.evaluation_id,
        "scenario_id": annotation.scenario_id,
        "run_id": annotation.run_id,
        "reviewer_id": annotation.reviewer_id,
        "label": annotation.label,
        "score_milli": annotation.score_milli,
        "reason_codes": annotation.reason_codes_json,
        "content_digest": annotation.content_digest,
        "created_at": annotation.created_at,
    }


def create_evaluation_annotation(
    session: Session,
    *,
    context: AuthContext,
    result_id: UUID,
    request: EvaluationAnnotationCreateInput,
) -> EvaluationAnnotation:
    result = session.get(EvaluationResult, result_id)
    if result is None:
        raise RunSigilError(
            ErrorCode.NOT_FOUND,
            "Evaluation result not found.",
            status_code=404,
        )
    content_digest = canonical_digest(
        {
            "evaluation_result_id": result.id,
            "evaluation_id": result.evaluation_id,
            "scenario_id": result.scenario_id,
            "run_id": result.run_id,
            "reviewer_id": context.actor_id,
            "label": request.label,
            "score_milli": request.score_milli,
            "reason_codes": request.reason_codes,
        }
    )
    existing = session.scalar(
        select(EvaluationAnnotation).where(
            EvaluationAnnotation.idempotency_key == request.idempotency_key
        )
    )
    if existing is not None:
        if existing.content_digest != content_digest:
            raise RunSigilError(
                ErrorCode.VALIDATION_FAILED,
                "The annotation idempotency key belongs to different content.",
                status_code=409,
            )
        return existing
    annotation = EvaluationAnnotation(
        id=uuid4(),
        organization_id=context.organization_id,
        evaluation_result_id=result.id,
        evaluation_id=result.evaluation_id,
        scenario_id=result.scenario_id,
        run_id=result.run_id,
        reviewer_id=context.actor_id,
        idempotency_key=request.idempotency_key,
        label=request.label,
        score_milli=request.score_milli,
        reason_codes_json=list(request.reason_codes),
        content_digest=content_digest,
    )
    session.add(annotation)
    _audit(
        session,
        organization_id=context.organization_id,
        actor_id=context.actor_id,
        event_type="evaluation.annotation_created",
        subject_type="evaluation_annotation",
        subject_id=annotation.id,
        content_digest=content_digest,
        metadata={
            "evaluation_id": str(result.evaluation_id),
            "evaluation_result_id": str(result.id),
            "label": request.label,
            "reason_codes": request.reason_codes,
            "raw_content_captured": False,
        },
    )
    return annotation
