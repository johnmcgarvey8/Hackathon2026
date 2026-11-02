from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from geo_agent.api import create_app
from geo_agent.contracts import Provenance, digest
from geo_agent.evaluation import measurement_scores
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import JobState, JobType
from geo_agent.measurement_workflow import (
    MeasurementCoordinator,
    MeasurementState,
    OwnerIdentity,
    RecommendationDecision,
)
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.project_measurements import ProjectMeasurementOrchestrator
from geo_agent.projects import ProjectCreate, ProjectUpdate, SpecialistAgentBinding
from geo_agent.specialist_agents import (
    AgentStageRecord,
    AgentStageStatus,
    SpecialistAgentCall,
    SpecialistAgentRole,
    SpecialistProviderMode,
)
from geo_agent.specialist_foundry import HostedSpecialistAgent, SpecialistFoundrySettings
from geo_agent.specialist_workflow import SpecialistStageHandler
from geo_agent.technical_checks import TechnicalField, TechnicalSignals
from geo_agent.webiq import ProviderError
from geo_agent.worker import Worker
from geo_agent.workflow import Conflict
from test_artifacts import measurement_result, recommendation_report
from test_recommendation_plan import proposal


TOKEN = "a" * 40


@pytest.fixture
def saved_run(tmp_path):
    database = tmp_path / "regeneration.sqlite3"
    repository = SQLiteMeasurementRepository(database)
    measurement = measurement_result(Provenance.LIVE)
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    project = repository.create_project(
        owner,
        ProjectCreate(name="Example", primary_domain="example.org", default_locale="en-GB"),
    )
    created = repository.create(owner, measurement.inputs, project_id=project.project_id)
    ready = repository.mutate(
        created.run_id, owner, created.revision,
        lambda run: run.model_copy(update={
            "state": MeasurementState.READY,
            "measurement": measurement,
            "recommendations": recommendation_report(measurement),
        }),
    )
    ready = MeasurementCoordinator(repository).review_recommendations(
        ready.run_id, owner, ready.revision,
        (RecommendationDecision(task_id="rec-1", decision="accepted"),),
    )
    policy = MeasurementExecutionPolicy(
        policy_id="regeneration-test",
        execution_mode="live",
        scope="project-bound",
        profiles=measurement.inputs.profiles,
        budget_grant_id="regeneration-test-grant",
    )
    settings = SpecialistFoundrySettings.from_environment({
        "GEO_FOUNDRY_RECOMMENDATIONS_AGENT_ENDPOINT": (
            "https://test.services.ai.azure.com/api/projects/test/"
            "agents/Recommendations/endpoint/protocols/openai/responses"
        ),
        "GEO_FOUNDRY_RECOMMENDATIONS_AGENT_NAME": "Recommendations",
        "GEO_FOUNDRY_RECOMMENDATIONS_AGENT_VERSION": "4",
    })
    yield database, repository, ready, project, policy, settings
    repository.close()


@pytest.mark.parametrize("prior_status", [None, AgentStageStatus.COMPLETED, AgentStageStatus.FAILED])
@pytest.mark.parametrize("html_enabled", [False, True])
def test_regeneration_calls_hosted_agent_and_preserves_measurement(
    saved_run, prior_status, html_enabled,
):
    _, repository, ready, _, policy, settings = saved_run
    policy = policy.model_copy(update={"technical_browse": html_enabled})
    if prior_status is not None:
        ready = repository.mutate(
            ready.run_id, ready.owner, ready.revision,
            lambda run: run.model_copy(update={"agent_stages": (AgentStageRecord(
                role=SpecialistAgentRole.RECOMMENDATIONS,
                status=prior_status,
                provider_mode=SpecialistProviderMode.ENVIRONMENT_AGENT,
                input_hash=digest(run.measurement.model_dump(mode="json")),
                job_id="previous-stage",
            ),)}),
        )
    orchestrator = ProjectMeasurementOrchestrator(repository, policy, settings)
    job, queued = orchestrator.regenerate_recommendations(
        ready, expected_revision=ready.revision, idempotency_key="regenerate-1",
    )
    assert queued.recommendations == ready.recommendations
    assert queued.recommendation_review == ready.recommendation_review
    assert queued.events[-1].event_type == "recommendations-regeneration-queued"
    replay, _ = orchestrator.regenerate_recommendations(
        queued, expected_revision=ready.revision, idempotency_key="regenerate-1",
    )
    assert replay.job_id == job.job_id
    with pytest.raises(Conflict, match="specialist stage is active"):
        orchestrator.regenerate_recommendations(
            queued, expected_revision=queued.revision, idempotency_key="regenerate-2",
        )
    hosted = Mock(spec=HostedSpecialistAgent)
    hosted.invoke.return_value = (
        proposal(page_optimisations=[], key_findings=[]),
        SpecialistAgentCall(
            role=SpecialistAgentRole.RECOMMENDATIONS,
            provider_mode=SpecialistProviderMode.ENVIRONMENT_AGENT,
            provider_response_id="regenerated-response",
        ),
    )
    baseline = Mock()
    browser = Mock()
    signals = TechnicalSignals(
        status="captured",
        reason="partial-html",
        crawled_at="2026-10-30T16:00:00Z",
        canonical=TechnicalField(
            state="present", value=str(ready.measurement.inputs.brief.url),
        ),
    )
    browser.browse_markup.return_value = signals
    result = Worker(repository, "regeneration-worker", {
        JobType.AGENT_STAGE: SpecialistStageHandler(
            repository, baseline, hosted, policy, browser,
        ),
    }).run_once()
    assert result is not None
    completed_job, completed = result
    assert completed_job.state == JobState.COMPLETED
    hosted.invoke.assert_called_once()
    assert hosted.invoke.call_args.args[0] == settings.binding(SpecialistAgentRole.RECOMMENDATIONS)
    baseline.recommend.assert_not_called()
    assert completed.measurement == ready.measurement
    assert completed.inputs == ready.inputs
    assert measurement_scores(completed.measurement) == measurement_scores(ready.measurement)
    assert completed.recommendations != ready.recommendations
    assert completed.recommendation_review is None
    assert completed.agent_stages[0].provider_response_id == "regenerated-response"
    claims = repository.list_operation_checkpoints(ready.run_id, ready.owner, job.job_id)
    assert [claim.operation_type for claim in claims] == [
        *(["recommendation-technical-browse"] if html_enabled else []),
        "recommendation-model",
    ]
    assert job.operation_ceiling == 1 + int(html_enabled)
    if html_enabled:
        browser.browse_markup.assert_called_once_with(ready.measurement.inputs.brief)
        assert completed.recommendations.technical_signals == signals
        assert hosted.invoke.call_args.args[3]["evidence_registry"]["tech.canonical"] == (
            str(ready.measurement.inputs.brief.url)
        )
    else:
        browser.browse_markup.assert_not_called()
    replay, current = orchestrator.regenerate_recommendations(
        completed, expected_revision=ready.revision, idempotency_key="regenerate-1",
    )
    assert replay.job_id == job.job_id
    assert current == completed
    assert Worker(repository, "regeneration-worker", {}).run_once() is None
    next_job, _ = orchestrator.regenerate_recommendations(
        completed, expected_revision=completed.revision, idempotency_key="regenerate-2",
    )
    assert next_job.job_id != job.job_id


def test_regeneration_failure_keeps_previous_recommendations_and_review(saved_run):
    _, repository, ready, _, policy, settings = saved_run
    ProjectMeasurementOrchestrator(repository, policy, settings).regenerate_recommendations(
        ready, expected_revision=ready.revision, idempotency_key="regenerate-failure",
    )
    hosted = Mock(spec=HostedSpecialistAgent)
    hosted.invoke.side_effect = ProviderError("Provider unavailable")
    result = Worker(repository, "regeneration-worker", {
        JobType.AGENT_STAGE: SpecialistStageHandler(repository, Mock(), hosted, policy),
    }).run_once()
    assert result is not None
    job, failed = result
    assert job.state == JobState.FAILED
    assert failed.measurement == ready.measurement
    assert failed.recommendations == ready.recommendations
    assert failed.recommendation_review == ready.recommendation_review


def test_regeneration_requires_live_policy_agent_evidence_and_current_revision(saved_run):
    _, repository, ready, _, policy, settings = saved_run
    with pytest.raises(Conflict, match="Live project measurement is not configured"):
        ProjectMeasurementOrchestrator(repository, None, settings).regenerate_recommendations(
            ready, expected_revision=ready.revision, idempotency_key="no-policy",
        )
    with pytest.raises(Conflict, match="Configure a Foundry recommendations agent"):
        ProjectMeasurementOrchestrator(repository, policy).regenerate_recommendations(
            ready, expected_revision=ready.revision, idempotency_key="no-agent",
        )
    orchestrator = ProjectMeasurementOrchestrator(repository, policy, settings)
    with pytest.raises(Conflict, match="Stale measurement revision"):
        orchestrator.regenerate_recommendations(
            ready, expected_revision=ready.revision - 1, idempotency_key="stale",
        )
    for invalid in (
        ready.model_copy(update={"measurement": None}),
        ready.model_copy(update={"state": MeasurementState.EVALUATING}),
        ready.model_copy(update={"measurement": ready.measurement.model_copy(update={"results": ()})}),
        ready.model_copy(update={"measurement": ready.measurement.model_copy(update={"retrievals": ()})}),
    ):
        with pytest.raises(Conflict, match="requires saved measurement evidence"):
            orchestrator.regenerate_recommendations(
                invalid, expected_revision=invalid.revision, idempotency_key="missing-evidence",
            )
    assert repository.list_run_jobs(ready.run_id, ready.owner) == ()


def test_regeneration_prefers_project_agent_binding(saved_run):
    _, repository, ready, project, policy, settings = saved_run
    binding = settings.binding(SpecialistAgentRole.RECOMMENDATIONS).model_copy(update={
        "agent_name": "ProjectRecommendations", "agent_version": "9",
    })
    repository.update_project(
        project.project_id, project.owner,
        ProjectUpdate(expected_revision=project.revision, specialist_agents=(SpecialistAgentBinding(
            role=SpecialistAgentRole.RECOMMENDATIONS, binding=binding,
        ),)),
    )
    job, _ = ProjectMeasurementOrchestrator(repository, policy, settings).regenerate_recommendations(
        ready, expected_revision=ready.revision, idempotency_key="project-agent",
    )
    assert job.request["provider_mode"] == "project-agent"
    assert job.request["binding"]["agent_version"] == "9"


def test_regeneration_api_requires_confirmation_auth_and_project_scope(saved_run):
    database, repository, ready, project, policy, settings = saved_run
    other = repository.create_project(
        ready.owner, ProjectCreate(name="Other", primary_domain="other.example"),
    )
    app = create_app(
        database, {TOKEN: "alice", "b" * 40: "bob"},
        measurement_policy=policy, specialist_foundry=settings,
    )
    route = f"/api/v2/projects/{project.project_id}/runs/{ready.run_id}/regenerate-recommendations"
    body = {
        "expected_revision": ready.revision,
        "idempotency_key": "api-regeneration",
        "confirm_provider_call": True,
    }
    with TestClient(app) as client:
        assert client.post(route, json=body).status_code == 401
        assert client.post(route, json=body, headers={
            "Authorization": "Bearer " + "b" * 40,
        }).status_code == 404
        client.headers["Authorization"] = "Bearer " + TOKEN
        assert client.post(
            route.replace(project.project_id, other.project_id), json=body,
        ).status_code == 404
        assert client.post(route, json={
            **body, "confirm_provider_call": False,
        }).status_code == 409
        assert client.post(route, json={
            **body, "expected_revision": ready.revision - 1,
        }).status_code == 409
        assert client.post(route, json={
            **body, "idempotency_key": "",
        }).status_code == 422
        accepted = client.post(route, json=body)
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["job"]["job_type"] == "agent-stage"
        assert accepted.json()["run"]["recommendations"] is not None
        replay = client.post(route, json=body)
        assert replay.status_code == 202
        assert replay.json()["job"]["job_id"] == accepted.json()["job"]["job_id"]
        conflict = client.post(route, json={
            **body,
            "expected_revision": accepted.json()["run"]["revision"],
            "idempotency_key": "second-regeneration",
        })
        assert conflict.status_code == 409
