from collections import Counter
from unittest.mock import Mock

import pytest

from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import JobService, JobType
from geo_agent.measurement_workflow import MeasurementState, OwnerIdentity
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.projects import FoundryProjectBinding
from geo_agent.specialist_agents import (
    SpecialistAgentCall, SpecialistAgentRole, SpecialistProviderMode,
)
from geo_agent.specialist_workflow import SpecialistStageHandler, SpecialistStageRequest
from geo_agent.technical_checks import TechnicalField, TechnicalSignals
from geo_agent.webiq import ProviderError
from geo_agent.worker import Worker

from test_recommendation_plan import proposal
from test_recommendations import measurement


class TechnicalBrowser:
    def __init__(self, result):
        self.result = result

    def browse_markup(self, brief):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def handler_with(browser):
    handler = object.__new__(SpecialistStageHandler)
    handler.technical_browser = browser
    return handler


def test_technical_browse_operation_returns_claim_compatible_result_and_metadata():
    signals = TechnicalSignals(
        status="captured",
        canonical=TechnicalField(state="present", value="https://target.example/visit"),
    )

    result, metadata = handler_with(TechnicalBrowser(signals))._browse_technical(
        measurement(),
    )

    assert result == signals
    assert metadata == {"status": "captured", "reason": ""}


def test_failed_technical_browse_is_recorded_without_failing_recommendations():
    result, metadata = handler_with(
        TechnicalBrowser(ProviderError("browse failed")),
    )._browse_technical(measurement())

    assert result == TechnicalSignals(reason="browse-failed")
    assert metadata == {"status": "not-captured", "reason": "browse-failed"}


def test_unavailable_technical_browser_is_explicit():
    result, metadata = handler_with(None)._browse_technical(measurement())

    assert result == TechnicalSignals(reason="provider-unavailable")
    assert metadata == {
        "status": "not-captured",
        "reason": "provider-unavailable",
    }


@pytest.mark.parametrize("enabled", [True, False])
def test_policy_gates_html_claim_and_persists_signals_sent_to_planner(tmp_path, enabled):
    saved = measurement()
    owner = OwnerIdentity(tenant_id="tenant-a", object_id="user-a")
    database = tmp_path / "technical-stage.sqlite3"
    repository = SQLiteMeasurementRepository(database)
    created = repository.create(owner, saved.inputs)
    ready = repository.mutate(
        created.run_id, owner, created.revision,
        lambda current: current.model_copy(update={
            "state": MeasurementState.READY, "measurement": saved,
        }),
    )
    policy = MeasurementExecutionPolicy(
        policy_id="technical-stage-test", scope="project-bound",
        profiles=saved.inputs.profiles, technical_browse=enabled,
    )
    signals = TechnicalSignals(
        status="captured", reason="partial-html", browse_trace_id="html-trace",
        canonical=TechnicalField(state="present", value=str(saved.inputs.brief.url)),
    )
    browser = Mock(spec=TechnicalBrowser)
    browser.browse_markup.return_value = signals
    hosted = Mock()
    hosted.invoke.return_value = (
        proposal(page_optimisations=[], key_findings=[]),
        SpecialistAgentCall(
            role=SpecialistAgentRole.RECOMMENDATIONS,
            provider_mode=SpecialistProviderMode.ENVIRONMENT_AGENT,
        ),
    )
    job, _ = JobService(repository).enqueue(
        ready.run_id, owner, ready.revision, JobType.AGENT_STAGE, "technical-stage",
        SpecialistStageRequest(
            role=SpecialistAgentRole.RECOMMENDATIONS,
            provider_mode=SpecialistProviderMode.ENVIRONMENT_AGENT,
            binding=FoundryProjectBinding(
                project_endpoint="https://test.services.ai.azure.com/api/projects/test",
                agent_name="Recommendations", agent_version="4",
            ),
            confirm_provider_call=True,
        ),
        operation_ceiling=1 + int(enabled),
    )
    result = Worker(repository, "technical-worker", {
        JobType.AGENT_STAGE: SpecialistStageHandler(
            repository, Mock(), hosted, policy=policy, technical_browser=browser,
        ),
    }).run_once()

    assert result is not None and result[0].state == "completed"
    hosted.invoke.assert_called_once()
    expected = signals if enabled else TechnicalSignals(reason="feature-disabled")
    context = hosted.invoke.call_args.args[3]
    assert context["technical_signals"] == expected.model_dump(mode="json")
    persisted = repository.get(ready.run_id, owner)
    assert persisted.recommendations.technical_signals == expected
    assert persisted.recommendations.technical_findings.findings == ()
    if enabled:
        browser.browse_markup.assert_called_once_with(saved.inputs.brief)
    else:
        browser.browse_markup.assert_not_called()
    checkpoints = repository.list_operation_checkpoints(ready.run_id, owner, job.job_id)
    claims = Counter(claim.operation_type for claim in checkpoints)
    assert claims == {
        "recommendation-model": 1,
        **({"recommendation-technical-browse": 1} if enabled else {}),
    }
    repository.close()
