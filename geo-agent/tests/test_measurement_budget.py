import sqlite3
from decimal import Decimal

import pytest

from geo_agent.contracts import Brief
from geo_agent.jobs import JobService, JobType, OperationClaimState
from geo_agent.measurement_budget import MeasurementOperationAllowances
from geo_agent.measurement_workflow import OwnerIdentity
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.preparation import PreparationRequest
from geo_agent.workflow import Conflict
from test_live_runtime import budget_grant, live_policy


def leased_job(repository, owner, url="https://clarity.microsoft.com/", worker_id="worker-a"):
    brief = Brief(
        url=url,
        audience="Product teams",
        goal="Compare behavioural analytics tools",
        locale="en-GB",
    )
    draft = repository.create(owner, brief=brief)
    job, _ = JobService(repository).enqueue(
        draft.run_id,
        owner,
        draft.revision,
        JobType.PREPARE,
        f"prepare-budget-test-{draft.run_id}",
        PreparationRequest(brief=brief, confirm_preparation_calls=True),
    )
    leased = repository.lease_one_job(worker_id)
    assert leased is not None and leased.job_id == job.job_id
    return leased


def test_budget_grant_is_immutable_and_rejects_policy_mutation(tmp_path):
    database = tmp_path / "budget.sqlite3"
    policy = live_policy()
    grant = budget_grant(policy)
    repository = SQLiteMeasurementRepository(database)
    repository.bind_measurement_budget(grant, policy)
    repository.bind_measurement_budget(grant, policy)

    changed_approval = grant.model_copy(update={"approval": "Different approval"})
    with pytest.raises(Conflict, match="different authorisation"):
        SQLiteMeasurementRepository(database).bind_measurement_budget(changed_approval, policy)

    changed_cost = grant.model_copy(update={"maximum_authorized_cost_usd": Decimal("50.01")})
    with pytest.raises(Conflict, match="different authorisation"):
        SQLiteMeasurementRepository(database).bind_measurement_budget(changed_cost, policy)

    changed_policy = policy.model_copy(update={
        "budget_grant_id": "clarity-live-runtime-mutated-grant",
        "retention_days": 31,
    })
    changed_grant = budget_grant(changed_policy).model_copy(update={
        "grant_id": changed_policy.budget_grant_id,
        "policy_id": changed_policy.policy_id,
        "policy_hash": changed_policy.policy_hash,
    })
    with pytest.raises(Conflict, match="policy changed"):
        SQLiteMeasurementRepository(database).bind_measurement_budget(changed_grant, changed_policy)


def test_budget_grant_matches_single_profile_roster(tmp_path):
    policy = live_policy(("chatgpt-style",))
    grant = budget_grant(policy)
    repository = SQLiteMeasurementRepository(tmp_path / "single-budget.sqlite3")
    repository.bind_measurement_budget(grant, policy)
    assert grant.allowances.profile_evaluator == 5
    assert grant.allowances.total_calls == 13

    oversized = grant.model_copy(update={
        "allowances": grant.allowances.model_copy(update={"profile_evaluator": 15}),
    })
    with pytest.raises(Conflict, match="does not match the execution policy roster"):
        SQLiteMeasurementRepository(tmp_path / "oversized.sqlite3").bind_measurement_budget(
            oversized,
            policy,
        )


@pytest.mark.parametrize("recommendations,total", [(False, 23), (True, 24)])
def test_budget_consumption_is_atomic_bounded_and_audited(tmp_path, recommendations, total):
    database = tmp_path / "budget.sqlite3"
    policy = live_policy()
    grant = budget_grant(policy, recommendations=recommendations)
    repository = SQLiteMeasurementRepository(database)
    repository.bind_measurement_budget(grant, policy)
    job = leased_job(repository, grant.owner)
    operation_counts = dict(grant.allowances.items())

    first = repository.claim_operation(
        job.job_id,
        "worker-a",
        "budget:webiq-browse:0",
        "webiq-browse",
    )
    repository.record_operation(
        first.claim_id,
        "worker-a",
        OperationClaimState.FAILED,
        error_code="ProviderError",
    )
    with pytest.raises(Conflict, match="already claimed"):
        repository.claim_operation(
            job.job_id,
            "worker-a",
            "budget:webiq-browse:0",
            "webiq-browse",
        )

    for operation_type, allowance in operation_counts.items():
        start = 1 if operation_type == "webiq-browse" else 0
        for index in range(start, allowance):
            repository.claim_operation(
                job.job_id,
                "worker-a",
                f"budget:{operation_type}:{index}",
                operation_type,
            )

    with pytest.raises(Conflict, match="allowance is exhausted"):
        repository.claim_operation(
            job.job_id,
            "worker-a",
            "budget:profile-evaluator:overflow",
            "profile-evaluator",
        )

    with sqlite3.connect(database) as connection:
        consumed = connection.execute(
            "SELECT SUM(consumed) FROM measurement_budget_usage"
        ).fetchone()[0]
        claims = connection.execute("SELECT COUNT(*) FROM operation_claims").fetchone()[0]
        audit_rows = connection.execute(
            "SELECT COUNT(*) FROM measurement_budget_consumptions"
        ).fetchone()[0]
        failed_consumption = connection.execute(
            "SELECT COUNT(*) FROM measurement_budget_consumptions WHERE claim_id = ?",
            (first.claim_id,),
        ).fetchone()[0]
    assert grant.allowances.total_calls == total
    assert grant.maximum_authorized_cost_usd == Decimal("50.00")
    assert grant.cost_enforcement == "authorization-ceiling-only"
    assert (consumed, claims, audit_rows, failed_consumption) == (total, total, total, 1)


def test_budget_rejects_a_job_owned_by_someone_else(tmp_path):
    policy = live_policy()
    grant = budget_grant(policy)
    repository = SQLiteMeasurementRepository(tmp_path / "budget.sqlite3")
    repository.bind_measurement_budget(grant, policy)
    other_owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-b")
    job = leased_job(repository, other_owner)

    with pytest.raises(Conflict, match="does not authorise this job"):
        repository.claim_operation(
            job.job_id,
            "worker-a",
            "budget:webiq-browse:other-owner",
            "webiq-browse",
        )


def test_multi_run_grant_requires_proportional_complete_run_allowances(tmp_path):
    policy = live_policy(("chatgpt-style",))
    grant = budget_grant(policy, authorized_runs=2)
    repository = SQLiteMeasurementRepository(tmp_path / "multi-run-validation.sqlite3")
    repository.bind_measurement_budget(grant, policy)

    assert grant.allowances.authorized_runs == 2
    assert grant.allowances.total_calls == 26

    incomplete = grant.model_copy(update={
        "allowances": grant.allowances.model_copy(update={"webiq_search": 5}),
    })
    with pytest.raises(Conflict, match="do not fund complete authorized runs"):
        SQLiteMeasurementRepository(tmp_path / "incomplete.sqlite3").bind_measurement_budget(
            incomplete,
            policy,
        )


def test_multi_run_grant_funds_different_urls_for_same_owner_and_fails_closed(tmp_path):
    policy = live_policy(("chatgpt-style",))
    grant = budget_grant(policy, authorized_runs=2)
    repository = SQLiteMeasurementRepository(tmp_path / "multi-url.sqlite3")
    repository.bind_measurement_budget(grant, policy)
    homepage_job = leased_job(repository, grant.owner)

    repository.claim_operation(
        homepage_job.job_id,
        "worker-a",
        f"{homepage_job.run_id}:webiq-browse",
        "webiq-browse",
    )
    repository.fail_job(homepage_job.job_id, "worker-a", "test-stage-complete")
    feature_job = leased_job(
        repository,
        grant.owner,
        "https://clarity.microsoft.com/ai-visibility",
        "worker-b",
    )
    repository.claim_operation(
        feature_job.job_id,
        "worker-b",
        f"{feature_job.run_id}:webiq-browse",
        "webiq-browse",
    )
    repository.fail_job(feature_job.job_id, "worker-b", "test-stage-complete")

    overflow_job = leased_job(
        repository,
        grant.owner,
        "https://clarity.microsoft.com/blog",
        "worker-c",
    )
    with pytest.raises(Conflict, match="allowance is exhausted"):
        repository.claim_operation(
            overflow_job.job_id,
            "worker-c",
            f"{overflow_job.run_id}:webiq-browse",
            "webiq-browse",
        )


def test_one_active_job_serializes_last_budget_allowance(tmp_path):
    policy = live_policy(("chatgpt-style",))
    grant = budget_grant(policy)
    repository = SQLiteMeasurementRepository(tmp_path / "concurrent-budget.sqlite3")
    repository.bind_measurement_budget(grant, policy)
    first_job = leased_job(repository, grant.owner, worker_id="worker-a")
    brief = Brief(
        url="https://clarity.microsoft.com/ai-visibility",
        audience="Product teams",
        goal="Compare behavioural analytics tools",
        locale="en-GB",
    )
    draft = repository.create(grant.owner, brief=brief)
    second_job, _ = JobService(repository).enqueue(
        draft.run_id,
        grant.owner,
        draft.revision,
        JobType.PREPARE,
        "second-budget-job",
        PreparationRequest(brief=brief, confirm_preparation_calls=True),
    )
    assert repository.lease_one_job("worker-b") is None
    repository.claim_operation(
        first_job.job_id,
        "worker-a",
        f"{first_job.run_id}:webiq-browse",
        "webiq-browse",
    )
    repository.fail_job(first_job.job_id, "worker-a", "test-stage-complete")
    leased_second = repository.lease_one_job("worker-b")
    assert leased_second is not None and leased_second.job_id == second_job.job_id
    with pytest.raises(Conflict, match="allowance is exhausted"):
        repository.claim_operation(
            second_job.job_id,
            "worker-b",
            f"{second_job.run_id}:webiq-browse",
            "webiq-browse",
        )
    with sqlite3.connect(tmp_path / "concurrent-budget.sqlite3") as connection:
        assert connection.execute(
            "SELECT consumed FROM measurement_budget_usage "
            "WHERE grant_id = ? AND operation_type = 'webiq-browse'",
            (grant.grant_id,),
        ).fetchone()[0] == 1