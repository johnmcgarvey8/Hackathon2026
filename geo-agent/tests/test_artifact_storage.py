import pytest

from geo_agent.artifact_storage import LocalArtifactStorage
from geo_agent.workflow import Conflict, NotFound


def test_assessment_bundles_are_deterministic_and_leave_legacy_bytes_unchanged():
    import io
    import json
    import zipfile
    from geo_agent.artifacts import render_assessment_bundle, render_measurement_bundle
    from geo_agent.evidence_assessment import BrandDefinition, BrandDefinitionRecord, build_evidence_assessment
    from test_evaluation import measurement_fixture

    measurement = measurement_fixture()
    legacy = render_measurement_bundle(measurement)
    record = BrandDefinitionRecord(run_id="test", definition_version=1,
                                   definition=BrandDefinition(name="<script>Synthetic</script>"))
    report = build_evidence_assessment(measurement, record, run_id="test", run_revision=1)
    assessed = render_measurement_bundle(measurement, assessment=report)
    companion = render_assessment_bundle(measurement, report)
    assert render_assessment_bundle(measurement, report) == companion
    assert render_measurement_bundle(measurement) == legacy
    with zipfile.ZipFile(io.BytesIO(assessed)) as archive, zipfile.ZipFile(io.BytesIO(legacy)) as old:
        assert json.loads(archive.read("manifest.json"))["schema_version"] == "geo-measurement-manifest/v2"
        assert archive.read("measurement.json") == old.read("measurement.json")
        assert archive.read("scores.json") == old.read("scores.json")
        assert "<script>" not in archive.read("evidence-assessment.md").decode()
        assert "Brand definitions may be model-inferred" in archive.read("evidence-assessment.md").decode()
        with zipfile.ZipFile(io.BytesIO(companion)) as assessment_archive:
            assert assessment_archive.read("evidence-assessment.md") == archive.read("evidence-assessment.md")
        assert json.loads(archive.read("evidence-assessment.json")) == report.model_dump(mode="json")
    with pytest.raises(ValueError, match="does not match"):
        render_assessment_bundle(measurement, report.model_copy(update={"grounding": {}}))


def test_local_artifact_storage_is_idempotent_and_path_safe(tmp_path):
    storage = LocalArtifactStorage(tmp_path / "artifacts")
    storage.put("owner/run/hash.zip", b"immutable bundle")
    storage.put("owner/run/hash.zip", b"immutable bundle")

    assert storage.get("owner/run/hash.zip") == b"immutable bundle"
    with pytest.raises(Conflict, match="different content"):
        storage.put("owner/run/hash.zip", b"changed bundle")
    with pytest.raises(ValueError, match="safe relative"):
        storage.put("../outside.zip", b"content")
    with pytest.raises(NotFound, match="not found"):
        storage.get("owner/run/missing.zip")


def test_content_strategy_bundle_is_deterministic_and_escapes_saved_evidence():
    import io
    import json
    import zipfile
    from geo_agent.artifacts import render_content_strategy_bundle, render_measurement_bundle
    from geo_agent.recommendations import build_content_strategy
    from test_recommendations import measurement, packet, source

    saved = measurement((packet("q-1", (source(excerpt="How to install <script>untrusted</script>."),)),),
                        content="Local places. How to install <script>not instructions</script>.")
    legacy = render_measurement_bundle(saved)
    content = render_content_strategy_bundle(saved)
    assert render_content_strategy_bundle(saved) == content
    assert render_measurement_bundle(saved) == legacy
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert json.loads(archive.read("content-strategy.json")) == build_content_strategy(saved).model_dump(mode="json")
        markdown = archive.read("content-strategy.md").decode()
        assert "<script>" not in markdown and "&lt;script&gt;" in markdown
        assert "Grounding: Content Returned" in markdown and "LLM: Observed Selection" in markdown
        assert "Insufficient matching saved evidence" in markdown
        assert json.loads(archive.read("manifest.json"))["publish_permission"] is False