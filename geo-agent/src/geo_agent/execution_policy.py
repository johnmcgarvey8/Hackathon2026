from typing import Literal

from pydantic import Field, model_validator

from geo_agent.contracts import Brief, Contract, SimulationProfile, digest
from geo_agent.workflow import Conflict


class MeasurementExecutionPolicy(Contract):
    schema_version: Literal["geo-execution-policy/v1"] = "geo-execution-policy/v1"
    policy_id: str = Field(pattern=r"^[a-z0-9-]{1,100}$")
    execution_mode: Literal["mock", "live"] = "mock"
    owner_role: Literal["Geo.Operator"] = "Geo.Operator"
    allowed_domains: tuple[str, ...] = Field(min_length=1, max_length=20)
    locale: str = Field(pattern=r"^[a-z]{2}-[A-Z]{2}$")
    profiles: tuple[SimulationProfile, ...] = Field(min_length=1, max_length=3)
    retention_days: int = Field(default=30, ge=1, le=365)
    max_output_tokens_per_call: int = Field(default=2000, ge=1, le=4000)
    max_concurrent_workers: int = Field(default=1, ge=1, le=10)
    max_browse_calls: Literal[1] = 1
    max_page_analysis_calls: Literal[1] = 1
    max_query_plan_calls: Literal[1] = 1
    max_search_calls: Literal[5] = 5
    max_evaluator_calls_per_profile: Literal[5] = 5
    max_recommendation_calls: Literal[1] = 1
    automatic_retries: Literal[False] = False
    budget_grant_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_live_gate(self) -> "MeasurementExecutionPolicy":
        if self.execution_mode == "live" and (
            self.budget_grant_id is None or len(self.profiles) not in {1, 3}
        ):
            raise ValueError("Live execution requires an approved budget and a one- or three-profile roster")
        if len({profile.profile_id for profile in self.profiles}) != len(self.profiles):
            raise ValueError("Execution policy profile IDs must be unique")
        return self

    @property
    def policy_hash(self) -> str:
        return digest(self.model_dump(mode="json"))

    def validate_brief(self, brief: Brief) -> None:
        host = (brief.url.host or "").casefold()
        allowed = tuple(domain.casefold().strip(".") for domain in self.allowed_domains)
        if brief.locale != self.locale:
            raise Conflict(f"Brief locale must be {self.locale}")
        if not any(host == domain or host.endswith(f".{domain}") for domain in allowed):
            raise Conflict(f"Brief host must match an allowed domain: {', '.join(self.allowed_domains)}")