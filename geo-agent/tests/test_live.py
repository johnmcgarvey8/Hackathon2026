from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from geo_agent.api import create_app
from geo_agent.contracts import EvaluationResult, Provenance
from geo_agent.fixtures import synthetic_inputs
from geo_agent.foundry import Foundry
from geo_agent.live import BudgetedLiveWorkflow, LivePolicy, LiveWorkflow
from geo_agent.budget import BudgetGrant, apply_grant
from geo_agent.webiq import ProviderError
from geo_agent.workflow import Conflict, RunStore


class FakeWeb:
    def __init__(self):
        self.calls = []
        self.fail = False

    def browse(self, brief):
        self.calls.append("browse")
        return synthetic_inputs().snapshot.model_copy(update={"provenance": Provenance.LIVE})

    def search(self, query, locale):
        self.calls.append("search")
        if self.fail:
            raise ProviderError("Web IQ returned HTTP 403; no automatic retry")
        return ()


class FakeFoundry(Foundry):
    def __init__(self):
        super().__init__("https://test.openai.azure.com/openai/v1/", "test")
        self.calls = []

    def propose(self, brief, snapshot):
        self.calls.append("propose")
        return synthetic_inputs().queries, {"model": "test-v1", "response_id": "response-query", "input_tokens": 10, "output_tokens": 10}

    def evaluate(self, query, locale, sources):
        self.calls.append("evaluate")
        return EvaluationResult(query_id=query.query_id, profile_id=self.profile.profile_id, provenance="live", status="completed", answer="Insufficient evidence", sources=sources)


@pytest.fixture
def live(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite3")
    policy = LivePolicy(policy_id="test-policy", owner="alice", brief=synthetic_inputs().brief, deployment="test", endpoint="https://test.openai.azure.com/openai/v1/")
    return LiveWorkflow(store, policy, FakeWeb(), FakeFoundry())


def test_runtime_endpoint_can_differ_from_policy(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite3")
    policy = LivePolicy(
        policy_id="test-policy", owner="alice", brief=synthetic_inputs().brief, deployment="test",
        endpoint="https://policy.openai.azure.com/openai/v1/",
    )
    workflow = LiveWorkflow(store, policy, FakeWeb(), FakeFoundry())
    assert workflow.foundry.base_url == "https://test.openai.azure.com/openai/v1/"


def test_full_live_flow_is_gated_and_bounded(live):
    run = live.prepare(live.policy.brief, "alice", "prepare")
    with pytest.raises(Conflict, match="approval"):
        live.execute(run.run_id, "alice", 1, "start")
    assert live.webiq.calls == ["browse"]
    live.coordinator.approve(run.run_id, "alice", 1, run.inputs.approval_hash)
    finished = live.execute(run.run_id, "alice", 1, "start")
    assert len(finished.results) == 5
    assert finished.state == "recommendations-ready"
    assert live.webiq.calls.count("search") == 5
    assert live.foundry.calls == ["propose"] + ["evaluate"] * 5
    assert live.execute(run.run_id, "alice", 1, "start") == finished
    assert live.prepare(live.policy.brief, "alice", "prepare") == finished
    with pytest.raises(Conflict):
        live.prepare(live.policy.brief, "alice", "different")
    assert live.webiq.calls.count("browse") == 1


def test_failed_search_preserves_missing_results_without_extra_calls(live):
    run = live.prepare(live.policy.brief, "alice", "prepare")
    live.coordinator.approve(run.run_id, "alice", 1, run.inputs.approval_hash)
    live.webiq.fail = True
    finished = live.execute(run.run_id, "alice", 1, "start")
    assert finished.state == "failed"
    assert all(result.status == "error" for result in finished.results)
    assert live.webiq.calls.count("search") == 1
    assert live.foundry.calls == ["propose"]


def test_wrong_owner_or_page_makes_no_calls(live):
    with pytest.raises(Conflict):
        live.prepare(live.policy.brief, "bob", "prepare")
    changed = live.policy.brief.model_copy(update={"goal": "Different"})
    with pytest.raises(Conflict):
        live.prepare(changed, "alice", "prepare")
    assert live.webiq.calls == []


def test_claim_is_atomic_and_persistent(live):
    with ThreadPoolExecutor(max_workers=4) as executor:
        claimed = list(executor.map(lambda _: live._claim("test-operation", {}), range(4)))
    assert sum(claimed) == 1
    restarted = LiveWorkflow(live.store, live.policy, live.webiq, live.foundry)
    assert restarted._claim("test-operation", {}) is False


def test_api_live_preparation(live):
    with TestClient(create_app(live.store.path, {"a" * 40: "alice"}, live), headers={"Authorization": "Bearer " + "a" * 40}) as client:
        body = {**live.policy.brief.model_dump(mode="json"), "idempotency_key": "prepare"}
        result = client.post("/briefs", json=body)
        assert result.status_code == 201
        run = result.json()
        assert run["inputs"]["snapshot"]["provenance"] == "live"
        assert run["approval"] is None
        assert client.get("/live-policy").status_code == 200
        assert client.post(f"/runs/{run['run_id']}/start", json={"expected_revision": 1, "idempotency_key": "start"}).status_code == 409


def granted_batch(live):
    apply_grant(live.store, BudgetGrant(grant_id="ten-runs", owner="alice", live_policy_id=live.policy.policy_id,
                                       chat_policy_id="chat", additional_live_runs=10, additional_chat_requests=30,
                                       approval="User approved ten more full runs"))
    return BudgetedLiveWorkflow(live)


def test_ten_additional_runs_without_reset_or_approval_bypass(live):
    original = live.prepare(live.policy.brief, "alice", "original")
    batch = granted_batch(live)
    assert batch.prepare(live.policy.brief, "alice", "original") == original
    for index in range(10):
        run = batch.prepare(live.policy.brief, "alice", f"new-{index}")
        with pytest.raises(Conflict, match="approval"):
            batch.execute(run.run_id, "alice", 1, "start")
        live.coordinator.approve(run.run_id, "alice", 1, run.inputs.approval_hash)
        finished = batch.execute(run.run_id, "alice", 1, "start")
        assert finished.state == "recommendations-ready"
        assert batch.execute(run.run_id, "alice", 1, "start") == finished
        assert batch.prepare(live.policy.brief, "alice", f"new-{index}") == finished
    restarted = BudgetedLiveWorkflow(live)
    assert restarted.budget()["remaining_runs"] == 0
    with pytest.raises(Conflict, match="exhausted"):
        restarted.prepare(live.policy.brief, "alice", "eleventh")
    assert live.webiq.calls.count("browse") == 11
    assert live.webiq.calls.count("search") == 50
    assert live.foundry.calls.count("propose") == 11
    assert live.foundry.calls.count("evaluate") == 50
    assert live.store.get(original.run_id, "alice") == original


def test_run_slots_are_atomic_and_failed_attempts_not_refunded(live):
    batch = granted_batch(live)

    def prepare(index):
        try:
            return batch.prepare(live.policy.brief, "alice", f"key-{index}").run_id
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(prepare, range(15)))
    assert len({result for result in results if result}) == 10
    assert batch.budget()["claimed_runs"] == 10
    assert live.webiq.calls.count("browse") == 10
    run = live.store.get(next(result for result in results if result), "alice")
    live.coordinator.approve(run.run_id, "alice", 1, run.inputs.approval_hash)
    live.webiq.fail = True
    assert batch.execute(run.run_id, "alice", 1, "start").state == "failed"
    assert batch.budget()["remaining_runs"] == 0
    with pytest.raises(Conflict):
        batch.prepare(live.policy.brief, "bob", "other")


def test_additional_budget_api_still_requires_query_approval(live):
    batch = granted_batch(live)
    with TestClient(create_app(live.store.path, {"a" * 40: "alice", "b" * 40: "bob"}, batch), headers={"Authorization": "Bearer " + "a" * 40}) as client:
        assert client.get("/live-budget").json()["remaining_runs"] == 10
        assert client.get("/live-budget", headers={"Authorization": "Bearer " + "b" * 40}).status_code == 404
        assert client.get("/live-budget", headers={"Authorization": ""}).status_code == 401
        run = client.post("/briefs", json={**live.policy.brief.model_dump(mode="json"), "idempotency_key": "new-run"}).json()
        assert client.get("/live-budget").json()["remaining_runs"] == 9
        assert client.post(f"/runs/{run['run_id']}/start", json={"expected_revision": 1, "idempotency_key": "start"}).status_code == 409