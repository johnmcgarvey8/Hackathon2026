import pytest
from pydantic import HttpUrl, ValidationError

from geo_agent.contracts import (
    Brief, EvaluationResult, EvidenceQuote, MeasurementInputs, MeasurementResults,
    PageSnapshot, Provenance, QueryPair, QueryPlan, RetrievalResult, SimulationProfile, Source,
)
from geo_agent.evaluation import answer_mentions_target, match_citations, measurement_scores, score_results
from geo_agent.fixtures import evaluate_synthetic, synthetic_inputs
from geo_agent.workflow import Conflict, Coordinator, RunStore


TARGET = HttpUrl("https://example.org/product")


def test_brand_matching_separates_ambiguous_text_and_owned_domains():
    from geo_agent.evidence_assessment import BrandAlias, BrandDefinition, brand_matches, owns_host

    definition = BrandDefinition(name="Microsoft Clarity", aliases=(BrandAlias(text="Clarity", ambiguous=True),),
                                 domains=("clarity.microsoft.com",))
    assert brand_matches({"excerpt": "Improve clarity today"}, definition)["status"] == "ambiguous"
    assert brand_matches({"title": "MICROSOFT CLARITY", "excerpt": "Clarity tools"}, definition)["status"] == "matched"
    assert brand_matches({"excerpt": "Clarity tools"}, definition, brand_owned=True)["status"] == "matched"
    assert brand_matches({"excerpt": "clarityful"}, definition)["status"] == "absent"
    assert owns_host("https://docs.clarity.microsoft.com/", definition)
    assert not owns_host("https://clarity.microsoft.com.evil.org/", definition)
    assert not owns_host("https://microsoft.com/", definition)
    assert brand_matches({"excerpt": "Clarity"}, None)["status"] == "unconfigured"


def test_evidence_assessment_keeps_grounding_answers_and_citations_independent():
    from geo_agent.contracts import digest
    from geo_agent.evidence_assessment import BrandDefinition, BrandDefinitionRecord, build_evidence_assessment

    measurement = measurement_fixture()
    sources = (measurement.retrievals[0].sources[0].model_copy(update={"excerpt": "Microsoft Clarity records sessions."}),
               measurement.retrievals[0].sources[1])
    retrievals = (measurement.retrievals[0].model_copy(update={"sources": sources}), *measurement.retrievals[1:])
    results = tuple(item.model_copy(update={"sources": sources, "citation_ids": (sources[1].evidence_id, "invented"),
                                           "answer": "Microsoft Clarity is a product."})
                    if item.query_id == "q-1" else item for item in measurement.results)
    measurement = measurement.model_copy(update={"retrievals": retrievals, "results": results})
    before = digest(measurement.model_dump(mode="json"))
    scores = measurement_scores(measurement)
    record = BrandDefinitionRecord(run_id="test", definition_version=1, definition=BrandDefinition(name="Microsoft Clarity"))
    report = build_evidence_assessment(measurement, record, run_id="test", run_revision=1)
    assert report.grounding["brand_presence"]["numerator"] == 1
    assert report.grounding["brand_presence"]["denominator"] == 5
    assert report.answer["brand_presence"]["numerator"] == 3
    assert report.answer["brand_source_conversion"]["numerator"] == 0
    assert report.answer["brand_source_conversion"]["denominator"] == 3
    assert report.answers[0]["sources"][0]["cited"] is False
    assert report.answers[0]["sources"][1]["cited"] is True
    assert report.answers[0]["unsupported_citation_ids"] == ["invented"]
    assert report.answers[0]["citation_status"] == "some"
    assert report.measurement_hash == before == digest(measurement.model_dump(mode="json"))
    assert measurement_scores(measurement) == scores
    assert report == build_evidence_assessment(measurement, record, run_id="test", run_revision=1)


def test_evidence_assessment_unknown_and_empty_are_not_all_sources_cited():
    from geo_agent.evidence_assessment import BrandDefinition, BrandDefinitionRecord, build_evidence_assessment

    measurement = measurement_fixture(failed_retrievals=("q-1",), missing_retrievals=("q-2",),
                                      source_urls={"q-3": ()})
    record = BrandDefinitionRecord(run_id="test", definition_version=1, definition=BrandDefinition(name="Clarity"))
    report = build_evidence_assessment(measurement, record, run_id="test", run_revision=1)
    assert report.grounding["brand_presence"]["denominator"] == 3
    assert report.queries[0]["brand_status"] == "unknown"
    assert report.answers[0]["citation_status"] == "unknown"
    assert report.answers[6]["citation_status"] == "not-assessable"
    assert report.answers[6]["source_citation_rate"]["rate"] is None


@pytest.mark.parametrize("text,expected", [
    ("CAFÉ", "matched"), ("Cafe\u0301", "matched"), ("Cafeteria", "absent"),
])
def test_brand_unicode_highlights_preserve_original_text(text, expected):
    from geo_agent.evidence_assessment import BrandDefinition, brand_matches

    finding = brand_matches({"excerpt": text}, BrandDefinition(name="Caf\u00e9"))
    assert finding["status"] == expected
    if expected == "matched":
        match = finding["matches"][0]
        assert match["quote"] == text[match["start"]:match["end"]] == text


@pytest.mark.parametrize("domain", ["https://clarity.microsoft.com", "*.microsoft.com", "clarity.microsoft.com/path",
                                   "clarity.microsoft.com:443", "a..org", "-a.org", "a-.org", "localhost"])
def test_brand_domains_reject_ambiguous_or_unsafe_configuration(domain):
    from geo_agent.evidence_assessment import BrandDefinition

    with pytest.raises(ValueError):
        BrandDefinition(name="Microsoft Clarity", domains=(domain,))


@pytest.mark.parametrize("profile_count", [1, 2, 3])
def test_assessment_packet_ids_wording_and_profile_denominators(profile_count):
    from geo_agent.evidence_assessment import BrandDefinition, BrandDefinitionRecord, build_evidence_assessment

    measurement = measurement_fixture()
    profiles = measurement.inputs.profiles[:profile_count]
    shared = "This is a retained passage with at least six matching words"
    packets = tuple(packet.model_copy(update={"sources": tuple(source.model_copy(update={
        "evidence_id": f"source-{index}", "excerpt": shared, "title": "Microsoft Clarity" if index == 0 else "Third party",
    }) for index, source in enumerate(packet.sources))}) for packet in measurement.retrievals)
    answers = tuple(answer.model_copy(update={
        "sources": next(packet.sources for packet in packets if packet.query_id == answer.query_id),
        "answer": shared, "citation_ids": ("source-1", "source-1", "q-2-source-1"),
    }) for answer in measurement.results if answer.profile_id in {profile.profile_id for profile in profiles})
    measurement = MeasurementResults(inputs=measurement.inputs.model_copy(update={"profiles": profiles}),
                                     retrievals=packets, results=answers)
    record = BrandDefinitionRecord(run_id="test", definition_version=1, definition=BrandDefinition(name="Microsoft Clarity"))
    report = build_evidence_assessment(measurement, record, run_id="test", run_revision=1)
    assert report.grounding["source_count"] == 10
    assert report.answer["brand_presence"]["numerator"] == 0
    assert report.answer["source_citation_rate"]["numerator"] == 5 * profile_count
    assert report.answer["source_citation_rate"]["denominator"] == 10 * profile_count
    assert report.answers[0]["valid_citation_ids"] == ["source-1"]
    assert report.answers[0]["unsupported_citation_ids"] == ["q-2-source-1"]
    assert all(source["shared_wording"][0]["non_unique"] for source in report.answers[0]["sources"])
    assert report.answers[0]["sources"][0]["cited"] is False
    assert all(len(answer["sources"]) == 2 for answer in report.answers)


def test_answer_ambiguity_is_not_resolved_by_supplied_brand_packet():
    from geo_agent.evidence_assessment import BrandAlias, BrandDefinition, BrandDefinitionRecord, build_evidence_assessment

    measurement = measurement_fixture()
    results = tuple(answer.model_copy(update={"answer": "Improve clarity in the final answer."}) for answer in measurement.results)
    measurement = measurement.model_copy(update={"results": results})
    record = BrandDefinitionRecord(run_id="test", definition_version=1, definition=BrandDefinition(
        name="Microsoft Clarity", aliases=(BrandAlias(text="Clarity", ambiguous=True),), domains=("clarity.microsoft.com",)))
    report = build_evidence_assessment(measurement, record, run_id="test", run_revision=1)
    assert report.answer["ambiguous_answers"] == 15
    assert report.answer["brand_presence"]["numerator"] == 0
    unconfigured = build_evidence_assessment(measurement, run_id="test", run_revision=1)
    assert unconfigured.answer["brand_presence"]["rate"] is None
    assert unconfigured.answer["source_citation_rate"] == report.answer["source_citation_rate"]


def result(query="q-1", profile="baseline", cited=True, url=TARGET):
    return EvaluationResult(
        query_id=query, profile_id=profile, provenance="synthetic", status="completed",
        answer="Synthetic answer", citation_ids=("source-1",) if cited else (),
        sources=(Source(evidence_id="source-1", url=url, excerpt="Synthetic passage", provenance="synthetic"),),
    )


def test_prototype_regression_scores():
    results = tuple(
        result(query=f"q-{index}", profile=profile, cited=index < cited_count)
        for profile, cited_count in (("profile-a", 2), ("profile-b", 2), ("profile-c", 1))
        for index in range(4)
    )
    overall = score_results(results, TARGET, 12)
    assert (overall.score, overall.numerator, overall.denominator) == (42, 5, 12)
    assert [
        score_results(tuple(item for item in results if item.profile_id == profile), TARGET, 4).score
        for profile in ("profile-a", "profile-b", "profile-c")
    ] == [50, 50, 25]


@pytest.mark.parametrize("url,kind", [
    ("https://EXAMPLE.org:443/product#details", "exact-page"),
    ("https://example.org/product?variant=2", "same-domain-other-page"),
    ("https://example.org/product?utm_source=mail", "same-domain-other-page"),
    ("https://example.org/other", "same-domain-other-page"),
    ("https://example.org/product/", "same-domain-other-page"),
    ("https://other.example/product", "other-page"),
])
def test_conservative_url_matching(url, kind):
    assert match_citations(result(url=url), TARGET)[0].kind == kind


def test_fabricated_citation_is_not_evidence():
    fabricated = result().model_copy(update={"citation_ids": ("invented",)})
    assert match_citations(fabricated, TARGET)[0].kind == "unsupported"
    assert score_results((fabricated,), TARGET, 1).score == 0


def test_no_citation_is_zero_but_no_answer_is_missing():
    failed = EvaluationResult(query_id="q-2", profile_id="baseline", provenance="synthetic", status="error", error="timeout")
    score = score_results((result(cited=False), failed), TARGET, 3)
    assert (score.score, score.denominator, score.errors, score.missing) == (0, 1, 1, 1)
    assert score_results((failed,), TARGET, 1).score is None
    assert score_results((), TARGET, 0).score is None


def test_duplicate_pairs_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        score_results((result(), result()), TARGET, 2)


def test_empty_completed_answer_is_rejected():
    with pytest.raises(ValidationError):
        EvaluationResult(query_id="q-1", profile_id="baseline", provenance="synthetic", status="completed")


def test_fixture_finish_requires_complete_results_and_approval(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite3")
    coordinator = Coordinator(store)
    run = store.create("alice", synthetic_inputs())
    results = evaluate_synthetic(run)
    with pytest.raises(Conflict):
        coordinator.finish(run.run_id, "alice", 1, results)
    coordinator.approve(run.run_id, "alice", 1, run.inputs.approval_hash)
    coordinator.start(run.run_id, "alice", 1, "start")
    with pytest.raises(Conflict, match="exactly one"):
        coordinator.finish(run.run_id, "alice", 1, results[:-1])
    finished = coordinator.finish(run.run_id, "alice", 1, results)
    assert finished.state == "recommendations-ready"
    assert coordinator.start(run.run_id, "alice", 1, "start") == finished
    assert score_results(finished.results, run.inputs.snapshot.url, 5).score == 40


def test_synthetic_evaluator_rejects_live_run(tmp_path):
    inputs = synthetic_inputs()
    live = inputs.model_copy(update={"snapshot": inputs.snapshot.model_copy(update={"provenance": Provenance.LIVE})})
    run = RunStore(tmp_path / "runs.sqlite3").create("alice", live)
    with pytest.raises(ValueError, match="cannot process"):
        evaluate_synthetic(run)


def measurement_fixture(*, cited_queries=("q-1", "q-2"), failed_pairs=(), missing_pairs=(),
                        failed_retrievals=(), missing_retrievals=(), source_urls=None):
    timestamp = "2026-09-15T00:00:00Z"
    snapshot = PageSnapshot(url=TARGET, title="Synthetic product", content="Synthetic product evidence.",
                            provenance="synthetic", captured_at=timestamp)
    queries = tuple(
        QueryPair(query_id=f"q-{index}", priority=index, rationale="Supported by page evidence",
                  intent=f"Synthetic intent {index}", branded=index <= 2,
                  chat_query=f"Synthetic chat question {index}", grounding_query=f"Synthetic search {index}",
                  evidence=(EvidenceQuote(evidence_id="page-1", quote="Synthetic product evidence."),))
        for index in range(1, 6)
    )
    profiles = tuple(
        SimulationProfile(profile_id=profile_id, provider=provider, deployment=f"synthetic-{profile_id}",
                          endpoint="https://models.example.org/", prompt_version="synthetic/v1",
                          instructions="Use only the supplied synthetic sources.")
        for profile_id, provider in (("chatgpt-style", "openai-responses"),
                                     ("claude-backed", "anthropic-messages"),
                                     ("copilot-style", "openai-responses"))
    )
    inputs = MeasurementInputs(
        brief=Brief(url=TARGET, audience="Synthetic audience", goal="Measure citations", locale="en-GB"),
        snapshot=snapshot, query_plan=QueryPlan(queries=queries), profiles=profiles, policy_hash="0" * 64,
    )
    retrievals = []
    results = []
    for query in queries:
        retrieval_failed = query.query_id in failed_retrievals
        retrieval_missing = query.query_id in missing_retrievals
        urls = (source_urls or {}).get(query.query_id, (TARGET, HttpUrl("https://example.org/other")))
        sources = () if retrieval_failed or retrieval_missing else tuple(
            Source(evidence_id=f"{query.query_id}-source-{position}", url=url,
                   excerpt=f"Synthetic passage {position}", provenance="synthetic", returned_position=position)
            for position, url in enumerate(urls, 1)
        )
        if not retrieval_missing:
            retrievals.append(RetrievalResult(
                query_id=query.query_id, grounding_query=query.grounding_query, provenance="synthetic",
                status="error" if retrieval_failed else "completed", sources=sources,
                error="Synthetic search failure" if retrieval_failed else None, retrieved_at=timestamp,
            ))
        for profile in profiles:
            pair = (query.query_id, profile.profile_id)
            if pair in missing_pairs:
                continue
            failed = retrieval_failed or retrieval_missing or pair in failed_pairs
            results.append(EvaluationResult(
                query_id=query.query_id, profile_id=profile.profile_id, provenance="synthetic", sources=sources,
                status="error" if failed else "completed", answer="" if failed else "Synthetic model answer.",
                citation_ids=(sources[0].evidence_id,) if not failed and sources and query.query_id in cited_queries else (),
                error="Synthetic model failure" if failed else None,
            ))
    return MeasurementResults(inputs=inputs, retrievals=tuple(retrievals), results=tuple(results))


@pytest.mark.parametrize("cited_queries,numerator,score", [(("q-1", "q-2"), 6, 40), ((), 0, 0)])
def test_measurement_scores_complete(cited_queries, numerator, score):
    measurement = measurement_fixture(cited_queries=cited_queries)
    scores = measurement_scores(measurement)
    assert scores["method_version"] == "exact-page-citation/v1"
    assert scores["provisional"] is False
    assert scores["overall"] == {
        "score": score, "numerator": numerator, "denominator": 15, "intended": 15,
        "errors": 0, "missing": 0, "coverage": 1, "provisional": False,
    }
    assert [item["score"] for item in scores["by_profile"].values()] == [score] * 3
    assert [item["denominator"] for item in scores["by_query"].values()] == [3] * 5
    assert scores["by_query_type"]["branded"]["denominator"] == 6
    assert scores["by_query_type"]["branded"]["numerator"] == numerator
    assert scores["by_query_type"]["unbranded"]["denominator"] == 9
    assert scores["by_query_type"]["unbranded"]["score"] == 0
    assert scores["common_completed_query_ids"] == [f"q-{index}" for index in range(1, 6)]
    assert scores["comparable_by_profile"] == scores["by_profile"]
    assert len(scores["by_answer"]) == 15
    assert sum(item["exact_page_cited"] for item in scores["by_answer"]) == numerator
    assert measurement_scores(measurement) == scores


def test_measurement_all_model_failures_preserve_retrieval_scores():
    complete = measurement_fixture()
    measurement = measurement_fixture(failed_pairs=tuple(
        (result.query_id, result.profile_id) for result in complete.results
    ))
    scores = measurement_scores(measurement)
    assert scores["overall"] == {
        "score": None, "numerator": 0, "denominator": 0, "intended": 15,
        "errors": 15, "missing": 0, "coverage": 0, "provisional": True,
    }
    assert scores["provisional"] is True
    assert scores["common_completed_query_ids"] == []
    assert all(item["score"] is None for item in scores["comparable_by_profile"].values())
    assert scores["retrieval"] == measurement_scores(complete)["retrieval"]
    assert scores["retrieval"]["score"] == 100
    assert scores["retrieval"]["denominator"] == scores["retrieval"]["intended"] == 5
    assert scores["retrieval"]["coverage"] == 1
    assert all(item["status"] == "error" and item["exact_page_cited"] is None for item in scores["by_answer"])


def test_measurement_compares_only_common_completed_queries():
    measurement = measurement_fixture(
        failed_pairs=(("q-3", "chatgpt-style"), ("q-1", "claude-backed")),
        missing_pairs=(("q-4", "copilot-style"), ("q-5", "claude-backed")),
    )
    scores = measurement_scores(measurement)
    assert scores["overall"]["denominator"] == 11
    assert scores["overall"]["intended"] == 15
    assert scores["overall"]["errors"] == scores["overall"]["missing"] == 2
    assert scores["overall"]["coverage"] == 11 / 15
    assert scores["provisional"] is True
    assert [item["denominator"] for item in scores["by_profile"].values()] == [4, 3, 4]
    assert [item["score"] for item in scores["by_profile"].values()] == [50, 33, 50]
    assert scores["common_completed_query_ids"] == ["q-2"]
    assert list(scores["comparable_by_profile"]) == [profile.profile_id for profile in measurement.inputs.profiles]
    assert list(scores["comparable_by_profile"].values()) == [{
        "score": 100, "numerator": 1, "denominator": 1, "intended": 1,
        "errors": 0, "missing": 0, "coverage": 1, "provisional": False,
    }] * 3
    assert scores["by_query"]["q-3"]["errors"] == 1
    assert scores["by_query"]["q-4"]["missing"] == 1
    assert scores["by_query_type"]["branded"]["denominator"] == 5
    assert scores["by_query_type"]["branded"]["intended"] == 6
    assert scores["by_query_type"]["unbranded"]["denominator"] == 6
    assert scores["by_query_type"]["unbranded"]["intended"] == 9
    missing = next(item for item in scores["by_answer"]
                   if (item["query_id"], item["profile_id"]) == ("q-4", "copilot-style"))
    assert missing == {
        "query_id": "q-4", "profile_id": "copilot-style", "status": "missing", "exact_page_cited": None,
        "same_domain_citation_count": 0, "unsupported_citation_ids": [], "raw_url_mentioned": False,
    }


@pytest.mark.parametrize("profile_count", [1, 2])
def test_measurement_uses_all_and_only_configured_profiles(profile_count):
    measurement = measurement_fixture()
    profiles = measurement.inputs.profiles[:profile_count]
    measurement = measurement.model_copy(update={
        "inputs": measurement.inputs.model_copy(update={"profiles": profiles}),
        "results": tuple(result for result in measurement.results
                         if result.profile_id in {profile.profile_id for profile in profiles}),
    })
    scores = measurement_scores(measurement)
    assert scores["overall"]["intended"] == scores["overall"]["denominator"] == 5 * profile_count
    assert scores["overall"]["score"] == 40
    assert len(scores["by_profile"]) == len(scores["comparable_by_profile"]) == profile_count
    assert all(item["intended"] == profile_count for item in scores["by_query"].values())
    assert scores["comparable_by_profile"] == scores["by_profile"]


@pytest.mark.parametrize("branded,empty_group", [(True, "unbranded"), (False, "branded")])
def test_measurement_query_type_without_queries_is_na(branded, empty_group):
    measurement = measurement_fixture()
    query_plan = measurement.inputs.query_plan.model_copy(update={
        "queries": tuple(query.model_copy(update={"branded": branded}) for query in measurement.inputs.query_plan.queries),
    })
    measurement = measurement.model_copy(update={"inputs": measurement.inputs.model_copy(update={"query_plan": query_plan})})
    empty = measurement_scores(measurement)["by_query_type"][empty_group]
    assert empty["score"] is None
    assert empty["denominator"] == empty["intended"] == 0


def test_measurement_retrieval_scores_successful_searches_not_answer_completions():
    measurement = measurement_fixture(
        failed_retrievals=("q-4",), missing_retrievals=("q-5",),
        source_urls={"q-1": (TARGET, HttpUrl("https://EXAMPLE.org:443/product#details")),
                     "q-2": (), "q-3": (HttpUrl("https://example.org/other"), TARGET)},
    )
    retrieval = measurement_scores(measurement)["retrieval"]
    assert {key: retrieval[key] for key in ("score", "numerator", "denominator", "intended",
                                          "errors", "missing", "coverage", "provisional")} == {
        "score": 67, "numerator": 2, "denominator": 3, "intended": 5,
        "errors": 1, "missing": 1, "coverage": 3 / 5, "provisional": True,
    }
    assert retrieval["by_query"]["q-1"] == {
        "status": "completed", "target_returned": True, "returned_position": 1, "error": None,
    }
    assert retrieval["by_query"]["q-3"]["returned_position"] == 2
    assert retrieval["by_query"]["q-2"] == {
        "status": "completed", "target_returned": False, "returned_position": None, "error": None,
    }
    assert retrieval["by_query"]["q-4"] == {
        "status": "error", "target_returned": None, "returned_position": None, "error": "Synthetic search failure",
    }
    assert retrieval["by_query"]["q-5"] == {
        "status": "missing", "target_returned": None, "returned_position": None, "error": None,
    }
    assert retrieval["failures"] == [{"query_id": "q-4", "error": "Synthetic search failure"}]


@pytest.mark.parametrize("urls", [(), (HttpUrl("https://example.org/product?variant=2"),),
                                  (HttpUrl("https://example.org/product/"),)])
def test_measurement_retrieval_absence_is_zero_without_a_fabricated_rank(urls):
    measurement = measurement_fixture(source_urls={f"q-{index}": urls for index in range(1, 6)})
    retrieval = measurement_scores(measurement)["retrieval"]
    assert retrieval["score"] == retrieval["numerator"] == 0
    assert retrieval["denominator"] == 5
    assert retrieval["coverage"] == 1
    assert all(item["target_returned"] is False and item["returned_position"] is None
               for item in retrieval["by_query"].values())


@pytest.mark.parametrize("failed_count", [0, 5])
def test_measurement_no_completed_retrievals_is_na(failed_count):
    measurement = measurement_fixture()
    if failed_count:
        measurement = measurement_fixture(failed_retrievals=tuple(f"q-{index}" for index in range(1, 6)))
    else:
        measurement = measurement.model_copy(update={"results": (), "retrievals": ()})
    scores = measurement_scores(measurement)
    assert scores["retrieval"]["score"] is scores["overall"]["score"] is None
    assert scores["retrieval"]["denominator"] == scores["retrieval"]["coverage"] == 0
    assert scores["retrieval"]["errors"] == failed_count
    assert scores["retrieval"]["missing"] == 5 - failed_count
    assert scores["common_completed_query_ids"] == []
    assert all(item["target_returned"] is None for item in scores["retrieval"]["by_query"].values())
    assert all(item["score"] is None for item in scores["comparable_by_profile"].values())


@pytest.mark.parametrize("citation_ids,answer,exact,same_domain,unsupported,mentioned", [
    ((), "See https://example.org/product.", False, 0, [], True),
    (("invented", "invented"), "See <https://example.org/product>.", False, 0, ["invented"], True),
    (("q-1-source-2", "q-1-source-2"), "See the related page.", False, 1, [], False),
    (("q-1-source-1",), "Supported answer without a literal URL.", True, 0, [], False),
    (("q-1-source-1", "q-1-source-1", "q-1-source-2", "invented"), "Supported answer.", True, 1, ["invented"], False),
])
def test_measurement_answer_details_separate_citations_and_raw_mentions(
        citation_ids, answer, exact, same_domain, unsupported, mentioned):
    measurement = measurement_fixture(cited_queries=())
    changed = measurement.results[0].model_copy(update={"answer": answer, "citation_ids": citation_ids})
    measurement = measurement.model_copy(update={"results": (changed, *measurement.results[1:])})
    before = measurement.model_dump(mode="json")
    scores = measurement_scores(measurement)
    assert scores["by_answer"][0] == {
        "query_id": "q-1", "profile_id": "chatgpt-style", "status": "completed", "exact_page_cited": exact,
        "same_domain_citation_count": same_domain, "unsupported_citation_ids": unsupported, "raw_url_mentioned": mentioned,
    }
    assert scores["overall"]["numerator"] == int(exact)
    assert scores["overall"]["score"] == (7 if exact else 0)
    assert measurement.model_dump(mode="json") == before


@pytest.mark.parametrize("answer", [
    "https://example.org/product",
    "[product](https://example.org/product)",
    '[product](https://example.org/product "Product page")',
    "See <https://example.org/product>.",
    "See (https://example.org/product).",
    "See [https://example.org/product],",
    "See https://example.org/product!",
    "See https://example.org/product; next point.",
    "See https://example.org/product#details.",
    "See HTTPS://EXAMPLE.ORG:443/product#details.",
    "First https://other.example.org/path then https://example.org/product.",
])
def test_literal_target_url_mentions(answer):
    assert answer_mentions_target(answer, TARGET) is True


@pytest.mark.parametrize("answer", [
    "example.org/product", "Product page", "",
    "https://example.org/product-other",
    "https://example.org/product.ext",
    "https://example.org/product/",
    "https://example.org/Product",
    "https://example.org/%70roduct",
    "https://example.org/product?var",
    "https://example.org/product?variant=2#details",
    "https://example.org/product,other",
    "https://example.org/product;other",
    "https://example.org/product(extra)",
    "https://example.org/product]/other",
    "https://example.org/product%2Fother",
    "https://example.org:444/product",
    "http://example.org/product",
    "https://example.org.evil.test/product",
    "https://other.example.org/?next=https://example.org/product",
    "https://other.example.org/path/https://example.org/product",
    "https://other.example.org/#https://example.org/product",
    "https://user:password@example.org/product",
    "https://user@example.org/product",
    "https://@example.org/product",
    "https://example.org@other.example.org/product",
    "https://[invalid]/path https://example.org:bad/product",
    "prefixhttps://example.org/product",
])
def test_literal_target_url_rejects_substrings_and_credentials(answer):
    assert answer_mentions_target(answer, TARGET) is False


@pytest.mark.parametrize("answer,target,expected", [
    ("https://example.org/product#new", "https://example.org/product#old", True),
    ("https://example.org/product?variant=2", "https://example.org/product?variant=2", True),
    ("https://example.org/product?variant=2", "https://example.org/product?variant=3", False),
    ("https://example.org/product", "https://example.org/product/", False),
    ("https://user:password@example.org/product", "https://user:password@example.org/product", False),
])
def test_literal_target_url_uses_existing_comparison_rules(answer, target, expected):
    assert answer_mentions_target(answer, HttpUrl(target)) is expected


@pytest.mark.parametrize("tamper,reason", [
    ("duplicate-result", "Duplicate query/profile outcome"),
    ("duplicate-retrieval", "Duplicate retrieval"),
    ("duplicate-source", "source IDs must be unique"),
    ("duplicate-position", "positions must be unique and ordered"),
    ("result-provenance", "Result provenance must match"),
    ("source-provenance", "source provenance"),
    ("retrieval-provenance", "Retrieval provenance must match"),
    ("unapproved-query", "approved query/profile"),
    ("unapproved-profile", "approved query/profile"),
    ("unapproved-grounding", "approved grounding query"),
    ("unsupported-page-quote", "unsupported page reference or quote"),
    ("injected-target-source", "exactly match the saved retrieval packet"),
    ("reordered-answer-sources", "exactly match the saved retrieval packet"),
    ("missing-retrieval", "Completed answer requires a completed retrieval"),
])
def test_measurement_revalidates_serialized_model_copies(tamper, reason):
    measurement = measurement_fixture(source_urls={"q-1": (HttpUrl("https://other.example.org/page"),
                                                          HttpUrl("https://example.org/other"))})
    original = measurement.results[0]
    packet = measurement.retrievals[0]
    if tamper == "duplicate-result":
        measurement = measurement.model_copy(update={"results": (original, *measurement.results[1:-1], original)})
    elif tamper == "duplicate-retrieval":
        measurement = measurement.model_copy(update={"retrievals": (packet, *measurement.retrievals[1:-1], packet)})
    elif tamper in ("duplicate-source", "duplicate-position", "retrieval-provenance", "source-provenance", "unapproved-grounding"):
        updates = {}
        if tamper == "duplicate-source":
            updates = {"sources": (packet.sources[0], packet.sources[0])}
        elif tamper == "duplicate-position":
            updates = {"sources": (packet.sources[0], packet.sources[1].model_copy(update={"returned_position": 1}))}
        elif tamper == "retrieval-provenance":
            updates = {"provenance": Provenance.LIVE, "sources": tuple(
                source.model_copy(update={"provenance": Provenance.LIVE}) for source in packet.sources
            )}
        elif tamper == "source-provenance":
            updates = {"sources": (packet.sources[0].model_copy(update={"provenance": Provenance.LIVE}), packet.sources[1])}
        else:
            updates = {"grounding_query": "Unapproved search"}
        measurement = measurement.model_copy(update={"retrievals": (packet.model_copy(update=updates), *measurement.retrievals[1:])})
    elif tamper == "unsupported-page-quote":
        query_plan = measurement.inputs.query_plan
        changed = query_plan.queries[0].model_copy(update={
            "evidence": (EvidenceQuote(evidence_id="page-1", quote="Invented quote"),),
        })
        inputs = measurement.inputs.model_copy(update={
            "query_plan": query_plan.model_copy(update={"queries": (changed, *query_plan.queries[1:])}),
        })
        measurement = measurement.model_copy(update={"inputs": inputs})
    elif tamper == "missing-retrieval":
        measurement = measurement.model_copy(update={"retrievals": measurement.retrievals[1:]})
    else:
        if tamper == "result-provenance":
            updates = {"provenance": Provenance.LIVE, "sources": tuple(
                source.model_copy(update={"provenance": Provenance.LIVE}) for source in original.sources
            )}
        elif tamper == "unapproved-query":
            updates = {"query_id": "q-6"}
        elif tamper == "unapproved-profile":
            updates = {"profile_id": "unapproved-model"}
        elif tamper == "injected-target-source":
            updates = {"sources": (original.sources[0].model_copy(update={"url": TARGET}), original.sources[1])}
        else:
            updates = {"sources": tuple(reversed(original.sources))}
        measurement = measurement.model_copy(update={"results": (original.model_copy(update=updates), *measurement.results[1:])})
    with pytest.raises(ValidationError, match=reason):
        measurement_scores(measurement)