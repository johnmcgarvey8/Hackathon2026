import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


def identifier() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Provenance(StrEnum):
    SYNTHETIC = "synthetic"
    RECORDED = "recorded"
    LIVE = "live"


class State(StrEnum):
    AWAITING_APPROVAL = "awaiting-query-approval"
    EVALUATING = "evaluating"
    READY = "recommendations-ready"
    EXPORTED = "exported"
    CANCELLED = "cancelled"
    FAILED = "failed"
    PARTIAL = "partial"


class Brief(Contract):
    url: HttpUrl
    audience: str = Field(min_length=1, max_length=1000)
    goal: str = Field(min_length=1, max_length=1000)
    locale: str = Field(pattern=r"^[a-z]{2}-[A-Z]{2}$")


class PageSnapshot(Contract):
    url: HttpUrl
    title: str
    content: str = Field(max_length=50000)
    provenance: Provenance
    captured_at: datetime = Field(default_factory=utc_now)
    provider_trace_id: str | None = None
    content_format: str | None = None
    crawled_at: str | None = None
    last_updated_at: str | None = None

    @property
    def content_hash(self) -> str:
        return digest(self.model_dump(mode="json"))


class Query(Contract):
    query_id: str = Field(pattern=r"^q-[0-9]+$")
    text: str = Field(min_length=1, max_length=500)
    intent: str = Field(min_length=1, max_length=500)
    branded: bool = False


class Profile(Contract):
    profile_id: str = Field(pattern=r"^[a-z0-9-]+$")
    deployment: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    endpoint: str | None = None


class ModelCall(Contract):
    model: str
    response_id: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class RunInputs(Contract):
    brief: Brief
    snapshot: PageSnapshot
    queries: tuple[Query, ...] = Field(min_length=5, max_length=10)
    profiles: tuple[Profile, ...] = Field(min_length=1, max_length=3)
    retrieval_mode: Literal["controlled-evidence-packet"] = "controlled-evidence-packet"
    live_policy_hash: str | None = None
    live_policy: dict | None = None
    query_generation: ModelCall | None = None

    @model_validator(mode="after")
    def validate_inputs(self) -> "RunInputs":
        if self.brief.url != self.snapshot.url:
            raise ValueError("Snapshot must belong to the submitted page")
        if len({query.query_id for query in self.queries}) != len(self.queries):
            raise ValueError("Query IDs must be unique")
        if len({query.text.casefold() for query in self.queries}) != len(self.queries):
            raise ValueError("Queries must be deduplicated")
        if len({profile.profile_id for profile in self.profiles}) != len(self.profiles):
            raise ValueError("Profile IDs must be unique")
        return self

    @property
    def approval_hash(self) -> str:
        return digest(self.model_dump(mode="json"))


class Approval(Contract):
    actor: str
    revision: int
    input_hash: str
    approved_at: datetime = Field(default_factory=utc_now)


class Source(Contract):
    evidence_id: str = Field(pattern=r"^[a-z0-9-]+$")
    url: HttpUrl
    excerpt: str = Field(min_length=1, max_length=2000)
    provenance: Provenance
    title: str = ""
    returned_position: int | None = None
    provider_trace_id: str | None = None
    crawled_at: str | None = None
    last_updated_at: str | None = None


class EvaluationResult(Contract):
    query_id: str
    profile_id: str
    provenance: Provenance
    status: Literal["completed", "error"]
    answer: str = Field(default="", max_length=20000)
    citation_ids: tuple[str, ...] = ()
    sources: tuple[Source, ...] = ()
    error: str | None = None
    model: str | None = None
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "EvaluationResult":
        if self.status == "completed" and (not self.answer or self.error):
            raise ValueError("Completed results require an answer and no error")
        if self.status == "error" and not self.error:
            raise ValueError("Error results require a reason")
        if any(source.provenance != self.provenance for source in self.sources):
            raise ValueError("Mixed result/source provenance")
        if len({source.evidence_id for source in self.sources}) != len(self.sources):
            raise ValueError("Source IDs must be unique within an answer")
        return self


class Run(Contract):
    schema_version: Literal["geo-run/v1"] = "geo-run/v1"
    run_id: str = Field(default_factory=identifier)
    owner: str
    revision: int = 1
    inputs: RunInputs
    state: State = State.AWAITING_APPROVAL
    approval: Approval | None = None
    start_key: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    events: tuple[str, ...] = ("awaiting-query-approval",)
    results: tuple[EvaluationResult, ...] = ()


class EvidenceQuote(Contract):
    evidence_id: str = Field(pattern=r"^[a-z0-9-]+$")
    quote: str = Field(min_length=1, max_length=500)


class QueryPair(Contract):
    query_id: str = Field(pattern=r"^q-[1-5]$")
    priority: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1, max_length=500)
    intent: str = Field(min_length=1, max_length=500)
    branded: bool = False
    chat_query: str = Field(min_length=1, max_length=500)
    grounding_query: str = Field(min_length=1, max_length=500)
    evidence: tuple[EvidenceQuote, ...] = Field(min_length=1, max_length=2)

    def as_query(self, *, grounding: bool = False) -> Query:
        return Query(query_id=self.query_id, text=self.grounding_query if grounding else self.chat_query,
                     intent=self.intent, branded=self.branded)


class QueryPlan(Contract):
    queries: tuple[QueryPair, ...] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_plan(self) -> "QueryPlan":
        if {query.query_id for query in self.queries} != {f"q-{index}" for index in range(1, 6)}:
            raise ValueError("Query plan requires five unique query IDs")
        if {query.priority for query in self.queries} != set(range(1, 6)):
            raise ValueError("Query priorities must be unique from one to five")
        for field in ("chat_query", "grounding_query"):
            if len({" ".join(getattr(query, field).casefold().split()) for query in self.queries}) != 5:
                raise ValueError(f"Duplicate {field} in query plan")
        return self

    def validate_evidence(self, snapshot: PageSnapshot) -> None:
        passages = {f"page-{offset // 1000 + 1}": snapshot.content[offset:offset + 1000]
                    for offset in range(0, min(len(snapshot.content), 10000), 1000)}
        for query in self.queries:
            if any(reference.evidence_id not in passages
                   or reference.quote not in passages[reference.evidence_id] for reference in query.evidence):
                raise ValueError("Query plan contains an unsupported page reference or quote")


class SimulationProfile(Contract):
    profile_id: Literal["chatgpt-style", "claude-backed", "copilot-style"]
    provider: Literal["openai-responses", "anthropic-messages"]
    deployment: str = Field(min_length=1, max_length=200)
    endpoint: str = Field(min_length=1, max_length=1000)
    prompt_version: str = Field(min_length=1, max_length=100)
    instructions: str = Field(min_length=1, max_length=8000)
    simulation: Literal[True] = True

    @model_validator(mode="after")
    def validate_provider(self) -> "SimulationProfile":
        expected = "anthropic-messages" if self.profile_id == "claude-backed" else "openai-responses"
        if self.provider != expected:
            raise ValueError("Simulation label does not match the configured provider")
        return self


class MeasurementInputs(Contract):
    schema_version: Literal["geo-inputs/v2"] = "geo-inputs/v2"
    brief: Brief
    snapshot: PageSnapshot
    query_plan: QueryPlan
    profiles: tuple[SimulationProfile, ...] = Field(min_length=1, max_length=3)
    policy_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    method_version: Literal["exact-page-citation/v1"] = "exact-page-citation/v1"
    retrieval_mode: Literal["controlled-evidence-packet"] = "controlled-evidence-packet"
    query_generation: ModelCall | None = None

    @model_validator(mode="after")
    def validate_measurement(self) -> "MeasurementInputs":
        if self.brief.url != self.snapshot.url:
            raise ValueError("Snapshot must belong to the submitted page")
        if len({profile.profile_id for profile in self.profiles}) != len(self.profiles):
            raise ValueError("Profile IDs must be unique")
        if len({(profile.endpoint, profile.deployment) for profile in self.profiles}) != len(self.profiles):
            raise ValueError("Simulation profiles require distinct deployments")
        self.query_plan.validate_evidence(self.snapshot)
        return self

    @property
    def approval_hash(self) -> str:
        return digest(self.model_dump(mode="json"))


class RetrievalResult(Contract):
    query_id: str = Field(pattern=r"^q-[1-5]$")
    grounding_query: str = Field(min_length=1, max_length=500)
    provenance: Provenance
    status: Literal["completed", "error"]
    sources: tuple[Source, ...] = Field(default=(), max_length=5)
    error: str | None = Field(default=None, max_length=1000)
    provider_trace_id: str | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_retrieval(self) -> "RetrievalResult":
        if self.status == "completed" and self.error is not None:
            raise ValueError("Completed retrieval cannot contain an error")
        if self.status == "error" and (not self.error or self.sources):
            raise ValueError("Failed retrieval requires a reason and no sources")
        if len({source.evidence_id for source in self.sources}) != len(self.sources):
            raise ValueError("Retrieval source IDs must be unique")
        if any(source.provenance != self.provenance for source in self.sources):
            raise ValueError("Retrieval source provenance must match")
        positions = [source.returned_position for source in self.sources]
        if any(position is None or not 1 <= position <= 5 for position in positions):
            raise ValueError("Sources require returned positions from one to five")
        if positions != sorted(set(positions)):
            raise ValueError("Returned positions must be unique and ordered")
        return self


class MeasurementResults(Contract):
    schema_version: Literal["geo-measurement/v2"] = "geo-measurement/v2"
    inputs: MeasurementInputs
    retrievals: tuple[RetrievalResult, ...] = Field(default=(), max_length=5)
    results: tuple[EvaluationResult, ...] = Field(default=(), max_length=15)

    @model_validator(mode="after")
    def validate_results(self) -> "MeasurementResults":
        queries = {query.query_id: query for query in self.inputs.query_plan.queries}
        profiles = {profile.profile_id for profile in self.inputs.profiles}
        packets = {packet.query_id: packet for packet in self.retrievals}
        if len(packets) != len(self.retrievals):
            raise ValueError("Duplicate retrieval for a query")
        for packet in self.retrievals:
            if packet.query_id not in queries or packet.grounding_query != queries[packet.query_id].grounding_query:
                raise ValueError("Retrieval must match an approved grounding query")
            if packet.provenance != self.inputs.snapshot.provenance:
                raise ValueError("Retrieval provenance must match the measurement")
        pairs = {(result.query_id, result.profile_id) for result in self.results}
        if len(pairs) != len(self.results):
            raise ValueError("Duplicate query/profile outcome")
        for result in self.results:
            if result.query_id not in queries or result.profile_id not in profiles:
                raise ValueError("Result must belong to an approved query/profile")
            if result.provenance != self.inputs.snapshot.provenance:
                raise ValueError("Result provenance must match the measurement")
            packet = packets.get(result.query_id)
            if result.status == "completed" and (packet is None or packet.status != "completed"):
                raise ValueError("Completed answer requires a completed retrieval")
            if result.sources != (packet.sources if packet else ()):
                raise ValueError("Answer sources must exactly match the saved retrieval packet")
        return self