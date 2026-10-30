import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from geo_agent.measurement_worker import create_runtime_from_environment, run_worker
from geo_agent.jobs import JobService, JobType
from geo_agent.measurement_workflow import MeasurementState
from geo_agent.projects import FoundryProjectBinding
from geo_agent.specialist_agents import (
    SpecialistAgentCall, SpecialistAgentRole, SpecialistProviderMode,
)
from geo_agent.specialist_workflow import SpecialistStageRequest
from geo_agent.technical_checks import TechnicalSignals
from geo_agent.webiq import ProviderError
from test_live_runtime import budget_grant, live_policy
from test_recommendation_plan import proposal
from test_recommendations import measurement


def worker_environment(tmp_path, *, policy=None, grant=None):
    execution_policy = policy or live_policy()
    measurement_grant = grant or budget_grant(execution_policy)
    policy_path = tmp_path / "measurement-policy.json"
    grant_path = tmp_path / "measurement-grant.json"
    policy_path.write_text(execution_policy.model_dump_json(indent=2), encoding="utf-8")
    grant_path.write_text(measurement_grant.model_dump_json(indent=2), encoding="utf-8")
    return {
        "GEO_MEASUREMENT_POLICY": str(policy_path),
        "GEO_MEASUREMENT_BUDGET_GRANT": str(grant_path),
        "GEO_DATA_DIR": str(tmp_path / "data"),
        "WEBIQ_API_KEY": "test-webiq-key",
        "AZURE_OPENAI_ENDPOINT": "https://geo-runtime.services.ai.azure.com/openai/v1/",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "test-preparation",
        "GEO_QUERY_AGENT_ENDPOINT": (
            "https://geo-runtime.services.ai.azure.com/api/projects/runtime/agents/"
            "MissionsAndMoments/endpoint/protocols/openai/responses"
        ),
        "GEO_QUERY_AGENT_VERSION": "7",
    }


def test_worker_startup_validates_configuration_without_auth_or_provider_calls(tmp_path):
    token_calls = []
    runtime = create_runtime_from_environment(
        worker_environment(tmp_path),
        token_provider=lambda: token_calls.append(True) or "unexpected-token",
    )
    try:
        assert token_calls == []
        assert runtime.run_once() is None
        assert token_calls == []
    finally:
        runtime.close()


def test_worker_accepts_direct_responses_endpoint_compatibility_fallback(tmp_path):
    environment = worker_environment(tmp_path)
    del environment["AZURE_OPENAI_ENDPOINT"]
    environment["AZURE_AI_PROJECT_ENDPOINT"] = (
        "https://geo-runtime.services.ai.azure.com/openai/v1/responses"
    )
    token_calls = []

    runtime = create_runtime_from_environment(
        environment,
        token_provider=lambda: token_calls.append(True) or "unexpected-token",
    )
    try:
        assert token_calls == []
        assert runtime.run_once() is None
    finally:
        runtime.close()


@pytest.mark.parametrize("missing", [
    "GEO_MEASUREMENT_POLICY",
    "GEO_MEASUREMENT_BUDGET_GRANT",
    "WEBIQ_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_AI_MODEL_DEPLOYMENT_NAME",
    "GEO_QUERY_AGENT_ENDPOINT",
    "GEO_QUERY_AGENT_VERSION",
])
def test_worker_startup_requires_every_live_setting(tmp_path, missing):
    environment = worker_environment(tmp_path)
    del environment[missing]

    with pytest.raises(ValueError, match=missing):
        create_runtime_from_environment(
            environment,
            token_provider=lambda: pytest.fail("Startup must not authenticate"),
        )


@pytest.mark.parametrize("html_enabled", [False, True])
def test_worker_startup_rejects_mismatched_grant_without_authentication(tmp_path, html_enabled):
    policy = live_policy().model_copy(update={"technical_browse": html_enabled})
    mismatched = budget_grant(policy).model_copy(update={"approval": "Unrecognised approval"})
    environment = worker_environment(tmp_path, policy=policy, grant=mismatched)
    payload = json.loads(Path(environment["GEO_MEASUREMENT_BUDGET_GRANT"]).read_text(encoding="utf-8"))
    payload["policy_hash"] = "0" * 64
    Path(environment["GEO_MEASUREMENT_BUDGET_GRANT"]).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        create_runtime_from_environment(
            environment,
            token_provider=lambda: pytest.fail("Startup must not authenticate"),
        )


@pytest.mark.parametrize("browse_fails", [False, True])
def test_html_worker_consumes_separate_allowances_and_blocks_exhausted_dispatch(
    tmp_path, browse_fails,
):
    policy = live_policy().model_copy(update={"technical_browse": True})
    grant = budget_grant(policy, recommendations=True)
    runtime = create_runtime_from_environment(
        worker_environment(tmp_path, policy=policy, grant=grant),
        token_provider=lambda: pytest.fail("Test must not authenticate"),
    )
    try:
        repository = runtime.repository
        saved = measurement()
        created = repository.create(grant.owner, saved.inputs)
        ready = repository.mutate(
            created.run_id, grant.owner, created.revision,
            lambda run: run.model_copy(update={
                "state": MeasurementState.READY, "measurement": saved,
            }),
        )
        handler = runtime.worker.handlers[JobType.AGENT_STAGE]
        browser = Mock()
        browser.browse_markup.return_value = TechnicalSignals(
            status="captured", reason="partial-html",
        )
        if browse_fails:
            browser.browse_markup.side_effect = ProviderError("Captured test failure")
        handler.technical_browser = browser
        hosted = Mock()
        hosted.invoke.return_value = (
            proposal(page_optimisations=[], key_findings=[]),
            SpecialistAgentCall(
                role=SpecialistAgentRole.RECOMMENDATIONS,
                provider_mode=SpecialistProviderMode.ENVIRONMENT_AGENT,
            ),
        )
        handler.hosted_agent = hosted
        request = SpecialistStageRequest(
            role=SpecialistAgentRole.RECOMMENDATIONS,
            provider_mode=SpecialistProviderMode.ENVIRONMENT_AGENT,
            binding=FoundryProjectBinding(
                project_endpoint="https://test.services.ai.azure.com/api/projects/test",
                agent_name="Recommendations", agent_version="4",
            ),
            confirm_provider_call=True,
        )
        for index in range(2):
            JobService(repository).enqueue(
                ready.run_id, grant.owner, ready.revision,
                JobType.AGENT_STAGE, f"html-stage-{index}", request,
                policy_id=policy.policy_id, policy_hash=policy.policy_hash,
                operation_ceiling=2,
            )
            job, ready = runtime.run_once()
            assert job.state == ("completed" if index == 0 else "failed")
            if index == 1:
                assert job.error_code == "Conflict"
        assert ready.recommendations.technical_signals.reason == (
            "browse-failed" if browse_fails else "partial-html"
        )
        browser.browse_markup.assert_called_once()
        hosted.invoke.assert_called_once()
        capacity = repository.measurement_capacity(grant.owner, policy)
        for operation in ("recommendation-technical-browse", "recommendation-model"):
            assert capacity.operations[operation].consumed == 1
            assert capacity.operations[operation].remaining == 0
        assert ready.measurement == saved
    finally:
        runtime.close()


def test_worker_loop_waits_when_queue_is_empty():
    calls = []

    class Runtime:
        def run_once(self):
            calls.append("run")
            return None

    class StopEvent:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            calls.append(seconds)
            self.stopped = True

    run_worker(Runtime(), StopEvent(), 0.25)
    assert calls == ["run", 0.25]


@pytest.mark.parametrize("poll_seconds", [0, 0.09, 60.01])
def test_worker_loop_rejects_unbounded_poll_interval(poll_seconds):
    with pytest.raises(ValueError, match="poll interval"):
        run_worker(None, None, poll_seconds)