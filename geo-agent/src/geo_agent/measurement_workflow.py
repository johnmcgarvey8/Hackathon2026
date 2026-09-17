from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import Field, model_validator

from geo_agent.contracts import (
    Brief,
    Contract,
    MeasurementInputs,
    MeasurementResults,
    digest,
    identifier,
    utc_now,
)
from geo_agent.foundry import PageAnalysis, PreparationAnalysis
from geo_agent.evidence_assessment import BrandDefinition, BrandDefinitionRecord
from geo_agent.recommendations import RecommendationReport
from geo_agent.workflow import Conflict, NotFound


class MeasurementState(StrEnum):
    DRAFT = "draft"
    PREPARING = "preparing"
    AWAITING_APPROVAL = "awaiting-query-approval"
    QUEUED = "queued"
    EVALUATING = "evaluating"
    RECOMMENDING = "recommending"
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"
    NEEDS_REVIEW = "needs-review"
    CANCELLED = "cancelled"
    EXPORTED = "exported"


class OwnerIdentity(Contract):
    tenant_id: str = Field(min_length=1, max_length=200)
    object_id: str = Field(min_length=1, max_length=200)

    @property
    def key(self) -> str:
        return digest(self.model_dump(mode="json"))


class MeasurementApproval(Contract):
    actor: OwnerIdentity
    revision: int = Field(ge=1)
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_at: datetime = Field(default_factory=utc_now)


class MeasurementEvent(Contract):
    sequence: int = Field(ge=1)
    event_type: str = Field(pattern=r"^[a-z0-9-]+$")
    occurred_at: datetime = Field(default_factory=utc_now)


class RecommendationDecision(Contract):
    task_id: str = Field(pattern=r"^rec-[1-3]$")
    decision: Literal["accepted", "rejected"]


class RecommendationReview(Contract):
    schema_version: Literal["geo-recommendation-review/v1"] = "geo-recommendation-review/v1"
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    measurement_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    recommendation_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    actor: OwnerIdentity
    decisions: tuple[RecommendationDecision, ...] = Field(min_length=1, max_length=3)
    reviewed_at: datetime = Field(default_factory=utc_now)
    publish_permission: Literal[False] = False

    @model_validator(mode="after")
    def validate_decisions(self) -> "RecommendationReview":
        if len({decision.task_id for decision in self.decisions}) != len(self.decisions):
            raise ValueError("Recommendation review decisions must have unique task IDs")
        return self

    def validate_for(
        self,
        measurement: MeasurementResults,
        recommendations: RecommendationReport,
    ) -> None:
        if (
            self.approval_hash != measurement.inputs.approval_hash
            or self.measurement_hash != digest(measurement.model_dump(mode="json"))
            or self.recommendation_hash != digest(recommendations.model_dump(mode="json"))
            or {decision.task_id for decision in self.decisions}
            != {task.task_id for task in recommendations.tasks}
        ):
            raise ValueError("Recommendation review does not match the saved report")


class MeasurementRun(Contract):
    schema_version: str = Field(default="geo-measurement-run/v1", pattern=r"^geo-measurement-run/v1$")
    run_id: str = Field(default_factory=identifier)
    owner: OwnerIdentity
    revision: int = Field(default=1, ge=1)
    state: MeasurementState = MeasurementState.DRAFT
    brief: Brief | None = None
    inputs: MeasurementInputs | None = None
    page_analysis: PreparationAnalysis | PageAnalysis | None = None
    approval: MeasurementApproval | None = None
    measurement: MeasurementResults | None = None
    recommendations: RecommendationReport | None = None
    recommendation_review: RecommendationReview | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    events: tuple[MeasurementEvent, ...] = ()

    @model_validator(mode="after")
    def validate_bindings(self) -> "MeasurementRun":
        if self.inputs is not None and self.brief is not None and self.inputs.brief != self.brief:
            raise ValueError("Measurement inputs must match the submitted brief")
        if self.state == MeasurementState.AWAITING_APPROVAL and self.inputs is None:
            raise ValueError("Query approval requires prepared measurement inputs")
        if self.approval is not None:
            if self.inputs is None or self.approval.input_hash != self.inputs.approval_hash:
                raise ValueError("Measurement approval does not match the current inputs")
            if self.approval.actor != self.owner:
                raise ValueError("Measurement approval actor must own the run")
        if self.measurement is not None and self.measurement.inputs != self.inputs:
            raise ValueError("Saved measurement results must match the run inputs")
        if self.recommendations is not None:
            if self.measurement is None:
                raise ValueError("Recommendations require saved measurement results")
            self.recommendations.validate_for(self.measurement)
        if self.recommendation_review is not None:
            if self.measurement is None or self.recommendations is None:
                raise ValueError("Recommendation review requires a saved report")
            if self.recommendation_review.actor != self.owner:
                raise ValueError("Recommendation review actor must own the run")
            self.recommendation_review.validate_for(self.measurement, self.recommendations)
        if self.events and [event.sequence for event in self.events] != list(range(1, len(self.events) + 1)):
            raise ValueError("Measurement event sequence must be contiguous")
        return self


Mutation = Callable[[MeasurementRun], MeasurementRun]


class MeasurementRepository(Protocol):
    def create(
        self,
        owner: OwnerIdentity,
        inputs: MeasurementInputs | None = None,
        brief: Brief | None = None,
        brand_definition: BrandDefinition | None = None,
    ) -> MeasurementRun: ...

    def get_brand_definition(self, run_id: str, owner: OwnerIdentity,
                             version: int | None = None) -> BrandDefinitionRecord | None: ...

    def save_brand_definition(self, run_id: str, owner: OwnerIdentity, definition: BrandDefinition,
                              expected_version: int) -> BrandDefinitionRecord: ...

    def get(self, run_id: str, owner: OwnerIdentity) -> MeasurementRun: ...

    def list_runs(self, owner: OwnerIdentity, limit: int = 50) -> tuple[MeasurementRun, ...]: ...

    def mutate(self, run_id: str, owner: OwnerIdentity, revision: int, operation: Mutation) -> MeasurementRun: ...


class MeasurementCoordinator:
    def __init__(self, repository: MeasurementRepository):
        self.repository = repository

    @staticmethod
    def _append(run: MeasurementRun, event_type: str) -> tuple[MeasurementEvent, ...]:
        return (*run.events, MeasurementEvent(sequence=len(run.events) + 1, event_type=event_type))

    def approve(self, run_id: str, owner: OwnerIdentity, revision: int, input_hash: str) -> MeasurementRun:
        def operation(run: MeasurementRun) -> MeasurementRun:
            if run.state != MeasurementState.AWAITING_APPROVAL or run.inputs is None:
                raise Conflict("Measurement run is not awaiting query approval")
            if input_hash != run.inputs.approval_hash:
                raise Conflict("Approval does not match the current measurement inputs")
            return run.model_copy(update={
                "approval": MeasurementApproval(actor=owner, revision=revision, input_hash=input_hash),
                "events": self._append(run, "queries-approved"),
            })

        return self.repository.mutate(run_id, owner, revision, operation)

    def revise_inputs(
        self, run_id: str, owner: OwnerIdentity, revision: int, inputs: MeasurementInputs,
    ) -> MeasurementRun:
        def operation(run: MeasurementRun) -> MeasurementRun:
            if run.state not in {
                MeasurementState.AWAITING_APPROVAL,
                MeasurementState.CANCELLED,
                MeasurementState.FAILED,
            }:
                raise Conflict("Measurement inputs cannot change from the current state")
            if run.inputs is not None and inputs.snapshot.provenance != run.inputs.snapshot.provenance:
                raise Conflict("A measurement run cannot change provenance")
            return run.model_copy(update={
                "brief": inputs.brief,
                "inputs": inputs,
                "approval": None,
                "measurement": None,
                "recommendations": None,
                "recommendation_review": None,
                "state": MeasurementState.AWAITING_APPROVAL,
                "events": self._append(run, "inputs-revised"),
            })

        return self.repository.mutate(run_id, owner, revision, operation)

    def review_recommendations(
        self,
        run_id: str,
        owner: OwnerIdentity,
        revision: int,
        decisions: tuple[RecommendationDecision, ...],
    ) -> MeasurementRun:
        def operation(run: MeasurementRun) -> MeasurementRun:
            if (
                run.state not in {MeasurementState.READY, MeasurementState.PARTIAL}
                or run.measurement is None
                or run.recommendations is None
                or not run.recommendations.tasks
            ):
                raise Conflict("Recommendation review requires completed recommendation tasks")
            try:
                review = RecommendationReview(
                    approval_hash=run.measurement.inputs.approval_hash,
                    measurement_hash=digest(run.measurement.model_dump(mode="json")),
                    recommendation_hash=digest(run.recommendations.model_dump(mode="json")),
                    actor=owner,
                    decisions=decisions,
                )
                review.validate_for(run.measurement, run.recommendations)
            except ValueError:
                raise Conflict("Recommendation decisions must match every saved task") from None
            return run.model_copy(update={
                "recommendation_review": review,
                "events": self._append(run, "recommendations-reviewed"),
            })

        return self.repository.mutate(run_id, owner, revision, operation)