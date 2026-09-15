import io
import json
import zipfile
from collections import Counter
from datetime import datetime, timezone

import httpx
import pytest
import yaml

from geo_agent.artifacts import render_bundle, render_measurement_bundle, score_report
from geo_agent.contracts import (
    EvaluationResult, MeasurementInputs, MeasurementResults, QueryPlan, RetrievalResult,
    Run, SimulationProfile, Source, State, digest,
)
from geo_agent.evaluation import measurement_scores
from geo_agent.fixtures import evaluate_synthetic, synthetic_inputs
from geo_agent.foundry import Foundry
from geo_agent.providers import create_evaluator, simulation_instructions
from geo_agent.recommendations import RecommendationReport, RecommendationService, validate_recommendations
from geo_agent.webiq import BROWSE_ENDPOINT, SEARCH_ENDPOINT, ProviderError, WebIQ, public_url
from test_foundry import response_payload
from test_providers import claude_payload


def test_branded_queries_are_reported_separately():
    inputs = synthetic_inputs()
    queries = tuple(query.model_copy(update={"branded": index == 0}) for index, query in enumerate(inputs.queries))
    run = Run(owner="alice", inputs=inputs.model_copy(update={"queries": queries}))
    run = run.model_copy(update={"results": evaluate_synthetic(run)})
    report = score_report(run)
    assert report["overall"]["score"] == 40
    assert report["by_query_type"]["branded"]["score"] == 100
    assert report["by_query_type"]["branded"]["denominator"] == 1
    assert report["by_query_type"]["unbranded"]["score"] == 25
    assert report["by_query_type"]["unbranded"]["denominator"] == 4


@pytest.fixture
def measurement():
    legacy = synthetic_inputs()
    captured = datetime(2026, 9, 15, tzinfo=timezone.utc)
    snapshot = legacy.snapshot.model_copy(update={"captured_at": captured})
    plan = QueryPlan(queries=tuple({
        "query_id": query.query_id, "priority": index, "rationale": "Saved page supports this intent",
        "intent": query.intent, "branded": index == 1, "chat_query": query.text,
        "grounding_query": f"fixture search {index}",
        "evidence": [{"evidence_id": "page-1", "quote": snapshot.content[:40]}],
    } for index, query in enumerate(legacy.queries, start=1)))
    profiles = tuple(SimulationProfile(
        profile_id=profile_id, provider="anthropic-messages" if profile_id == "claude-backed" else "openai-responses",
        endpoint="https://fixture.services.ai.azure.com/anthropic" if profile_id == "claude-backed"
        else "https://fixture.openai.azure.com/openai/v1/", deployment=f"fixture-{profile_id}",
        prompt_version="fixture/v1", instructions="Synthetic test data; never invoke a provider.",
    ) for profile_id in ("chatgpt-style", "claude-backed", "copilot-style"))
    inputs = MeasurementInputs(brief=legacy.brief, snapshot=snapshot, query_plan=plan,
                               profiles=profiles, policy_hash=digest({"fixture": True}))
    packets = tuple(RetrievalResult(
        query_id=query.query_id, grounding_query=query.grounding_query, provenance="synthetic",
        status="completed", retrieved_at=captured,
        sources=(Source(evidence_id=f"{query.query_id}-alternative", url="https://alternative.example/guide",
                        excerpt="A clear buyer comparison checklist.", provenance="synthetic", returned_position=1),
                 Source(evidence_id=f"{query.query_id}-target", url=inputs.brief.url,
                        excerpt=snapshot.content[:500], provenance="synthetic", returned_position=2)),
    ) for query in plan.queries)
    results = tuple(EvaluationResult(
        query_id=packet.query_id, profile_id=profile.profile_id, provenance="synthetic", status="completed",
        answer="Synthetic answer, not a provider response.", sources=packet.sources,
        citation_ids=(packet.sources[1 if index < 2 else 0].evidence_id,),
    ) for index, packet in enumerate(packets) for profile in profiles)
    return MeasurementResults(inputs=inputs, retrievals=packets, results=results)


def recommendation_report(measurement):
    return validate_recommendations(measurement, {"reason": "", "tasks": [{
        "task_id": "rec-1", "priority": 1, "query_id": "q-1", "title": "Review buyer guidance",
        "target_section": "Buyer information", "proposed_change": "Draft original comparison criteria after review.",
        "rationale": "The retrieved alternative has a checklist in its saved excerpt.", "confidence": "low",
        "verification": "Check target facts and approve a separate repeat measurement after any edit.",
        "page_evidence": [{"evidence_id": "page-1", "quote": measurement.inputs.snapshot.content[:40]}],
        "comparison_evidence": [{"evidence_id": "q-1-alternative", "quote": "buyer comparison checklist"}],
    }]})


def test_measurement_bundle_recomputes_scores_and_validates_recommendations(measurement):
    recommendations = recommendation_report(measurement)
    bundle = render_measurement_bundle(measurement, recommendations)
    assert bundle == render_measurement_bundle(measurement, recommendations)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert archive.namelist() == sorted(("manifest.json", "manifest.md", "measurement.json", "scores.json", "recommendations.json"))
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
        restored = MeasurementResults.model_validate_json(archive.read("measurement.json"))
        report = RecommendationReport.model_validate_json(archive.read("recommendations.json"))
        report.validate_for(restored)
        scores = json.loads(archive.read("scores.json"))
        assert scores == measurement_scores(restored)
        assert scores["overall"]["score"] == 40 and scores["overall"]["denominator"] == 15
        assert scores["retrieval"]["score"] == 100
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == "geo-measurement-manifest/v1"
        assert manifest["measurement_hash"] == digest(restored.model_dump(mode="json"))
        assert manifest["input_hash"] == restored.inputs.approval_hash
        assert manifest["provenance"] == "synthetic" and manifest["provisional"] is False
        assert manifest["publish_permission"] is False and manifest["requires_human_approval"] is True
        assert "owner" not in manifest and "approval" not in manifest
        assert b"not proof of human approval" in archive.read("manifest.md")


def test_measurement_bundle_rejects_stale_recommendations_and_forged_sources(measurement):
    recommendations = recommendation_report(measurement)
    changed = measurement.model_copy(update={"results": measurement.results[:-1]})
    assert changed.inputs.approval_hash == measurement.inputs.approval_hash
    with pytest.raises(ProviderError):
        render_measurement_bundle(changed, recommendations)
    changed_answer = measurement.results[0].model_copy(update={"sources": ()})
    forged = measurement.model_copy(update={"results": (changed_answer,) + measurement.results[1:]})
    with pytest.raises(ValueError):
        render_measurement_bundle(forged)


def test_measurement_without_recommendations_keeps_retrieval_when_answers_fail(measurement):
    failures = tuple(result.model_copy(update={"status": "error", "answer": "", "citation_ids": (),
                                             "error": "Provider failed; no retry"}) for result in measurement.results)
    changed = measurement.model_copy(update={"results": failures})
    with zipfile.ZipFile(io.BytesIO(render_measurement_bundle(changed))) as archive:
        assert json.loads(archive.read("recommendations.json")) is None
        scores = json.loads(archive.read("scores.json"))
        assert scores["overall"]["score"] is None and scores["overall"]["errors"] == 15
        assert scores["retrieval"]["score"] == 100 and scores["retrieval"]["coverage"] == 1
        assert scores["common_completed_query_ids"] == []
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["recommendation_status"] == "not-generated" and manifest["provisional"] is True


def test_legacy_export_still_uses_original_manifest_contract():
    run = Run(owner="private-owner", inputs=synthetic_inputs())
    with pytest.raises(ValueError, match="frozen exported"):
        render_bundle(run)
    run = run.model_copy(update={"state": State.EXPORTED, "results": evaluate_synthetic(run)})
    bundle = render_bundle(run)
    assert bundle == render_bundle(run)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert archive.namelist() == ["manifest.md", "run.json", "scores.json"]
        manifest = yaml.safe_load(archive.read("manifest.md").decode().split("---", 2)[1])
        assert manifest["schema_version"] == "geo-manifest/v2" and manifest["tasks"] == []
        assert json.loads(archive.read("run.json"))["schema_version"] == "geo-run/v1"
        assert "owner" not in json.loads(archive.read("run.json"))
        assert json.loads(archive.read("scores.json"))["overall"]["score"] == 40


@pytest.mark.parametrize("failed_profile", [None, "claude-backed"])
def test_mocked_six_stage_components_route_and_export_without_live_calls(measurement, monkeypatch, failed_profile):
    def forbidden(*args, **kwargs):
        pytest.fail("Integration test must not authenticate or open a network connection")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    calls = Counter()
    answer_packets = {}
    plan = measurement.inputs.query_plan
    private_page_text = measurement.inputs.snapshot.content + " PRIVATE-SNAPSHOT-ONLY"
    quote = measurement.inputs.snapshot.content[:40]
    profiles = tuple(profile.model_copy(update={"instructions": simulation_instructions(profile.profile_id)})
                     for profile in measurement.inputs.profiles)
    query_by_chat = {query.chat_query: query for query in plan.queries}
    query_by_search = {query.grounding_query: query for query in plan.queries}
    profile_by_deployment = {profile.deployment: profile for profile in profiles}
    drafts = [{key: value for key, value in task.model_dump(mode="json").items()
               if key not in {"schema_version", "status", "requires_human_approval", "publish_permission"}}
              for task in recommendation_report(measurement).tasks]
    drafts[0]["comparison_evidence"][0]["evidence_id"] = "q-1-source-1"

    def handler(request):
        body = json.loads(request.content)
        url = str(request.url)
        if url == BROWSE_ENDPOINT:
            calls["browse"] += 1
            assert body["liveCrawl"] == "none" and body["maxLength"] == 10000
            assert body["url"] == str(measurement.inputs.brief.url)
            return httpx.Response(200, json={"url": body["url"], "title": "Fixture page",
                                            "content": private_page_text, "traceId": "mock-browse"})
        if url == SEARCH_ENDPOINT:
            calls["search"] += 1
            query = query_by_search[body["query"]]
            assert body["contentFormat"] == "passage" and body["maxResults"] == 5
            packet = measurement.retrievals[int(query.query_id[-1]) - 1]
            return httpx.Response(200, json={"traceId": f"mock-{query.query_id}", "webResults": [
                {"url": str(source.url), "title": source.title, "content": source.excerpt}
                for source in packet.sources
            ]})
        assert "tools" not in body and "previous_response_id" not in body
        schema = body.get("text", {}).get("format", {}).get("name")
        if schema in {"PageAnalysis", "QueryPlan", "RecommendationProposal"}:
            assert url == "https://fixture.openai.azure.com/openai/v1/responses"
            assert body["store"] is False and body["max_output_tokens"] == 2000
            assert body["model"] == "fixture-planner"
            calls[schema] += 1
            if schema == "PageAnalysis":
                finding = {"text": "Buyer planning", "basis": "inferred",
                           "evidence": [{"passage_id": "page-1", "quote": quote}]}
                output = {"purpose": finding, "audience": finding, "entities": [],
                          "questions_answered": [], "observations": [], "improvements": []}
            elif schema == "QueryPlan":
                output = plan.model_dump(mode="json")
            else:
                context = json.loads(body["input"])
                assert len(context["comparison_sources"]) == 5
                assert all(item["reasons"] for item in context["comparison_sources"])
                output = {"tasks": drafts, "reason": ""}
            return httpx.Response(200, json=response_payload(output))
        profile = profile_by_deployment[body["model"]]
        calls[profile.profile_id] += 1
        if profile.provider == "anthropic-messages":
            assert url == profile.endpoint + "/v1/messages" and body["max_tokens"] == 2000
            assert body["system"] == profile.instructions
            payload = json.loads(body["messages"][0]["content"])
        else:
            assert url == profile.endpoint + "responses"
            assert body["store"] is False and body["max_output_tokens"] == 2000
            assert body["instructions"] == profile.instructions
            payload = json.loads(body["input"])
        assert set(payload) == {"query", "locale", "untrusted_search_evidence"}
        assert "PRIVATE-SNAPSHOT-ONLY" not in str(body)
        query = query_by_chat[payload["query"]]
        assert query.grounding_query not in str(body)
        sources = payload["untrusted_search_evidence"]
        assert [source["returned_position"] for source in sources] == [1, 2]
        previous = answer_packets.setdefault(query.query_id, sources)
        assert sources == previous
        if failed_profile == profile.profile_id and query.query_id == "q-1":
            return httpx.Response(503, json={"error": {"message": "dummy-sensitive-provider-details"}})
        citation = sources[1 if query.priority <= 2 else 0]["evidence_id"]
        output = {"answer": f"Mock answer [{citation}]", "citation_ids": [citation]}
        response = claude_payload(output) if profile.provider == "anthropic-messages" else response_payload(output)
        return httpx.Response(200, json=response)

    transport = httpx.MockTransport(handler)
    webiq = WebIQ("dummy-key", transport=transport, url_validator=lambda url: public_url(url, resolve=False))
    foundry = Foundry("https://fixture.openai.azure.com/openai/v1/", "fixture-planner",
                      token_provider=lambda: "dummy-token", transport=transport)
    snapshot = webiq.browse(measurement.inputs.brief)
    analysis, analysis_call = foundry.analyse_page(snapshot, [{"passage_id": "page-1", "text": snapshot.content}])
    brief = measurement.inputs.brief.model_copy(update={"audience": analysis.audience.text, "goal": analysis.purpose.text})
    proposed, planner_call = foundry.propose_pairs(brief, snapshot)
    inputs = MeasurementInputs(brief=brief, snapshot=snapshot, query_plan=proposed, profiles=profiles,
                               policy_hash=digest({"mock-only": True}), query_generation=planner_call)
    evaluators = tuple(create_evaluator(profile, token_provider=lambda: "dummy-token", transport=transport)
                       for profile in profiles)
    packets, results = [], []
    for query in proposed.queries:
        sources = webiq.search(query.as_query(grounding=True), brief.locale)
        packets.append(RetrievalResult(query_id=query.query_id, grounding_query=query.grounding_query,
                                       provenance="live", status="completed", sources=sources))
        for evaluator in evaluators:
            try:
                result = evaluator.evaluate(query.as_query(), brief.locale, sources)
            except ProviderError as error:
                result = EvaluationResult(query_id=query.query_id, profile_id=evaluator.profile.profile_id,
                                          provenance="live", status="error", sources=sources, error=str(error))
            results.append(result)
    saved = MeasurementResults(inputs=inputs, retrievals=tuple(packets), results=tuple(results))
    recommendations = RecommendationService(foundry).recommend(saved)
    assert calls == {"browse": 1, "search": 5, "PageAnalysis": 1, "QueryPlan": 1,
                     "chatgpt-style": 5, "claude-backed": 5, "copilot-style": 5, "RecommendationProposal": 1}
    assert analysis_call["response_id"] and recommendations.model_call.response_id
    before_export = calls.copy()
    with zipfile.ZipFile(io.BytesIO(render_measurement_bundle(saved, recommendations))) as archive:
        restored = MeasurementResults.model_validate_json(archive.read("measurement.json"))
        scores = measurement_scores(restored)
        assert scores == json.loads(archive.read("scores.json"))
        assert scores["overall"]["score"] == (36 if failed_profile else 40)
        assert scores["overall"]["denominator"] == (14 if failed_profile else 15)
        assert scores["retrieval"]["coverage"] == 1
        assert scores["common_completed_query_ids"] == (["q-2", "q-3", "q-4", "q-5"] if failed_profile else ["q-1", "q-2", "q-3", "q-4", "q-5"])
        report = RecommendationReport.model_validate_json(archive.read("recommendations.json"))
        report.validate_for(restored)
        assert report.tasks[0].publish_permission is False
        assert "dummy-token" not in str(restored.model_dump())
        assert "dummy-sensitive" not in str(restored.model_dump())
    assert calls == before_export