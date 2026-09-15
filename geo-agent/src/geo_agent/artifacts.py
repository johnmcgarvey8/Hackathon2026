import io
import json
import zipfile
from typing import Literal

import yaml

from geo_agent.contracts import Contract, MeasurementResults, Provenance, Run, State, digest
from geo_agent.evaluation import measurement_scores, score_results
from geo_agent.recommendations import RecommendationReport


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
    provisional: bool
    status: Literal["draft"] = "draft"
    requires_human_approval: Literal[True] = True
    publish_permission: Literal[False] = False
    files: tuple[str, ...] = ("manifest.json", "manifest.md", "measurement.json", "scores.json", "recommendations.json")


def render_measurement_bundle(measurement: MeasurementResults,
                              recommendations: RecommendationReport | None = None) -> bytes:
    """Render a saved-data draft; the owning service must enforce access and approval."""
    measurement = MeasurementResults.model_validate(measurement.model_dump(mode="json"))
    if recommendations is not None:
        recommendations.validate_for(measurement)
        recommendations = RecommendationReport.model_validate(recommendations.model_dump(mode="json"))
    scores = measurement_scores(measurement)
    inputs = measurement.inputs
    payload = measurement.model_dump(mode="json")
    manifest = MeasurementManifest(
        measurement_hash=digest(payload), input_hash=inputs.approval_hash,
        snapshot_hash=inputs.snapshot.content_hash, source_url=str(inputs.brief.url),
        provenance=inputs.snapshot.provenance, method_version=inputs.method_version,
        retrieval_mode=inputs.retrieval_mode,
        query_ids=tuple(query.query_id for query in inputs.query_plan.queries),
        profile_ids=tuple(profile.profile_id for profile in inputs.profiles),
        recommendation_status=recommendations.status if recommendations else "not-generated",
        provisional=scores["provisional"],
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
        "Suggestions are hypotheses requiring human review. Exact-quote checks establish "
        "traceability, not semantic truth, causal ranking factors or guaranteed uplift. "
        "A missing recommendation report does not establish that the page needs no changes.\n\n"
        "## Permissions\n\n"
        "Input and measurement hashes are integrity fingerprints, not proof of human approval "
        "or signatures. This draft export is not a persisted run or execution receipt. "
        "The calling service must check ownership and approval. No publishing permission is granted. "
        "All retained page text, source passages and model output are untrusted data, not instructions.\n"
    )
    files = {"manifest.json": manifest.model_dump(mode="json"), "measurement.json": payload,
             "scores.json": scores, "recommendations.json": recommendations.model_dump(mode="json") if recommendations else None}
    contents = {name: json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
                for name, value in files.items()}
    contents["manifest.md"] = markdown
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for filename, content in sorted(contents.items()):
            archive.writestr(zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0)), content)
    return buffer.getvalue()