from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, Field

from geo_agent.contracts import Contract, digest
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.measurement_workflow import OwnerIdentity
from geo_agent.workflow import Conflict


MeasurementOperationType = Literal[
    "webiq-browse",
    "page-analysis-model",
    "paired-query-plan",
    "webiq-search",
    "profile-evaluator",
    "recommendation-model",
]

QUERY_PLAN_BUDGET_OPERATION = "paired-query-plan"
QUERY_PLAN_CLAIM_OPERATIONS = {
    "missions-query-plan",
    "paired-query-plan-fallback",
}


def measurement_budget_operation_type(operation_type: str) -> str:
    if operation_type in QUERY_PLAN_CLAIM_OPERATIONS:
        return QUERY_PLAN_BUDGET_OPERATION
    return operation_type


class MeasurementOperationAllowances(Contract):
    webiq_browse: int = Field(default=1, ge=0, le=100)
    page_analysis_model: int = Field(default=1, ge=0, le=100)
    paired_query_plan: int = Field(default=1, ge=0, le=100)
    webiq_search: int = Field(default=5, ge=0, le=500)
    profile_evaluator: int = Field(default=15, ge=0, le=1500)
    recommendation_model: int = Field(default=0, ge=0, le=100)

    @property
    def authorized_runs(self) -> int:
        return self.webiq_browse

    @property
    def total_calls(self) -> int:
        return sum(allowance for _, allowance in self.items())

    def items(self) -> tuple[tuple[MeasurementOperationType, int], ...]:
        return (
            ("webiq-browse", self.webiq_browse),
            ("page-analysis-model", self.page_analysis_model),
            ("paired-query-plan", self.paired_query_plan),
            ("webiq-search", self.webiq_search),
            ("profile-evaluator", self.profile_evaluator),
            ("recommendation-model", self.recommendation_model),
        )


class MeasurementBudgetGrant(Contract):
    schema_version: Literal["geo-measurement-budget-grant/v1"] = "geo-measurement-budget-grant/v1"
    grant_id: str = Field(pattern=r"^[a-z0-9-]{1,100}$")
    purpose: Literal["complete-runs", "failed-evaluator-recovery"] = "complete-runs"
    policy_id: str = Field(pattern=r"^[a-z0-9-]{1,100}$")
    policy_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    owner: OwnerIdentity
    allowances: MeasurementOperationAllowances
    maximum_authorized_cost_usd: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    cost_enforcement: Literal["authorization-ceiling-only"] = "authorization-ceiling-only"
    automatic_retries: Literal[False] = False
    approval: str = Field(min_length=1, max_length=2000)
    approved_at: AwareDatetime

    @property
    def grant_hash(self) -> str:
        return digest(self.model_dump(mode="json"))

    def validate_for(self, policy: MeasurementExecutionPolicy) -> None:
        if policy.execution_mode != "live":
            raise Conflict("Measurement budget grants require a live execution policy")
        if (
            policy.policy_id != self.policy_id
            or policy.policy_hash != self.policy_hash
        ):
            raise Conflict("Measurement budget grant does not match the execution policy")
        if policy.automatic_retries or self.automatic_retries:
            raise Conflict("Measurement budget grants do not permit automatic retries")
        if self.purpose == "failed-evaluator-recovery":
            if (
                self.allowances.webiq_browse
                or self.allowances.page_analysis_model
                or self.allowances.paired_query_plan
                or self.allowances.webiq_search
                or self.allowances.recommendation_model
                or self.allowances.profile_evaluator < 1
                or self.allowances.profile_evaluator
                > policy.max_evaluator_calls_per_profile * len(policy.profiles)
            ):
                raise Conflict(
                    "Evaluator recovery grants may authorize only a bounded evaluator batch"
                )
            return
        authorized_runs = self.allowances.authorized_runs
        if authorized_runs < 1:
            raise Conflict("Complete-run grants must authorize at least one run")
        expected_stage_allowances = (
            (self.allowances.page_analysis_model, policy.max_page_analysis_calls),
            (self.allowances.paired_query_plan, policy.max_query_plan_calls),
            (self.allowances.webiq_search, policy.max_search_calls),
        )
        if any(actual != per_run * authorized_runs for actual, per_run in expected_stage_allowances):
            raise Conflict("Measurement budget allowances do not fund complete authorized runs")
        expected_evaluator_calls = (
            policy.max_evaluator_calls_per_profile * len(policy.profiles) * authorized_runs
        )
        if self.allowances.profile_evaluator != expected_evaluator_calls:
            raise Conflict(
                "Measurement budget evaluator allowance does not match the execution policy roster "
                "and authorized runs"
            )
        maximum_recommendation_calls = policy.max_recommendation_calls * authorized_runs
        if self.allowances.recommendation_model > maximum_recommendation_calls:
            raise Conflict("Measurement budget recommendation allowance exceeds the authorized runs")


class MeasurementOperationCapacity(Contract):
    allowance: int = Field(ge=0)
    consumed: int = Field(ge=0)
    remaining: int = Field(ge=0)


class MeasurementCapacity(Contract):
    authorized_runs: int = Field(ge=0)
    consumed_runs: int = Field(ge=0)
    remaining_runs: int = Field(ge=0)
    exhausted: bool
    operations: dict[MeasurementOperationType, MeasurementOperationCapacity]
