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


def leased_job(repository, owner):
    brief = Brief(
        url="https://clarity.microsoft.com/",
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
        "prepare-budget-test",
        PreparationRequest(brief=brief, confirm_preparation_calls=True),
    )
    leased = repository.lease_one_job("worker-a")
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


@pytest.mark.parametrize("recommendations,total", [(False, 24), (True, 25)])
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


def test_primary_and_fallback_planners_share_the_authorized_query_plan_allowance(tmp_path):
    policy = live_policy()
    grant = budget_grant(policy)
    repository = SQLiteMeasurementRepository(tmp_path / "budget.sqlite3")
    repository.bind_measurement_budget(grant, policy)
    job = leased_job(repository, grant.owner)

    primary = repository.claim_operation(
        job.job_id,
        "worker-a",
        "budget:missions-query-plan",
        "missions-query-plan",
    )
    repository.record_operation(
        primary.claim_id,
        "worker-a",
        OperationClaimState.FAILED,
        error_code="ProviderError",
    )
    fallback = repository.claim_operation(
        job.job_id,
        "worker-a",
        "budget:paired-query-plan-fallback",
        "paired-query-plan-fallback",
    )
    with pytest.raises(Conflict, match="allowance is exhausted"):
        repository.claim_operation(
            job.job_id,
            "worker-a",
            "budget:paired-query-plan-overflow",
            "missions-query-plan",
        )

    with sqlite3.connect(tmp_path / "budget.sqlite3") as connection:
        consumed = connection.execute(
            "SELECT consumed FROM measurement_budget_usage "
            "WHERE operation_type = 'paired-query-plan'"
        ).fetchone()[0]
        audited = connection.execute(
            "SELECT operation_type FROM measurement_budget_consumptions "
            "WHERE claim_id IN (?, ?) ORDER BY created_at",
            (primary.claim_id, fallback.claim_id),
        ).fetchall()
    assert consumed == 2
    assert audited == [("paired-query-plan",), ("paired-query-plan",)]


def test_grant_must_cover_policy_query_plan_ceiling():
    policy = live_policy()
    grant = budget_grant(policy).model_copy(update={
        "allowances": MeasurementOperationAllowances(paired_query_plan=1),
    })
    with pytest.raises(Conflict, match="complete authorized runs"):
        grant.validate_for(policy)


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