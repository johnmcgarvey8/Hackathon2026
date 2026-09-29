import asyncio

import pytest

from geo_agent.goal_summary import (
    GoalSummaryInput,
    GoalSummaryRequest,
    GoalSummaryResult,
    ProjectGoalSummaryService,
)
from geo_agent.measurement_workflow import OwnerIdentity
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.projects import ProjectCreate
from geo_agent.workflow import Conflict


class RecordingProvider:
    def __init__(self, *, fallback: bool = False):
        self.calls = []
        self.fallback = fallback

    async def summarize(self, project, request):
        self.calls.append((project, request))
        return GoalSummaryResult(
            operation_id="provider-result",
            input_hash=request.input_hash,
            summary=request.raw_goal if self.fallback else "Evaluate AI visibility for enterprise buyers.",
            fallback_used=self.fallback,
            provider_response_id=None if self.fallback else "resp-summary",
            usage={} if self.fallback else {"total_tokens": 14},
            error_code="foundry-connection" if self.fallback else None,
        )


def test_goal_summary_is_persisted_and_idempotent_across_restart(tmp_path):
    database = tmp_path / "goal-summary.sqlite3"
    repository = SQLiteMeasurementRepository(database)
    owner = OwnerIdentity(tenant_id="local", object_id="operator")
    project = repository.create_project(
        owner,
        ProjectCreate(name="Example", primary_domain="example.com"),
    )
    provider = RecordingProvider()
    service = ProjectGoalSummaryService(repository, provider)
    request = GoalSummaryRequest(
        raw_goal="I want to understand whether enterprise buyers can find us in AI answers",
        url="https://example.com/page",
        audience="Enterprise buyers",
        target_kind="page",
        idempotency_key="summary-1",
    )

    first = asyncio.run(service.summarize(project, owner, request))
    restarted = ProjectGoalSummaryService(
        SQLiteMeasurementRepository(database),
        provider,
    )
    replay = asyncio.run(restarted.summarize(project, owner, request))

    assert replay == first
    assert first.summary == "Evaluate AI visibility for enterprise buyers."
    assert first.provider_response_id == "resp-summary"
    assert len(provider.calls) == 1

    with pytest.raises(Conflict, match="bound to another request"):
        asyncio.run(restarted.summarize(
            project,
            owner,
            request.model_copy(update={"raw_goal": "Different goal"}),
        ))


def test_goal_summary_fallback_preserves_supplied_user_text(tmp_path):
    repository = SQLiteMeasurementRepository(tmp_path / "goal-fallback.sqlite3")
    owner = OwnerIdentity(tenant_id="local", object_id="operator")
    project = repository.create_project(
        owner,
        ProjectCreate(name="Example", primary_domain="example.com"),
    )
    provider = RecordingProvider(fallback=True)
    service = ProjectGoalSummaryService(repository, provider)
    request = GoalSummaryRequest(
        raw_goal="Keep this exact user goal",
        url="https://example.com/page",
        idempotency_key="summary-fallback",
    )

    result = asyncio.run(service.summarize(project, owner, request))

    assert result.summary == "Keep this exact user goal"
    assert result.fallback_used is True
    assert result.error_code == "foundry-connection"
    assert service.get(project.project_id, owner, result.operation_id) == result
