import pytest
from datetime import datetime, timezone

from geo_agent.contracts import Brief, EvaluationResult, MeasurementInputs, MeasurementResults, PageSnapshot, Profile, Query, QueryPlan, RetrievalResult, RunInputs, Source, State
from geo_agent.workflow import Conflict, Coordinator, NotFound, RunStore


@pytest.fixture
def inputs():
    return RunInputs(
        brief=Brief(url="https://example.org/product", audience="Buyers", goal="Compare", locale="en-GB"),
        snapshot=PageSnapshot(url="https://example.org/product", title="Fixture", content="Synthetic page", provenance="synthetic"),
        queries=tuple(Query(query_id=f"q-{index}", text=f"Buyer question {index}?", intent="Compare") for index in range(1, 6)),
        profiles=(Profile(profile_id="fixture", deployment="synthetic-v1", prompt_version="v1"),),
    )


@pytest.fixture
def workflow(tmp_path, inputs):
    store = RunStore(tmp_path / "runs.sqlite3")
    return Coordinator(store), store.create("alice", inputs)


def test_start_requires_approval(workflow):
    coordinator, run = workflow
    with pytest.raises(Conflict, match="human query approval"):
        coordinator.start(run.run_id, "alice", 1, "start-1")
    assert coordinator.store.get(run.run_id, "alice").state == State.AWAITING_APPROVAL


def test_approval_hash_and_revision_are_checked(workflow):
    coordinator, run = workflow
    with pytest.raises(Conflict, match="current inputs"):
        coordinator.approve(run.run_id, "alice", 1, "wrong")
    with pytest.raises(Conflict, match="Stale revision"):
        coordinator.approve(run.run_id, "alice", 2, run.inputs.approval_hash)


def test_revising_invalidates_approval(workflow):
    coordinator, run = workflow
    coordinator.approve(run.run_id, "alice", 1, run.inputs.approval_hash)
    changed = run.inputs.model_copy(update={"brief": run.inputs.brief.model_copy(update={"locale": "en-US"})})
    revised = coordinator.revise(run.run_id, "alice", 1, changed)
    assert revised.revision == 2
    assert revised.approval is None
    assert revised.inputs.approval_hash != run.inputs.approval_hash
    with pytest.raises(Conflict):
        coordinator.start(run.run_id, "alice", 2, "start-1")


def test_duplicate_start_and_restart_are_safe(workflow):
    coordinator, run = workflow
    coordinator.approve(run.run_id, "alice", 1, run.inputs.approval_hash)
    first = coordinator.start(run.run_id, "alice", 1, "start-1")
    restarted = Coordinator(RunStore(coordinator.store.path))
    assert restarted.start(run.run_id, "alice", 1, "start-1") == first
    assert first.events.count("evaluating") == 1
    with pytest.raises(Conflict):
        restarted.start(run.run_id, "alice", 1, "start-2")


def test_cancellation_blocks_start(workflow):
    coordinator, run = workflow
    coordinator.approve(run.run_id, "alice", 1, run.inputs.approval_hash)
    coordinator.cancel(run.run_id, "alice", 1)
    with pytest.raises(Conflict):
        coordinator.start(run.run_id, "alice", 1, "start-1")


def test_cross_owner_access_is_hidden(workflow):
    coordinator, run = workflow
    with pytest.raises(NotFound):
        coordinator.store.get(run.run_id, "bob")
    with pytest.raises(NotFound):
        coordinator.approve(run.run_id, "bob", 1, run.inputs.approval_hash)


def test_provenance_cannot_change(workflow):
    coordinator, run = workflow
    changed = run.inputs.model_copy(update={"snapshot": run.inputs.snapshot.model_copy(update={"provenance": "live"})})
    with pytest.raises(Conflict, match="provenance"):
        coordinator.revise(run.run_id, "alice", 1, changed)


def test_legacy_approval_hash_is_unchanged(inputs):
    snapshot = inputs.snapshot.model_copy(update={"captured_at": datetime(2026, 9, 14, tzinfo=timezone.utc)})
    frozen = inputs.model_copy(update={"snapshot": snapshot})
    assert frozen.approval_hash == "4a55ccfe8a09c4a3fb76eb32835ea93d178d35f99c6197f8132c16d0a8df89e3"
    assert "query_plan" not in frozen.model_dump()


@pytest.fixture
def measurement_inputs(inputs):
    plan = QueryPlan(queries=[{
        "query_id": f"q-{index}", "priority": index, "rationale": "Page describes the product",
        "intent": "Compare", "chat_query": f"Buyer question {index}?", "grounding_query": f"Product search {index}",
        "evidence": [{"evidence_id": "page-1", "quote": "Synthetic page"}],
    } for index in range(1, 6)])
    return MeasurementInputs(brief=inputs.brief, snapshot=inputs.snapshot, query_plan=plan,
        policy_hash="a" * 64, profiles=[{
            "profile_id": "chatgpt-style", "provider": "openai-responses", "deployment": "fixture-gpt",
            "endpoint": "https://fixture.openai.azure.com/openai/v1/", "prompt_version": "fixture-v1",
            "instructions": "Answer using the supplied evidence only.",
        }])


def test_paired_queries_keep_chat_and_search_distinct(measurement_inputs):
    query = measurement_inputs.query_plan.queries[0]
    assert query.as_query().text == "Buyer question 1?"
    assert query.as_query(grounding=True).text == "Product search 1"
    assert MeasurementInputs.model_validate_json(measurement_inputs.model_dump_json()) == measurement_inputs


@pytest.mark.parametrize("field", ["chat_query", "grounding_query", "query_id", "priority"])
def test_paired_plan_rejects_duplicate_values(measurement_inputs, field):
    payload = measurement_inputs.model_dump()
    payload["query_plan"]["queries"][1][field] = payload["query_plan"]["queries"][0][field]
    with pytest.raises(ValueError):
        MeasurementInputs.model_validate(payload)


def test_paired_plan_requires_verbatim_page_evidence(measurement_inputs):
    payload = measurement_inputs.model_dump()
    payload["query_plan"]["queries"][0]["evidence"][0]["quote"] = "Invented claim"
    with pytest.raises(ValueError, match="unsupported page"):
        MeasurementInputs.model_validate(payload)


@pytest.mark.parametrize("field,value", [("deployment", "other-gpt"), ("instructions", "Different prompt"),
                                         ("prompt_version", "v3")])
def test_profile_configuration_changes_approval_hash(measurement_inputs, field, value):
    payload = measurement_inputs.model_dump()
    payload["profiles"][0][field] = value
    assert MeasurementInputs.model_validate(payload).approval_hash != measurement_inputs.approval_hash


def test_claude_label_cannot_reuse_gpt_adapter(measurement_inputs):
    payload = measurement_inputs.model_dump()
    payload["profiles"][0]["profile_id"] = "claude-backed"
    with pytest.raises(ValueError, match="provider"):
        MeasurementInputs.model_validate(payload)


@pytest.fixture
def measurement_results(measurement_inputs):
    query = measurement_inputs.query_plan.queries[0]
    source = Source(evidence_id="q-1-source-1", url=measurement_inputs.snapshot.url,
                    excerpt="Synthetic page", provenance="synthetic", returned_position=1)
    packet = RetrievalResult(query_id=query.query_id, grounding_query=query.grounding_query,
                             provenance="synthetic", status="completed", sources=(source,))
    answer = EvaluationResult(query_id=query.query_id, profile_id="chatgpt-style", provenance="synthetic",
                              status="completed", answer="Answer [q-1-source-1]", citation_ids=(source.evidence_id,),
                              sources=packet.sources)
    return MeasurementResults(inputs=measurement_inputs, retrievals=(packet,), results=(answer,))


def test_measurement_preserves_packet_without_an_answer(measurement_results):
    payload = measurement_results.model_dump()
    payload["results"] = []
    assert MeasurementResults.model_validate(payload).retrievals == measurement_results.retrievals


@pytest.mark.parametrize("problem", ["missing-packet", "duplicate-packet", "wrong-query", "invented-source", "wrong-profile", "duplicate-answer", "wrong-provenance"])
def test_measurement_rejects_unbound_evidence(measurement_results, problem):
    payload = measurement_results.model_dump(mode="json")
    if problem == "missing-packet":
        payload["retrievals"] = []
    elif problem == "duplicate-packet":
        payload["retrievals"].append(payload["retrievals"][0])
    elif problem == "wrong-query":
        payload["retrievals"][0]["grounding_query"] = "Not approved"
    elif problem == "invented-source":
        payload["results"][0]["sources"][0]["url"] = "https://example.org/invented"
    elif problem == "wrong-profile":
        payload["results"][0]["profile_id"] = "unapproved-profile"
    elif problem == "duplicate-answer":
        payload["results"].append(payload["results"][0])
    else:
        payload["results"][0]["provenance"] = "live"
    with pytest.raises(ValueError):
        MeasurementResults.model_validate(payload)


@pytest.mark.parametrize("position", [None, 0, 6])
def test_retrieval_requires_bounded_observed_position(measurement_results, position):
    payload = measurement_results.retrievals[0].model_dump(mode="json")
    payload["sources"][0]["returned_position"] = position
    with pytest.raises(ValueError, match="positions"):
        RetrievalResult.model_validate(payload)