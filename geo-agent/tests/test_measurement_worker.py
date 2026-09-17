import json
from pathlib import Path

import pytest

from geo_agent.measurement_worker import create_runtime_from_environment, run_worker
from test_live_runtime import budget_grant, live_policy


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
])
def test_worker_startup_requires_every_live_setting(tmp_path, missing):
    environment = worker_environment(tmp_path)
    del environment[missing]

    with pytest.raises(ValueError, match=missing):
        create_runtime_from_environment(
            environment,
            token_provider=lambda: pytest.fail("Startup must not authenticate"),
        )


def test_worker_startup_rejects_mismatched_grant_without_authentication(tmp_path):
    policy = live_policy()
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