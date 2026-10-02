from typing import Any

from geo_agent.evaluation import measurement_scores
from geo_agent.evidence_assessment import build_competitor_summary
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import JobState, WorkflowJob
from geo_agent.measurement_workflow import MeasurementRun, MeasurementState, OwnerIdentity
from geo_agent.specialist_agents import AgentStageStatus, SpecialistAgentRole


ACTIVE_JOB_STATES = {JobState.QUEUED, JobState.LEASED}


def job_view(job: WorkflowJob) -> dict:
    return job.model_dump(mode="json", exclude={
        "owner",
        "request",
        "request_hash",
        "idempotency_key",
        "lease_holder",
        "lease_expires_at",
    })


def operation_estimates(
    policy: MeasurementExecutionPolicy | None,
    run: MeasurementRun,
) -> dict | None:
    if policy is None:
        return None
    query_count = len(run.inputs.query_plan.queries) if run.inputs is not None else 5
    profile_count = len(run.inputs.profiles) if run.inputs is not None else len(policy.profiles)
    return {
        "preparation": {
            "webiq-browse": policy.max_browse_calls,
            "page-analysis-model": policy.max_page_analysis_calls,
            "paired-query-plan": policy.max_query_plan_calls,
            "total": (
                policy.max_browse_calls
                + policy.max_page_analysis_calls
                + policy.max_query_plan_calls
            ),
        },
        "evaluation": {
            "webiq-search": min(query_count, policy.max_search_calls),
            "profile-evaluator": min(
                query_count * profile_count,
                policy.max_evaluator_calls_per_profile * profile_count,
            ),
            "recommendation-model": policy.max_recommendation_calls,
        },
        "automatic_retries": policy.automatic_retries,
    }


def _project_id(repository: Any, run: MeasurementRun, owner: OwnerIdentity) -> str | None:
    resolver = getattr(repository, "get_run_project_id", None)
    return resolver(run.run_id, owner) if resolver is not None else None


def build_run_view(
    repository: Any,
    run: MeasurementRun,
    policy: MeasurementExecutionPolicy | None,
    *,
    project_id: str | None = None,
) -> dict:
    payload = run.model_dump(mode="json", exclude={"owner"})
    if payload["approval"] is not None:
        payload["approval"].pop("actor", None)
    if payload["recommendation_review"] is not None:
        payload["recommendation_review"].pop("actor", None)

    jobs = repository.list_run_jobs(run.run_id, run.owner)
    latest_job = jobs[0] if jobs else None
    progress = repository.get_run_progress(run.run_id, run.owner)
    bound_project_id = project_id or _project_id(repository, run, run.owner)
    active_job = latest_job is not None and latest_job.state in ACTIVE_JOB_STATES
    approval_current = (
        run.inputs is not None
        and run.approval is not None
        and run.approval.input_hash == run.inputs.approval_hash
    )
    recommendation_tasks = bool(run.recommendations and run.recommendations.tasks)
    recommendation_stage = next(
        (
            stage for stage in run.agent_stages
            if stage.role == SpecialistAgentRole.RECOMMENDATIONS
        ),
        None,
    )
    failed_evaluators = bool(
        run.measurement
        and any(result.status == "error" for result in run.measurement.results)
    )
    exportable = (
        run.measurement is not None
        and run.state in {
            MeasurementState.READY,
            MeasurementState.PARTIAL,
            MeasurementState.FAILED,
            MeasurementState.EXPORTED,
        }
    )
    progress_payload = progress.model_dump(mode="json", exclude={"observed_at"})

    payload.update({
        "project_id": bound_project_id,
        "approval_hash": run.inputs.approval_hash if run.inputs is not None else None,
        "policy_mode": policy.execution_mode if policy is not None else None,
        "operation_estimates": operation_estimates(policy, run),
        "latest_job": job_view(latest_job) if latest_job is not None else None,
        "progress": progress_payload,
        "result_availability": {
            "query_plan": run.inputs is not None,
            "webiq_evidence": bool(run.measurement and run.measurement.retrievals),
            "model_answers": bool(run.measurement and run.measurement.results),
            "citation_performance": run.measurement is not None,
            "brand_presence": run.measurement is not None,
            "recommendations": run.recommendations is not None,
            "limitations_and_provenance": run.inputs is not None,
            "content_strategy": run.measurement is not None,
            "artifact": run.state == MeasurementState.EXPORTED,
        },
        "available_actions": {
            "prepare": (
                policy is not None
                and run.state == MeasurementState.DRAFT
                and run.brief is not None
                and not active_job
            ),
            "revise_queries": (
                run.inputs is not None
                and run.state in {
                    MeasurementState.AWAITING_APPROVAL,
                    MeasurementState.CANCELLED,
                    MeasurementState.FAILED,
                }
                and not active_job
            ),
            "approve": (
                run.state == MeasurementState.AWAITING_APPROVAL
                and run.inputs is not None
                and not approval_current
                and not active_job
            ),
            "start": (
                policy is not None
                and run.state == MeasurementState.AWAITING_APPROVAL
                and approval_current
                and not active_job
            ),
            "recover_evaluators": (
                policy is not None
                and policy.execution_mode == "live"
                and run.state in {MeasurementState.FAILED, MeasurementState.PARTIAL}
                and failed_evaluators
                and not active_job
            ),
            "retry_recommendations": (
                recommendation_stage is not None
                and recommendation_stage.status == AgentStageStatus.FAILED
                and not active_job
            ),
            "cancel": active_job,
            "review_recommendations": (
                run.state in {MeasurementState.READY, MeasurementState.PARTIAL}
                and recommendation_tasks
                and not active_job
            ),
            "export": exportable and not active_job,
            "discuss": bound_project_id is not None,
        },
        "scores": measurement_scores(run.measurement) if run.measurement is not None else None,
        "competitor_summary": build_competitor_summary(
            run.measurement,
            run.competitor_domains,
        ),
    })
    return payload
