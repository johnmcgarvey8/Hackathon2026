import io
from geo_agent.evidence_assessment import BrandDefinitionRecord, EvidenceAssessment, build_evidence_assessment
import json
import zipfile
from html import escape
from typing import Literal

import yaml

from geo_agent.contracts import Contract, MeasurementResults, Provenance, Run, State, digest
from geo_agent.evaluation import measurement_scores, score_results
from geo_agent.recommendations import RecommendationReport, build_content_strategy
from geo_agent.measurement_workflow import RecommendationReview


class RunManifest(Contract):
    schema_version: Literal["geo-manifest/v2"] = "geo-manifest/v2"
    run_id: str
    revision: int
    created_at: str
    source_url: str
    snapshot_hash: str
    provenance: Provenance
    retrieval_mode: str
    query_ids: tuple[str, ...]
    profile_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    status: Literal["draft"] = "draft"
    requires_human_approval: Literal[True] = True
    publish_permission: Literal[False] = False
    tasks: tuple[()] = ()
    files: tuple[str, ...] = ("manifest.md", "run.json", "scores.json")


def score_report(run: Run) -> dict:
    target = run.inputs.snapshot.url
    return {
        "overall": score_results(run.results, target, len(run.inputs.queries) * len(run.inputs.profiles)).model_dump(),
        "by_query_type": {
            label: score_results(
                tuple(result for result in run.results if result.query_id in {
                    query.query_id for query in run.inputs.queries if query.branded == branded
                }),
                target, sum(query.branded == branded for query in run.inputs.queries) * len(run.inputs.profiles),
            ).model_dump()
            for label, branded in (("branded", True), ("unbranded", False))
        },
        "by_profile": {
            profile.profile_id: score_results(
                tuple(result for result in run.results if result.profile_id == profile.profile_id),
                target, len(run.inputs.queries),
            ).model_dump()
            for profile in run.inputs.profiles
        },
        "by_query": {
            query.query_id: score_results(
                tuple(result for result in run.results if result.query_id == query.query_id),
                target, len(run.inputs.profiles),
            ).model_dump()
            for query in run.inputs.queries
        },
    }


def render_bundle(run: Run) -> bytes:
    if run.state != State.EXPORTED:
        raise ValueError("Only a frozen exported run can be rendered")
    manifest = RunManifest(
        run_id=run.run_id,
        revision=run.revision,
        created_at=run.created_at.isoformat(),
        source_url=str(run.inputs.snapshot.url),
        snapshot_hash=run.inputs.snapshot.content_hash,
        provenance=run.inputs.snapshot.provenance,
        retrieval_mode=run.inputs.retrieval_mode,
        query_ids=tuple(query.query_id for query in run.inputs.queries),
        profile_ids=tuple(profile.profile_id for profile in run.inputs.profiles),
        evidence_ids=tuple(sorted({source.evidence_id for result in run.results for source in result.sources})),
    )
    scores = score_report(run)
    overall = scores["overall"]
    display_score = "N/A" if overall["score"] is None else f'{overall["score"]}/100'
    frontmatter = yaml.safe_dump(manifest.model_dump(mode="json"), sort_keys=True, default_style='"')
    markdown = (
        f"---\n{frontmatter}---\n\n# GEO Run Manifest\n\n"
        f"Provenance: **{manifest.provenance.value}**. Score: **{display_score}**.\n\n"
        f"Verified exact-page answers: {overall['numerator']}/{overall['denominator']}; "
        f"intended answers: {overall['intended']}; errors: {overall['errors']}; missing: {overall['missing']}.\n\n"
        "## Method\n\n"
        "Score = round(100 * completed answers with a source-backed exact-page citation / "
        "completed evaluable answers). No citations count as zero; failed calls are excluded "
        "from the denominator and reduce coverage. No answers means N/A.\n\n"
        "Branded and unbranded query scores are reported separately in scores.json. "
        "A source-backed citation verifies page inclusion, not factual correctness of every claim.\n\n"
        "## Files and Evidence\n\n"
        "- [Run inputs, approval, answers and source excerpts](run.json)\n"
        "- [Overall, query and profile scores](scores.json)\n\n"
        "Evidence IDs resolve to results[].sources[] in run.json. No external resolver is required.\n\n"
        "## Recommendations and Limits\n\n"
        "No recommendation tasks were generated. Comparison and recommendation generation are "
        "not implemented in this foundation; there is insufficient comparison evidence to propose edits. "
        "Synthetic results are software test data, not live observations or model responses. "
        "URL matching is conservative; canonical aliases and tracking-parameter equivalence are not inferred.\n\n"
        "## Review\n\n"
        "A human must review provenance, source passages and coverage before using these results. "
        "This bundle grants no publishing permission. Schema geo-manifest/v2 is a new backend "
        "contract, not a reinterpretation of the offline prototype's v1 task fields.\n"
    )
    payload = run.model_dump(mode="json", exclude={"owner", "start_key"})
    if payload["approval"]:
        payload["approval"].pop("actor", None)
    files = {
        "manifest.md": markdown,
        "run.json": json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        "scores.json": json.dumps(scores, sort_keys=True, indent=2) + "\n",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for filename, content in sorted(files.items()):
            archive.writestr(zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0)), content)
    return buffer.getvalue()


class MeasurementManifest(Contract):
    schema_version: Literal["geo-measurement-manifest/v1"] = "geo-measurement-manifest/v1"
    measurement_hash: str
    input_hash: str
    snapshot_hash: str
    source_url: str
    provenance: Provenance
    method_version: str
    retrieval_mode: str
    query_ids: tuple[str, ...]
    profile_ids: tuple[str, ...]
    recommendation_status: Literal["not-generated", "completed", "insufficient-evidence"]
    recommendation_review_status: Literal["not-reviewed", "reviewed"]
    provisional: bool
    status: Literal["draft"] = "draft"
    requires_human_approval: Literal[True] = True
    publish_permission: Literal[False] = False
    files: tuple[str, ...] = (
        "manifest.json",
        "manifest.md",
        "measurement.json",
        "recommendation-review.json",
        "recommendations.json",
        "scores.json",
    )


def _markdown_text(value: str) -> str:
    text = escape(value.strip(), quote=False)
    for character in ("\\", "`", "*", "_", "[", "]", "#", "|"):
        text = text.replace(character, f"\\{character}")
    return text


def _accepted_task_markdown(task) -> str:
    page_evidence = "\n".join(
        f"- `{reference.evidence_id}`: {_markdown_text(reference.quote)}"
        for reference in task.page_evidence
    )
    comparison_evidence = "\n".join(
        f"- `{reference.evidence_id}`: {_markdown_text(reference.quote)}"
        for reference in task.comparison_evidence
    )
    return (
        f"# {_markdown_text(task.title)}\n\n"
        "Decision: **accepted for human implementation planning**. Publishing permission: **none**.\n\n"
        f"Query: `{task.query_id}`. Priority: {task.priority}. Confidence: {_markdown_text(task.confidence)}.\n\n"
        f"## Target Section\n\n{_markdown_text(task.target_section)}\n\n"
        f"## Proposed Change\n\n{_markdown_text(task.proposed_change)}\n\n"
        f"## Rationale\n\n{_markdown_text(task.rationale)}\n\n"
        f"## Page Evidence\n\n{page_evidence}\n\n"
        f"## Comparison Evidence\n\n{comparison_evidence}\n\n"
        f"## Verification\n\n{_markdown_text(task.verification)}\n\n"
        "This task remains a hypothesis. Verify page facts, ownership, accessibility, legal requirements, "
        "and measurement design before editing or publishing.\n"
    )


def render_measurement_bundle(
    measurement: MeasurementResults,
    recommendations: RecommendationReport | None = None,
    recommendation_review: RecommendationReview | None = None,
    assessment: EvidenceAssessment | None = None,
) -> bytes:
    """Render a saved-data draft; the owning service must enforce access and approval."""
    measurement = MeasurementResults.model_validate(measurement.model_dump(mode="json"))
    if recommendations is not None:
        recommendations.validate_for(measurement)
        recommendations = RecommendationReport.model_validate(recommendations.model_dump(mode="json"))
    if recommendation_review is not None:
        if recommendations is None:
            raise ValueError("Recommendation review requires a saved report")
        recommendation_review.validate_for(measurement, recommendations)
        recommendation_review = RecommendationReview.model_validate(
            recommendation_review.model_dump(mode="json")
        )
    scores = measurement_scores(measurement)
    inputs = measurement.inputs
    payload = measurement.model_dump(mode="json")
    accepted_ids = {
        decision.task_id
        for decision in recommendation_review.decisions
        if decision.decision == "accepted"
    } if recommendation_review is not None else set()
    accepted_files = tuple(
        f"recommendations/{task.task_id}.md"
        for task in recommendations.tasks
        if task.task_id in accepted_ids
    ) if recommendations is not None else ()
    manifest_files = tuple(sorted((*MeasurementManifest.model_fields["files"].default, *accepted_files)))
    manifest = MeasurementManifest(
        measurement_hash=digest(payload), input_hash=inputs.approval_hash,
        snapshot_hash=inputs.snapshot.content_hash, source_url=str(inputs.brief.url),
        provenance=inputs.snapshot.provenance, method_version=inputs.method_version,
        retrieval_mode=inputs.retrieval_mode,
        query_ids=tuple(query.query_id for query in inputs.query_plan.queries),
        profile_ids=tuple(profile.profile_id for profile in inputs.profiles),
        recommendation_status=recommendations.status if recommendations else "not-generated",
        recommendation_review_status="reviewed" if recommendation_review else "not-reviewed",
        provisional=scores["provisional"],
        files=manifest_files,
    )
    overall = scores["overall"]
    display_score = "N/A" if overall["score"] is None else f'{overall["score"]}/100'
    markdown = (
        "# GEO Measurement Draft\n\n"
        f"Provenance: **{manifest.provenance.value}**. Exact-page citation score: **{display_score}**.\n\n"
        f"Source-backed exact-page answers: {overall['numerator']}/{overall['denominator']}; "
        f"intended: {overall['intended']}; errors: {overall['errors']}; missing: {overall['missing']}. "
        f"Provisional: {'yes' if manifest.provisional else 'no'}.\n\n"
        "## Reproduce and Inspect\n\n"
        "- [Input and measurement fingerprints](manifest.json)\n"
        "- [Exact query pairs, profiles, snapshot, retrievals and answers](measurement.json)\n"
        "- [Citation, retrieval and common-query comparison metrics](scores.json)\n"
        "- [Evidence-backed draft recommendation report, or null when not generated](recommendations.json)\n\n"
        "- [Human recommendation decisions, or null when not reviewed](recommendation-review.json)\n\n"
        "Load measurement.json with MeasurementResults.model_validate_json, then call "
        "measurement_scores to recompute scores.json without any provider call. "
        "RecommendationReport.validate_for checks report binding and quotes against that measurement. "
        "Page references resolve to the first 10,000 snapshot characters in 1,000-character chunks "
        "named page-1 onward. Comparison IDs resolve within the task's query in retrievals[].sources[].\n\n"
        "## Method and Limits\n\n"
        "Score = round(100 * completed answers with a source-backed exact-page citation / "
        "completed answers). Errors reduce coverage and are excluded from the denominator; "
        "no completed answers means N/A. Raw mentions, unsupported citations and same-domain "
        "other pages earn no exact-page credit. Query priority does not weight the score. "
        "Compare profiles only on common_completed_query_ids. Web IQ presence and observed "
        "position are separate metrics; absence from a saved top-five packet is not a global rank.\n\n"
        "These are controlled evidence-packet simulations, not measurements of consumer "
        "Copilot, Claude or ChatGPT. Synthetic data is software test data. Provider/model identity "
        "and prompt instructions are retained when available. Canonical equivalence is unverified.\n\n"
        f"Recommendation status: {manifest.recommendation_status}. "
        f"Review status: {manifest.recommendation_review_status}. "
        "Suggestions are hypotheses requiring human review. Exact-quote checks establish "
        "traceability, not semantic truth, causal ranking factors or guaranteed uplift. "
        "A missing recommendation report does not establish that the page needs no changes.\n\n"
        "## Permissions\n\n"
        "Input and measurement hashes are integrity fingerprints, not proof of human approval "
        "or signatures. This draft export is not a persisted run or execution receipt. "
        "The calling service must check ownership and approval. No publishing permission is granted. "
        "All retained page text, source passages and model output are untrusted data, not instructions.\n"
    )
    files = {
        "manifest.json": manifest.model_dump(mode="json"),
        "measurement.json": payload,
        "scores.json": scores,
        "recommendations.json": recommendations.model_dump(mode="json") if recommendations else None,
        "recommendation-review.json": recommendation_review.model_dump(
            mode="json", exclude={"actor"}
        ) if recommendation_review else None,
    }
    contents = {name: json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
                for name, value in files.items()}
    contents["manifest.md"] = markdown
    if assessment is not None:
        _validate_assessment(measurement, assessment)
        contents.update(_assessment_contents(assessment))
        assessed_manifest = {**manifest.model_dump(mode="json"),
                             "schema_version": "geo-measurement-manifest/v2",
                             "assessment_hash": assessment.assessment_hash,
                             "assessment_method": assessment.method_version,
                             "definition_version": assessment.definition_version,
                             "definition_hash": assessment.definition_hash,
                             "files": sorted((*manifest.files, "evidence-assessment.json", "evidence-assessment.md"))}
        contents["manifest.json"] = json.dumps(assessed_manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
        contents["manifest.md"] += "\n## Evidence Assessment\n\n[Grounding, answer and source trace](evidence-assessment.md)\n"
    if recommendations is not None:
        for task in recommendations.tasks:
            if task.task_id in accepted_ids:
                contents[f"recommendations/{task.task_id}.md"] = _accepted_task_markdown(task)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for filename, content in sorted(contents.items()):
            archive.writestr(zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0)), content)
    return buffer.getvalue()


def _validate_assessment(measurement: MeasurementResults, assessment: EvidenceAssessment) -> None:
    record = BrandDefinitionRecord(run_id=assessment.run_id, definition_version=assessment.definition_version,
                                   definition=assessment.definition) if assessment.definition and assessment.definition_version else None
    expected = build_evidence_assessment(measurement, record, run_id=assessment.run_id,
                                         run_revision=assessment.run_revision)
    if assessment != expected:
        raise ValueError("Assessment does not match saved measurement and brand definition")


def _assessment_contents(assessment: EvidenceAssessment) -> dict[str, str]:
    def display(metric: dict) -> str:
        return f"{metric['numerator']}/{metric['denominator']}" if metric["rate"] is not None else "N/A"

    lines = ["# Grounding and Answer Assessment", "",
             f"Brand: {_markdown_text(assessment.definition.name) if assessment.definition else 'Not configured'}",
             f"Definition version: {assessment.definition_version or 'N/A'}. Method: {assessment.method_version}.", "",
             f"Brand in Web IQ packets: {display(assessment.grounding['brand_presence'])}.",
             f"Brand in completed answers: {display(assessment.answer['brand_presence'])}.",
             f"Sources cited: {display(assessment.answer['source_citation_rate'])}.",
             f"Brand-bearing sources cited: {display(assessment.answer['brand_sources_cited'])}.", "",
             "## Limits", "",
             "- Brand definitions may be model-inferred. Names, aliases and domain ownership are not independently verified.",
             *(f"- {item}" for item in assessment.limitations), ""]
    for query in assessment.queries:
        lines.extend([f"## {_markdown_text(query['query_id'])}: Grounding", "",
                      _markdown_text(query["grounding_query"]), "",
                      f"Retrieval: {query['status']}. Brand: {query['brand_status']}.", ""])
        for source in query["sources"]:
            lines.extend([f"### {_markdown_text(source['evidence_id'])}", "",
                          f"Position: {source['returned_position']}. Brand: {source['brand']['status']}. "
                          f"Configured brand-domain match: {source['brand_owned_host']}. Target relation: {source['target_relation']}.",
                          _markdown_text(source["url"]), "", _markdown_text(source["title"] or ""), "",
                          _markdown_text(source["excerpt"]), ""])
        for answer in (item for item in assessment.answers if item["query_id"] == query["query_id"]):
            lines.extend([f"### {_markdown_text(answer['profile_id'])}: Answer", "",
                          f"Status: {answer['status']}. Brand: {answer['brand']['status']}. "
                          f"Sources cited: {display(answer['source_citation_rate'])}.", "",
                          _markdown_text(answer["answer"]), "",
                          "Unsupported citation IDs: " + _markdown_text(", ".join(answer["unsupported_citation_ids"]) or "None"), ""])
            for trace in answer["sources"]:
                cited = "Unknown" if trace["cited"] is None else "Cited" if trace["cited"] else "Not cited"
                lines.append(f"- {_markdown_text(trace['evidence_id'])}: {cited}. Selection reason not recorded.")
                for overlap in trace["shared_wording"]:
                    lines.append("  Shared wording" + (" (non-unique)" if overlap["non_unique"] else "") +
                                 ": " + _markdown_text(overlap["answer_quote"]))
            lines.append("")
    return {"evidence-assessment.json": json.dumps(assessment.model_dump(mode="json"), ensure_ascii=True,
                                                  sort_keys=True, indent=2) + "\n",
            "evidence-assessment.md": "\n".join(lines) + "\n"}


def render_assessment_bundle(measurement: MeasurementResults, assessment: EvidenceAssessment) -> bytes:
    _validate_assessment(measurement, assessment)
    contents = _assessment_contents(assessment)
    files = {"measurement.json": measurement.model_dump(mode="json"), "scores.json": measurement_scores(measurement),
             "brand-definition.json": assessment.definition.model_dump(mode="json") if assessment.definition else None,
             "manifest.json": {"schema_version": "geo-evidence-assessment-manifest/v1",
                               "assessment_hash": assessment.assessment_hash,
                               "measurement_hash": assessment.measurement_hash, "input_hash": assessment.input_hash,
                               "definition_hash": assessment.definition_hash, "definition_version": assessment.definition_version,
                               "method_version": assessment.method_version,
                               "files": ["brand-definition.json", "evidence-assessment.json", "evidence-assessment.md",
                                         "manifest.json", "measurement.json", "scores.json"]}}
    contents.update({name: json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n" for name, value in files.items()})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for filename, content in sorted(contents.items()):
            archive.writestr(zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0)), content)
    return buffer.getvalue()


def render_content_strategy_bundle(measurement: MeasurementResults) -> bytes:
    report = build_content_strategy(measurement)
    lines = ["# Content Recommendations", "", f"Target: {_markdown_text(report.target_url)}", "",
             f"Provenance: {report.provenance}. Retrieved queries: {report.retrieved_queries}/{report.query_count}. "
             f"Completed answers: {report.completed_answers}/{report.expected_answers}.", "",
             "## Captured Page", "", _markdown_text(report.target_excerpt), ""]
    queries = {query.query_id: query for query in measurement.inputs.query_plan.queries}
    for layer, title in (("grounding", "Grounding: Content Returned"), ("answer", "LLM: Observed Selection")):
        lines.extend([f"## {title}", ""])
        matched = [pattern for pattern in report.patterns if getattr(pattern, f"{layer}_change")]
        if not matched:
            lines.extend(["Insufficient matching saved evidence for a content recommendation in this layer.", ""])
        for pattern in matched:
            lines.extend([f"### {pattern.label}", "",
                          f"Returned excerpts: {len(pattern.sources)}; queries: {pattern.query_count}. "
                          f"Cited source/answer pairs: {pattern.cited_appearances}/{pattern.citation_opportunities}. "
                          f"Final answers with this wording cue: {len(pattern.answers)}/{report.completed_answers}.", "",
                          "Change to test: " + _markdown_text(getattr(pattern, f"{layer}_change")), ""])
            if pattern.target_evidence:
                lines.extend([f"Captured page ({pattern.target_evidence.evidence_id}): "
                              + _markdown_text(pattern.target_evidence.quote), ""])
            else:
                lines.extend(["Cue not seen in the captured page; check the full page before adding content.", ""])
            for source in pattern.sources:
                query = queries[source.query_id]
                lines.extend([f"- {source.query_id}/{source.evidence_id}; {source.target_relation}; "
                              f"returned position {source.returned_position}; cited by: {', '.join(source.cited_by) or 'none'}.",
                              f"  Query: {_markdown_text(query.grounding_query)}",
                              f"  Source: {_markdown_text(source.url)}", f"  Quote: {_markdown_text(source.quote)}"])
            if layer == "answer":
                for answer in pattern.answers:
                    lines.extend([f"- Answer {answer.query_id}/{answer.profile_id}: {_markdown_text(answer.quote)}"])
            lines.append("")
    lines.extend(["## Verification", "", report.verification, "", "## Limits", "",
                  *(f"- {item}" for item in report.limitations), ""])
    files = {
        "content-strategy.json": report.model_dump(mode="json"),
        "measurement.json": measurement.model_dump(mode="json"),
        "manifest.json": {"schema_version": "geo-content-strategy-manifest/v1",
                          "measurement_hash": report.measurement_hash,
                          "report_hash": digest(report.model_dump(mode="json")),
                          "method_version": report.method_version, "publish_permission": False,
                          "files": ["content-strategy.json", "content-strategy.md", "manifest.json", "measurement.json"]},
    }
    contents = {name: json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n" for name, value in files.items()}
    contents["content-strategy.md"] = "\n".join(lines)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for filename, content in sorted(contents.items()):
            archive.writestr(zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0)), content)
    return buffer.getvalue()