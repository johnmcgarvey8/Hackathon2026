import json
import sqlite3

import pytest
from pydantic import ValidationError

from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.evidence_assessment import BrandDefinition
from geo_agent.foundry import InferredBrand, PreparationAnalysis, PageEvidence, PageFinding, PageImprovement
from geo_agent.jobs import JobService, JobState, JobType
from geo_agent.measurement_workflow import MeasurementState, OwnerIdentity
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.preparation import PreparationHandler, PreparationRequest
from geo_agent.webiq import ProviderError, ProviderFailure
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
    def __init__(self, plan, infer_brand=True):
        self.plan = plan
        self.infer_brand = infer_brand
        self.analysis_calls = 0
        self.plan_calls = 0

    def analyse_preparation(self, _snapshot, _passages):
        self.analysis_calls += 1
        evidence = [PageEvidence(passage_id="page-1", quote="Family visits")]
        finding = PageFinding(text="The page supports visit planning", basis="observed", evidence=evidence)
        return PreparationAnalysis(
            brand=InferredBrand(definition=BrandDefinition(name="Example Visits", domains=("example.com",)),
                                evidence=evidence, rationale="Synthetic brand inferred from the fixture.") if self.infer_brand else None,
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


@pytest.mark.parametrize("manual,infer_brand", [(False, True), (True, True), (False, False)])
def test_mock_preparation_runs_exactly_three_claimed_operations(tmp_path, manual, infer_brand):
    prepared = inputs()
    repository = SQLiteMeasurementRepository(tmp_path / "prepare.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    request = PreparationRequest(brief=prepared.brief, confirm_preparation_calls=True)
    manual_definition = BrandDefinition(name="Manual override") if manual else None
    draft = repository.create(owner, brief=request.brief, brand_definition=manual_definition)
    job, _ = JobService(repository).enqueue(
        draft.run_id, owner, draft.revision, JobType.PREPARE, "prepare-1", request
    )
    browse = FakeBrowse(prepared.snapshot)
    foundry = FakeFoundry(prepared.query_plan, infer_brand=infer_brand)

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
    restarted = SQLiteMeasurementRepository(tmp_path / "prepare.sqlite3")
    saved = restarted.get(completed_run.run_id, owner)
    assert saved.page_analysis == completed_run.page_analysis
    record = restarted.get_brand_definition(completed_run.run_id, owner)
    if manual:
        assert record.definition == manual_definition and record.source == "manual"
    elif infer_brand:
        assert record.definition == saved.page_analysis.brand.definition
        assert record.source == "page-analysis" and record.definition_version == 1
    else:
        assert record is None
    restarted.engine.dispose()
    with sqlite3.connect(tmp_path / "prepare.sqlite3") as connection:
        claims = connection.execute(
            "SELECT operation_key, state FROM operation_claims ORDER BY created_at"
        ).fetchall()
    assert claims == [
        ("prepare-1:browse", "completed"),
        ("prepare-1:page-analysis", "completed"),
        ("prepare-1:query-plan", "completed"),
    ]


@pytest.mark.parametrize("code", list(ProviderFailure))
def test_query_packet_failure_preserves_safe_code_without_replay(tmp_path, monkeypatch, code):
    prepared = inputs()
    database = tmp_path / "failure.sqlite3"
    repository = SQLiteMeasurementRepository(database)
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    draft = repository.create(owner, brief=prepared.brief)
    JobService(repository).enqueue(draft.run_id, owner, draft.revision, JobType.PREPARE, "prepare-failure",
                                  PreparationRequest(brief=prepared.brief, confirm_preparation_calls=True))
    browse = FakeBrowse(prepared.snapshot)
    foundry = FakeFoundry(prepared.query_plan)

    def fail_plan(*args):
        foundry.plan_calls += 1
        raise ProviderError("dummy-sensitive-provider-response", code=code)

    monkeypatch.setattr(foundry, "propose_pairs", fail_plan)
    worker = Worker(repository, "worker-a", {JobType.PREPARE: PreparationHandler(policy(), browse, foundry, foundry)})
    job, run = worker.run_once()
    assert job.state == JobState.FAILED and job.error_code == code.value
    assert run.state == MeasurementState.NEEDS_REVIEW and run.inputs is None
    restarted = SQLiteMeasurementRepository(database)
    assert restarted.get_job(job.job_id, owner).error_code == code.value
    assert Worker(restarted, "worker-b", worker.handlers).run_once() is None
    assert (browse.calls, foundry.analysis_calls, foundry.plan_calls) == (1, 1, 1)
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT payload FROM operation_claims ORDER BY created_at").fetchall()
        claims = [json.loads(row[0]) for row in rows]
        saved_job = connection.execute("SELECT payload FROM workflow_jobs").fetchone()[0]
    assert [claim["state"] for claim in claims] == ["completed", "completed", "failed"]
    assert claims[-1]["error_code"] == code.value
    assert "dummy-sensitive" not in json.dumps(claims) + saved_job


def test_unconfirmed_preparation_request_is_rejected_before_enqueue():
    with pytest.raises(ValidationError):
        PreparationRequest(brief=inputs().brief, confirm_preparation_calls=False)


def test_brand_save_failure_rolls_back_preparation_without_replay(tmp_path, monkeypatch):
    prepared = inputs()
    repository = SQLiteMeasurementRepository(tmp_path / "rollback.sqlite3")
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    draft = repository.create(owner, brief=prepared.brief)
    JobService(repository).enqueue(draft.run_id, owner, draft.revision, JobType.PREPARE, "prepare-rollback",
                                  PreparationRequest(brief=prepared.brief, confirm_preparation_calls=True))
    browse = FakeBrowse(prepared.snapshot)
    foundry = FakeFoundry(prepared.query_plan)

    def fail_insert(*args):
        raise RuntimeError("synthetic brand save failure")

    monkeypatch.setattr(repository, "_insert_brand_definition", fail_insert)
    worker = Worker(repository, "worker-a", {JobType.PREPARE: PreparationHandler(policy(), browse, foundry, foundry)})
    with pytest.raises(RuntimeError, match="synthetic brand save failure"):
        worker.run_once()
    run = repository.get(draft.run_id, owner)
    assert run.state == MeasurementState.PREPARING and run.inputs is None and run.page_analysis is None
    assert repository.get_brand_definition(run.run_id, owner) is None
    assert worker.run_once() is None
    assert (browse.calls, foundry.analysis_calls, foundry.plan_calls) == (1, 1, 1)


def test_live_policy_fails_closed_without_budget_and_roster():
    base = policy().model_dump()
    with pytest.raises(ValidationError, match="approved budget and a one- or three-profile roster"):
        MeasurementExecutionPolicy.model_validate({**base, "execution_mode": "live"})