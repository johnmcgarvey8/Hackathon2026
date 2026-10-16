import io
import json
import zipfile

import pytest

from geo_agent.artifacts import render_measurement_bundle
from geo_agent.contracts import digest
from geo_agent.measurement_workflow import (
    OwnerIdentity,
    RecommendationDecisionV2,
    RecommendationReviewV2,
)
from geo_agent.recommendation_plan import (
    METHOD_HASH,
    PlanProposal,
    RecommendationPlanReport,
    build_recommendation_context_v3,
    validate_plan,
)
from geo_agent.webiq import ProviderError
from geo_agent.technical_checks import (
    TechnicalField,
    TechnicalSignals,
    run_technical_checks,
)

from test_recommendations import measurement, outcome, packet, source


def proposal(**updates):
    value = {
        "diagnosis": "The target was returned but was not cited in the completed answer.",
        "key_findings": [{
            "summary": "The completed answer did not cite the target page.",
            "evidence": [{
                "evidence_id": "sig-q1.exact_page_cited_by",
                "quote": "[]",
            }],
        }],
        "page_optimisations": [{
            "rec_id": "opt-1",
            "category": "content",
            "subtype": "answer-ready-summary",
            "title": "Add a concise visit summary",
            "target_location": "Introduction",
            "observation": "The target page was returned but not cited.",
            "observation_basis": "observed-in-measurement",
            "proposed_change": "Add a concise, verifiable summary for the measured intent.",
            "implementation": {
                "outline": ["Summarise the verified visit details in two sentences."],
                "schema_org": [],
            },
            "addresses_query_ids": ["q-1"],
            "rationale": "A direct summary may make the relevant passage easier to select.",
            "evidence": [{
                "evidence_id": "page-1",
                "quote": "Local places",
            }],
            "comparison_evidence": [{
                "evidence_id": "source-1",
                "quote": "Opening times",
            }],
            "impact": "high",
            "effort": "S",
            "confidence": "medium",
            "facts_to_verify": [],
            "verification": "Verify all details, publish only after approval, then rerun.",
            "depends_on": [],
            "source_check_ids": [],
            "origin": "model",
        }],
        "new_content_recommendations": [],
        "not_recommended": [],
        "reason": "",
    }
    value.update(updates)
    return value


def test_v3_context_is_deterministic_and_bound_to_saved_measurement():
    saved_packet = packet("q-1", (source(),))
    saved = measurement((saved_packet,), (outcome(saved_packet),))

    first = build_recommendation_context_v3(saved)
    second = build_recommendation_context_v3(saved)

    assert first == second
    assert first["binding"]["measurement_hash"] == digest(saved.model_dump(mode="json"))
    assert first["versions"]["method_hash"] == METHOD_HASH
    assert first["signals"]["q-1"]["gap_class"] == "not-retrieved"
    assert first["evidence_registry"]["page-1"].startswith("Local places")
    assert first["evidence_registry"]["source-1"] == "Opening times listed."


def test_v3_plan_validates_ranks_and_quarantines_only_invalid_items():
    saved_packet = packet("q-1", (source(),))
    saved = measurement((saved_packet,), (outcome(saved_packet),))
    context = build_recommendation_context_v3(saved)
    invalid = proposal()
    invalid["page_optimisations"].append({
        **invalid["page_optimisations"][0],
        "rec_id": "opt-2",
        "title": "Unsupported item",
        "evidence": [{"evidence_id": "page-1", "quote": "not present"}],
    })

    report = validate_plan(saved, PlanProposal.model_validate(invalid), context)

    assert isinstance(report, RecommendationPlanReport)
    assert report.status == "completed"
    assert [item.rec_id for item in report.items] == ["opt-1"]
    assert report.items[0].tier == "P1"
    assert report.items[0].score_breakdown.priority_score > 0
    assert report.validation_log[0].item_id == "opt-2"
    assert report.publish_permission is False
    report.validate_for(saved, context)


def test_v3_plan_quarantines_duplicate_ids_instead_of_rejecting_proposal():
    saved_packet = packet("q-1", (source(),))
    saved = measurement((saved_packet,), (outcome(saved_packet),))
    context = build_recommendation_context_v3(saved)
    payload = proposal()
    payload["page_optimisations"].append(dict(payload["page_optimisations"][0]))

    report = validate_plan(saved, PlanProposal.model_validate(payload), context)

    assert [item.rec_id for item in report.items] == ["opt-1"]
    assert any(
        entry.item_id == "opt-1" and entry.rule == "duplicate-item-id"
        for entry in report.validation_log
    )


def test_v3_empty_plan_without_model_reason_gets_deterministic_reason():
    saved_packet = packet("q-1", (source(),))
    saved = measurement((saved_packet,), (outcome(saved_packet),))
    context = build_recommendation_context_v3(saved)

    report = validate_plan(
        saved,
        PlanProposal.model_validate(proposal(
            page_optimisations=[],
            new_content_recommendations=[],
            reason="",
        )),
        context,
        include_rule_items=False,
    )

    assert report.status == "insufficient-evidence"
    assert report.reason == "All proposed recommendations were quarantined by deterministic validation."


def test_v3_new_content_uses_neutral_basis_weight():
    saved_packet = packet("q-1", (source(),))
    saved = measurement((saved_packet,), (outcome(saved_packet),))
    context = build_recommendation_context_v3(saved)
    payload = proposal(page_optimisations=[], new_content_recommendations=[{
        "rec_id": "new-1",
        "working_title": "Family visit planning guide",
        "page_type": "buying-guide",
        "primary_intent": "Help families plan a visit.",
        "target_query_ids": ["q-1"],
        "gap_type": "depth-required",
        "gap_justification": "The current page cannot cover the measured planning need in depth.",
        "existing_page_check": ["https://target.example/visit"],
        "evidence": [{"evidence_id": "page-1", "quote": "Local places"}],
        "comparison_evidence": [],
        "impact": "medium",
        "effort": "M",
        "confidence": "medium",
    }])

    report = validate_plan(saved, payload, context)

    assert report.items[0].kind == "new-content"
    assert report.items[0].score_breakdown.basis_weight == 1.0
    assert report.items[0].brief_status == "not-generated"


def test_technical_checks_preserve_not_provided_and_scope_absence():
    saved = measurement()
    uncaptured = run_technical_checks(
        TechnicalSignals(reason="feature-disabled"),
        saved.inputs.brief,
        saved.inputs.snapshot,
    )
    captured = run_technical_checks(
        TechnicalSignals(
            status="captured",
            jsonld_types=TechnicalField(state="not-found"),
            canonical=TechnicalField(state="not-found"),
            meta_title=TechnicalField(state="present", value="Short"),
            meta_description=TechnicalField(state="not-provided"),
        ),
        saved.inputs.brief,
        saved.inputs.snapshot,
    )

    assert uncaptured.findings == ()
    assert {finding.check_id for finding in captured.findings} >= {"T-02", "T-03", "T-07"}
    assert all(finding.field != "meta_description" for finding in captured.findings)


def test_v3_strict_mode_rejects_an_invalid_item(monkeypatch):
    saved_packet = packet("q-1", (source(),))
    saved = measurement((saved_packet,), (outcome(saved_packet),))
    context = build_recommendation_context_v3(saved)
    invalid = proposal()
    invalid["page_optimisations"][0]["evidence"] = [{
        "evidence_id": "page-1",
        "quote": "not present",
    }]
    monkeypatch.setenv("GEO_RECOMMENDATION_STRICT", "true")

    with pytest.raises(ProviderError, match="strict validation"):
        validate_plan(saved, invalid, context)


def test_v3_adds_rule_item_for_unaddressed_high_technical_finding():
    saved_packet = packet("q-1", (source(),))
    saved = measurement((saved_packet,), (outcome(saved_packet),))
    signals = TechnicalSignals(
        status="captured",
        robots=TechnicalField(state="present", value="noindex"),
    )
    context = build_recommendation_context_v3(saved, technical_signals=signals)

    empty_proposal = proposal(
        page_optimisations=[],
        reason="No model-authored recommendation was needed.",
    )
    report = validate_plan(
        saved,
        empty_proposal,
        context,
    )
    gated = validate_plan(
        saved,
        empty_proposal,
        context,
        include_rule_items=False,
    )

    item = next(item for item in report.items if item.rec_id == "opt-T01")
    assert item.kind == "page-optimisation"
    assert item.origin == "rule"
    assert item.source_check_ids == ("T-01",)
    assert item.evidence[0].evidence_id == "tech.robots"
    assert gated.status == "insufficient-evidence"
    assert gated.items == ()


def test_v3_review_and_export_preserve_deferred_items_and_export_only_accepted():
    saved_packet = packet("q-1", (source(),))
    saved = measurement((saved_packet,), (outcome(saved_packet),))
    context = build_recommendation_context_v3(saved)
    payload = proposal(new_content_recommendations=[{
        "rec_id": "new-1",
        "working_title": "Family visit planning guide",
        "page_type": "buying-guide",
        "primary_intent": "Help families plan a visit.",
        "target_query_ids": ["q-1"],
        "gap_type": "depth-required",
        "gap_justification": "The current page cannot cover the measured planning need in depth.",
        "existing_page_check": ["https://target.example/visit"],
        "evidence": [{"evidence_id": "page-1", "quote": "Local places"}],
        "comparison_evidence": [],
        "impact": "medium",
        "effort": "M",
        "confidence": "medium",
    }])
    report = validate_plan(saved, payload, context)
    decisions = tuple(
        RecommendationDecisionV2(
            item_id=item.rec_id,
            decision="accepted" if item.rec_id == "opt-1" else "deferred",
            note=None if item.rec_id == "opt-1" else "Revisit after page optimisation.",
        )
        for item in report.items
    )
    review = RecommendationReviewV2(
        approval_hash=saved.inputs.approval_hash,
        measurement_hash=digest(saved.model_dump(mode="json")),
        recommendation_hash=digest(report.model_dump(mode="json")),
        actor=OwnerIdentity(tenant_id="private-tenant", object_id="private-reviewer"),
        decisions=decisions,
    )

    review.validate_for(saved, report)
    bundle = render_measurement_bundle(saved, report, review)

    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert "technical-signals.json" in archive.namelist()
        assert "recommendations/opt-1.md" in archive.namelist()
        assert "recommendations/new-1.md" not in archive.namelist()
        review_payload = json.loads(archive.read("recommendation-review.json"))
        assert "actor" not in review_payload
        assert {item["decision"] for item in review_payload["decisions"]} == {
            "accepted", "deferred",
        }
