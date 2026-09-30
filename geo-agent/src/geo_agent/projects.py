from datetime import datetime
import re
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from geo_agent.contracts import Contract, identifier, utc_now
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import JobRepository, WorkflowJob
from geo_agent.measurement_budget import MeasurementCapacity
from geo_agent.measurement_workflow import MeasurementRepository, MeasurementRun, OwnerIdentity


def normalize_domain(value: str) -> str:
    candidate = value.strip().lower().rstrip(".")
    parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
    ):
        raise ValueError("Use a hostname without a path, query, credentials or port")
    return parsed.hostname.rstrip(".")


def canonical_host(value: str) -> str:
    return value.strip().casefold().rstrip(".").removeprefix("www.")


def host_in_domains(host: str, domains: tuple[str, ...]) -> bool:
    candidate = canonical_host(host)
    if not candidate:
        return False
    return any(
        candidate == normalized or candidate.endswith(f".{normalized}")
        for normalized in (canonical_host(domain) for domain in domains)
        if normalized
    )


class FoundryProjectBinding(Contract):
    project_endpoint: str = Field(min_length=1, max_length=1000)
    agent_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    agent_version: str = Field(pattern=r"^[1-9][0-9]*$")
    model_deployment: str | None = Field(default=None, min_length=1, max_length=200)
    knowledge_base_id: str | None = Field(default=None, min_length=1, max_length=1000)

    @field_validator("project_endpoint")
    @classmethod
    def validate_project_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or not parsed.hostname.endswith(".services.ai.azure.com")
            or parsed.port not in {None, 443}
            or re.fullmatch(r"/api/projects/[A-Za-z0-9][A-Za-z0-9._-]*", parsed.path.rstrip("/")) is None
        ):
            raise ValueError("Use a direct HTTPS Microsoft Foundry project endpoint")
        return value.strip().rstrip("/")


class ProjectCreate(Contract):
    name: str = Field(min_length=1, max_length=120)
    primary_domain: str
    additional_domains: tuple[str, ...] = Field(default=(), max_length=20)
    competitor_domains: tuple[str, ...] = Field(default=(), max_length=20)
    default_locale: str = Field(default="en-GB", pattern=r"^[a-z]{2}-[A-Z]{2}$")
    active_goal: str | None = Field(default=None, max_length=500)
    colour: str = Field(default="#0067b8", pattern=r"^#[0-9a-fA-F]{6}$")
    foundry: FoundryProjectBinding | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Project name is required")
        return normalized

    @field_validator("primary_domain")
    @classmethod
    def normalize_primary_domain(cls, value: str) -> str:
        return normalize_domain(value)

    @field_validator("additional_domains", "competitor_domains")
    @classmethod
    def normalize_domains(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_domain(value) for value in values)

    @model_validator(mode="after")
    def validate_domains(self) -> "ProjectCreate":
        domains = (self.primary_domain, *self.additional_domains)
        if len(set(domains)) != len(domains):
            raise ValueError("Project domains must be unique")
        if len(set(self.competitor_domains)) != len(self.competitor_domains):
            raise ValueError("Competitor domains must be unique")
        if set(domains) & set(self.competitor_domains):
            raise ValueError("Competitor domains cannot overlap project domains")
        return self


class ProjectUpdate(Contract):
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    primary_domain: str | None = None
    additional_domains: tuple[str, ...] | None = Field(default=None, max_length=20)
    competitor_domains: tuple[str, ...] | None = Field(default=None, max_length=20)
    default_locale: str | None = Field(default=None, pattern=r"^[a-z]{2}-[A-Z]{2}$")
    active_goal: str | None = Field(default=None, max_length=500)
    colour: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    foundry: FoundryProjectBinding | None = None

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Project name is required")
        return normalized

    @field_validator("primary_domain")
    @classmethod
    def normalize_optional_primary_domain(cls, value: str | None) -> str | None:
        return normalize_domain(value) if value is not None else None

    @field_validator("additional_domains", "competitor_domains")
    @classmethod
    def normalize_optional_domains(
        cls, values: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        return tuple(normalize_domain(value) for value in values) if values is not None else None


class Project(Contract):
    schema_version: str = Field(default="geo-project/v1", pattern=r"^geo-project/v1$")
    project_id: str = Field(default_factory=identifier)
    owner: OwnerIdentity
    revision: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=120)
    primary_domain: str
    additional_domains: tuple[str, ...] = ()
    competitor_domains: tuple[str, ...] = ()
    default_locale: str = Field(pattern=r"^[a-z]{2}-[A-Z]{2}$")
    active_goal: str | None = Field(default=None, max_length=500)
    colour: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    foundry: FoundryProjectBinding | None = None
    archived: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def initials(self) -> str:
        words = [word for word in self.name.split() if word]
        return "".join(word[0] for word in words[:2]).upper()

    @property
    def domains(self) -> tuple[str, ...]:
        return (self.primary_domain, *self.additional_domains)


class ProjectRepository(Protocol):
    def create_project(self, owner: OwnerIdentity, request: ProjectCreate) -> Project: ...

    def get_project(self, project_id: str, owner: OwnerIdentity) -> Project: ...

    def list_projects(self, owner: OwnerIdentity) -> tuple[Project, ...]: ...

    def update_project(
        self, project_id: str, owner: OwnerIdentity, request: ProjectUpdate,
    ) -> Project: ...

    def archive_project(
        self, project_id: str, owner: OwnerIdentity, expected_revision: int,
    ) -> Project: ...

    def bind_run_to_project(
        self, project_id: str, run_id: str, owner: OwnerIdentity,
    ) -> None: ...

    def list_project_runs(
        self, project_id: str, owner: OwnerIdentity, limit: int | None = 50,
    ) -> tuple[MeasurementRun, ...]: ...

    def get_project_run(
        self, project_id: str, run_id: str, owner: OwnerIdentity,
    ) -> MeasurementRun: ...

    def get_run_project_id(
        self, run_id: str, owner: OwnerIdentity,
    ) -> str | None: ...

    def delete_project_run(
        self, project_id: str, run_id: str, owner: OwnerIdentity,
    ) -> tuple[str, ...]: ...

    def delete_project(
        self, project_id: str, owner: OwnerIdentity,
    ) -> tuple[str, ...]: ...


class ProjectMeasurementRepository(
    ProjectRepository,
    MeasurementRepository,
    JobRepository,
    Protocol,
):
    def measurement_capacity(
        self,
        owner: OwnerIdentity,
        policy: MeasurementExecutionPolicy,
    ) -> MeasurementCapacity: ...
