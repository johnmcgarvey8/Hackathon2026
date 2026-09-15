from types import SimpleNamespace

import pytest

from geo_agent.fixtures import synthetic_inputs
from geo_agent.foundry import Foundry, PageAnalysis
from geo_agent.webiq import ProviderError
from geo_agent.page_analysis import AnalysisPolicy, AnalysisRequest, EvaluationBriefRequest, PageAnalysisService
from geo_agent.workflow import Conflict, NotFound, RunStore
from test_live import FakeFoundry, FakeWeb


def report_for(text="Organic farm shop"):
    finding = {"text": "The page describes a farm shop.", "basis": "observed",
               "evidence": [{"passage_id": "page-1", "quote": text}]}
    return PageAnalysis(purpose=finding, audience={**finding, "basis": "inferred"}, entities=[finding],
                        questions_answered=[], observations=[finding], improvements=[{
                            "hypothesis": "Clarify visitor details", "rationale": "Visitor context could help.",
                            "evidence": finding["evidence"], "verification": "Review the full page before editing."}])


@pytest.mark.parametrize("quote,valid", [("Organic farm shop", True), ("Invented quote", False), ("", False)])
def test_analysis_rejects_unsupported_quotes(monkeypatch, quote, valid):
    foundry = Foundry("https://test.openai.azure.com/openai/v1/", "test")
    response = SimpleNamespace(output_parsed=report_for(quote), model="test", id="response-test", usage=None)
    monkeypatch.setattr(foundry, "_parse", lambda *args: response)
    if valid:
        report, metadata = foundry.analyse_page(synthetic_inputs().snapshot, [{"passage_id": "page-1", "text": "Organic farm shop"}])
        assert report.audience.basis == "inferred"
        assert metadata["response_id"] == "response-test"
    else:
        with pytest.raises(ProviderError, match="unsupported"):
            foundry.analyse_page(synthetic_inputs().snapshot, [{"passage_id": "page-1", "text": "Organic farm shop"}])


def service_for(tmp_path):
    foundry = FakeFoundry()
    def analyse(snapshot, passages):
        foundry.calls.append("analyse")
        return report_for(passages[0]["text"][:30]), {"model": "test", "response_id": "analysis-test"}
    foundry.analyse_page = analyse
    policy = AnalysisPolicy(policy_id="page-test", owner="alice", endpoint=foundry.base_url, deployment=foundry.deployment,
                            max_analyses=2, max_evaluations=1, approval="Test authorisation")
    return PageAnalysisService(RunStore(tmp_path / "runs.sqlite3"), policy, FakeWeb(), foundry, url_validator=lambda value: value)


def test_runtime_endpoint_can_differ_from_analysis_policy(tmp_path):
    service = service_for(tmp_path)
    policy = service.policy.model_copy(update={"endpoint": "https://policy.openai.azure.com/openai/v1/"})
    configured = PageAnalysisService(
        RunStore(tmp_path / "other.sqlite3"), policy, service.webiq, service.foundry
    )
    assert configured.foundry.base_url == "https://test.openai.azure.com/openai/v1/"


def test_analysis_persists_and_replays_without_new_calls(tmp_path):
    service = service_for(tmp_path)
    request = AnalysisRequest(url=synthetic_inputs().brief.url, idempotency_key="first")
    result = service.analyse(request, "alice")
    assert result["status"] == "completed" and result["retrieval_status"] == "completed"
    assert result["evaluation"] is None
    assert service.analyse(request, "alice") == result
    restarted = PageAnalysisService(service.store, service.policy, service.webiq, service.foundry)
    assert restarted.get(result["analysis_id"], "alice") == result
    assert restarted.budget()["analyses_remaining"] == 1
    assert service.webiq.calls == ["browse"] and service.foundry.calls == ["analyse"]
    with pytest.raises(NotFound):
        service.get(result["analysis_id"], "bob")
    with pytest.raises(Conflict, match="different"):
        service.analyse(request.model_copy(update={"locale": "en-US"}), "alice")
    with pytest.raises(Conflict, match="cannot be reset"):
        PageAnalysisService(service.store, service.policy.model_copy(update={"max_analyses": 3}), service.webiq, service.foundry)


def test_retrieval_failure_never_calls_model_or_retries(tmp_path):
    service = service_for(tmp_path)
    def reject(value):
        raise ProviderError("URL policy rejected the page")
    service.validate_url = reject
    request = AnalysisRequest(url="http://127.0.0.1/", idempotency_key="blocked")
    result = service.analyse(request, "alice")
    assert result["retrieval_status"] == "failed" and result["analysis_status"] == "not-started"
    assert service.analyse(request, "alice") == result
    assert service.webiq.calls == [] and service.foundry.calls == []


def test_citation_handoff_reuses_snapshot_and_requires_exact_approval(tmp_path):
    service = service_for(tmp_path)
    analysis = service.analyse(AnalysisRequest(url=synthetic_inputs().brief.url, idempotency_key="analysis"), "alice")
    brief = EvaluationBriefRequest(audience="Visitors", goal="Measure citations", idempotency_key="evaluation", confirm_query_generation=True)
    prepared = service.prepare_evaluation(analysis["analysis_id"], brief, "alice")
    assert prepared["status"] == "awaiting-query-approval"
    assert service.prepare_evaluation(analysis["analysis_id"], brief, "alice") == prepared
    assert service.webiq.calls == ["browse"]
    assert service.foundry.calls == ["analyse", "propose"]
    run = service.store.get(prepared["run_id"], "alice")
    with pytest.raises(Conflict, match="approval"):
        service.execute(run.run_id, "alice", 1, "start")
    from geo_agent.workflow import Coordinator
    Coordinator(service.store).approve(run.run_id, "alice", 1, run.inputs.approval_hash)
    assert service.execute(run.run_id, "alice", 1, "start").state == "recommendations-ready"
    service.execute(run.run_id, "alice", 1, "start")
    assert service.webiq.calls.count("search") == 5
    assert service.foundry.calls.count("evaluate") == 5
    assert service.budget()["evaluations_remaining"] == 0


def test_browser_api_flow_is_authenticated_and_approval_gated(tmp_path):
    from fastapi.testclient import TestClient
    from geo_agent.api import create_app
    service = service_for(tmp_path)
    with TestClient(create_app(service.store.path, {"a" * 40: "alice", "b" * 40: "bob"}, analysis=service),
                    headers={"Authorization": "Bearer " + "a" * 40}) as client:
        assert client.get("/analysis-policy", headers={"Authorization": ""}).status_code == 401
        assert client.get("/analysis-policy", headers={"Authorization": "Bearer " + "b" * 40}).status_code == 404
        response = client.post("/page-analyses", json={"url": str(synthetic_inputs().brief.url), "idempotency_key": "browser"})
        assert response.status_code == 201
        record = response.json()
        assert client.get("/page-analyses").json()[0]["analysis_id"] == record["analysis_id"]
        route = f"/page-analyses/{record['analysis_id']}"
        assert client.get(route, headers={"Authorization": "Bearer " + "b" * 40}).status_code == 404
        brief = {"audience": "Visitors", "goal": "Measure citations", "idempotency_key": "brief"}
        assert client.post(route + "/evaluation", json=brief).status_code == 422
        prepared = client.post(route + "/evaluation", json={**brief, "confirm_query_generation": True}).json()
        run_route = f"/runs/{prepared['run_id']}"
        run = client.get(run_route).json()
        start = {"expected_revision": 1, "idempotency_key": "start"}
        assert client.post(run_route + "/start", json=start).status_code == 409
        assert client.post(run_route + "/query-approval", json={"expected_revision": 1, "input_hash": "0" * 64}).status_code == 409
        assert service.webiq.calls == ["browse"]
        assert client.post(run_route + "/query-approval", json={"expected_revision": 1, "input_hash": run["approval_hash"]}).status_code == 200
        result = client.post(run_route + "/start", json=start)
        assert result.status_code == 200 and len(result.json()["results"]) == 5
        assert client.get("/analysis-policy").json()["budget"]["evaluations_remaining"] == 0


def test_analysis_slots_are_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    service = service_for(tmp_path)
    def attempt(index):
        try:
            return service.analyse(AnalysisRequest(url=synthetic_inputs().brief.url, idempotency_key=f"attempt-{index}"), "alice")
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(attempt, range(8)))
    assert len([result for result in results if result]) == 2
    assert service.webiq.calls.count("browse") == 2
    assert service.foundry.calls.count("analyse") == 2
    assert service.budget()["analyses_remaining"] == 0


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/", "http://localhost/", "https://user:password@example.com/"])
def test_real_url_guard_prevents_provider_calls(tmp_path, url):
    from geo_agent.webiq import public_url
    service = service_for(tmp_path)
    service.validate_url = public_url
    record = service.analyse(AnalysisRequest(url=url, idempotency_key="reject"), "alice")
    assert record["retrieval_status"] == "failed"
    assert service.webiq.calls == [] and service.foundry.calls == []


@pytest.mark.parametrize("stage", ["browse", "analyse_page"])
def test_provider_failure_is_saved_with_no_retry_or_evaluation(tmp_path, stage):
    service = service_for(tmp_path)
    def fail(*args):
        raise ProviderError("Provider unavailable; no automatic retry")
    setattr(service.webiq if stage == "browse" else service.foundry, stage, fail)
    request = AnalysisRequest(url=synthetic_inputs().brief.url, idempotency_key="failure")
    record = service.analyse(request, "alice")
    assert record["status"] == "failed" and record["report"] is None
    assert (record["snapshot"] is None) == (stage == "browse")
    assert service.analyse(request, "alice") == record
    brief = EvaluationBriefRequest(audience="Visitors", goal="Measure", idempotency_key="blocked", confirm_query_generation=True)
    with pytest.raises(Conflict, match="Complete"):
        service.prepare_evaluation(record["analysis_id"], brief, "alice")
    assert service.budget()["evaluations_claimed"] == 0


def test_unknown_passage_and_excessive_findings_rejected(monkeypatch):
    foundry = Foundry("https://test.openai.azure.com/openai/v1/", "test")
    report = report_for()
    response = SimpleNamespace(output_parsed=report, model="test", id="response-test", usage=None)
    monkeypatch.setattr(foundry, "_parse", lambda *args: response)
    with pytest.raises(ProviderError, match="unsupported"):
        foundry.analyse_page(synthetic_inputs().snapshot, [{"passage_id": "different-page", "text": "Organic farm shop"}])
    report.entities = [report.purpose] * 3
    with pytest.raises(ProviderError, match="finding limit"):
        foundry.analyse_page(synthetic_inputs().snapshot, [{"passage_id": "page-1", "text": "Organic farm shop"}])