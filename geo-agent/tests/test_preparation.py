import json
import sqlite3

import pytest
from pydantic import ValidationError

from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.foundry import PageAnalysis, PageEvidence, PageFinding, PageImprovement
from geo_agent.jobs import JobService, JobState, JobType
from geo_agent.measurement_workflow import MeasurementState, OwnerIdentity
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.preparation import PreparationHandler, PreparationRequest
from geo_agent.run_view import operation_estimates
from geo_agent.webiq import ProviderError
from geo_agent.workflow import Conflict
from geo_agent.worker import Worker
from test_measurement_workflow import inputs


class FakeBrowse:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0

    def browse(self, _brief):
        self.calls += 1
        return self.snapshot


class FakeFoundry:
    def __init__(self, plan):
        self.plan = plan
        self.analysis_calls = 0
        self.plan_calls = 0

    def analyse_page(self, _snapshot, _passages):
        self.analysis_calls += 1
        evidence = [PageEvidence(passage_id="page-1", quote="Family visits")]
        finding = PageFinding(text="The page supports visit planning", basis="observed", evidence=evidence)
        return PageAnalysis(
            purpose=finding,
            audience=PageFinding(text="Families", basis="inferred", evidence=evidence),
            entities=[],
            questions_answered=[],
            observations=[],
            improvements=[PageImprovement(
                hypothesis="Clarify seasonal details",
                rationale="The excerpt mentions seasonal events",
                evidence=evidence,
                verification="Compare task completion after the edit",
            )],
        ), {"model": "fixture-analysis", "response_id": "analysis-1"}

    def propose_pairs(self, _brief, _snapshot):
        self.plan_calls += 1
        return self.plan, {"model": "fixture-planner", "response_id": "plan-1"}


def policy():
    prepared = inputs()
    return MeasurementExecutionPolicy(
        policy_id="mock-policy",
        allowed_domains=("example.com",),
        locale="en-GB",
        profiles=prepared.profiles,
    )


def test_mock_preparation_runs_exactly_three_claimed_operations(tmp_path):
    prepared = inputs()
    repository = SQLiteMeasurementRepository(tmp_path / "prepare.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    request = PreparationRequest(brief=prepared.brief, confirm_preparation_calls=True)
    draft = repository.create(owner, brief=request.brief)
    job, _ = JobService(repository).enqueue(
        draft.run_id, owner, draft.revision, JobType.PREPARE, "prepare-1", request
    )
    browse = FakeBrowse(prepared.snapshot)
    foundry = FakeFoundry(prepared.query_plan)

    result = Worker(
        repository,
        "worker-a",
        {JobType.PREPARE: PreparationHandler(policy(), browse, foundry, foundry)},
    ).run_once()
    assert result is not None
    completed_job, completed_run = result

    assert completed_job.state == JobState.COMPLETED
    assert completed_run.state == MeasurementState.AWAITING_APPROVAL
    assert completed_run.inputs is not None
    assert completed_run.inputs.query_plan == prepared.query_plan
    assert completed_run.inputs.policy_hash == policy().policy_hash
    assert completed_run.page_analysis is not None
    assert completed_run.page_analysis.purpose.text == "The page supports visit planning"
    assert (browse.calls, foundry.analysis_calls, foundry.plan_calls) == (1, 1, 1)
    with sqlite3.connect(tmp_path / "prepare.sqlite3") as connection:
        claims = connection.execute(
            "SELECT operation_key, state FROM operation_claims ORDER BY created_at"
        ).fetchall()
    assert claims == [
        (f"{job.job_id}:browse", "completed"),
        (f"{job.job_id}:page-analysis", "completed"),
        (f"{job.job_id}:query-plan", "completed"),
    ]


def test_unconfirmed_preparation_request_is_rejected_before_enqueue():
    with pytest.raises(ValidationError):
        PreparationRequest(brief=inputs().brief, confirm_preparation_calls=False)


def test_live_operation_estimates_separate_primary_and_optional_fallback(tmp_path):
    prepared = inputs()
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    repository = SQLiteMeasurementRepository(tmp_path / "estimates.sqlite3")
    run = repository.create(owner, inputs=prepared)
    estimates = operation_estimates(
        policy().model_copy(update={
            "execution_mode": "live",
            "max_query_plan_calls": 2,
            "budget_grant_id": "estimate-grant",
        }),
        run,
    )
    assert estimates["preparation"] == {
        "webiq-browse": 1,
        "page-analysis-model": 1,
        "missions-query-plan": 1,
        "paired-query-plan-fallback": 1,
        "total": 4,
    }


def test_hosted_query_failure_uses_visible_claimed_fallback(tmp_path):
    prepared = inputs()
    repository = SQLiteMeasurementRepository(tmp_path / "fallback.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    request = PreparationRequest(brief=prepared.brief, confirm_preparation_calls=True)
    draft = repository.create(owner, brief=request.brief)
    job, _ = JobService(repository).enqueue(
        draft.run_id, owner, draft.revision, JobType.PREPARE, "prepare-fallback", request
    )
    browse = FakeBrowse(prepared.snapshot)
    foundry = FakeFoundry(prepared.query_plan)

    class FailingHostedPlanner:
        def propose_pairs(self, _brief, _snapshot):
            raise ProviderError("Hosted query agent failed")

    fallback_policy = policy().model_copy(update={"max_query_plan_calls": 2})
    result = Worker(
        repository,
        "worker-a",
        {
            JobType.PREPARE: PreparationHandler(
                fallback_policy,
                browse,
                foundry,
                FailingHostedPlanner(),
                foundry,
                query_planner_operation_type="missions-query-plan",
            ),
        },
    ).run_once()
    assert result is not None
    completed_job, completed_run = result
    assert completed_job.state == JobState.COMPLETED
    assert completed_run.inputs is not None
    assert completed_run.inputs.query_plan == prepared.query_plan
    assert completed_run.inputs.query_generation.model == "fixture-planner"
    assert [event.event_type for event in completed_run.events][-2:] == [
        "query-plan-fallback-used",
        "awaiting-query-approval",
    ]
    with sqlite3.connect(tmp_path / "fallback.sqlite3") as connection:
        rows = connection.execute(
            "SELECT operation_key, payload, state FROM operation_claims ORDER BY created_at"
        ).fetchall()
    claims = [
        (operation_key, json.loads(payload)["operation_type"], state)
        for operation_key, payload, state in rows
    ]
    assert claims[-2:] == [
        (f"{job.job_id}:query-plan:missions", "missions-query-plan", "failed"),
        (f"{job.job_id}:query-plan:fallback", "paired-query-plan-fallback", "completed"),
    ]


def test_page_analysis_provider_failure_does_not_block_query_measurement(tmp_path):
    prepared = inputs()
    repository = SQLiteMeasurementRepository(tmp_path / "analysis-optional.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    request = PreparationRequest(brief=prepared.brief, confirm_preparation_calls=True)
    draft = repository.create(owner, brief=request.brief)
    JobService(repository).enqueue(
        draft.run_id, owner, draft.revision, JobType.PREPARE, "prepare-analysis-optional", request
    )
    browse = FakeBrowse(prepared.snapshot)
    foundry = FakeFoundry(prepared.query_plan)

    class FailingAnalysis:
        def analyse_page(self, _snapshot, _passages):
            raise ProviderError("Page analysis failed")

    result = Worker(
        repository,
        "worker-a",
        {JobType.PREPARE: PreparationHandler(policy(), browse, FailingAnalysis(), foundry)},
    ).run_once()
    assert result is not None
    completed_job, completed_run = result
    assert completed_job.state == JobState.COMPLETED
    assert completed_run.state == MeasurementState.AWAITING_APPROVAL
    assert completed_run.page_analysis is None
    assert [event.event_type for event in completed_run.events][-2:] == [
        "page-analysis-unavailable",
        "awaiting-query-approval",
    ]


def test_live_policy_fails_closed_without_budget():
    base = policy().model_dump()
    with pytest.raises(ValidationError, match="approved budget"):
        MeasurementExecutionPolicy.model_validate({**base, "execution_mode": "live"})


def test_live_policy_accepts_one_profile_and_project_bound_scope():
    base = policy().model_dump()
    validated = MeasurementExecutionPolicy.model_validate({
        **base,
        "execution_mode": "live",
        "scope": "project-bound",
        "allowed_domains": [],
        "locale": None,
        "profiles": [base["profiles"][0]],
        "budget_grant_id": "demo-grant",
    })
    assert len(validated.profiles) == 1
    with pytest.raises(Conflict, match="project-bound"):
        validated.validate_brief(inputs().brief)
    validated.validate_brief(inputs().brief, project_bound=True)