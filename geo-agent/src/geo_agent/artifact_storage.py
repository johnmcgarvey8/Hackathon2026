import os
import re
import zipfile
from io import BytesIO
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import Field

from geo_agent.contracts import Contract, digest, identifier, utc_now
from geo_agent.artifacts import render_measurement_bundle
from geo_agent.evidence_assessment import BrandDefinitionRecord, build_evidence_assessment
from geo_agent.measurement_workflow import MeasurementRun, MeasurementState, OwnerIdentity
from geo_agent.workflow import Conflict, NotFound


class ArtifactKind(StrEnum):
    MEASUREMENT = "measurement"
    ASSESSMENT = "assessment"
    CONTENT_STRATEGY = "content_strategy"


class MeasurementArtifact(Contract):
    schema_version: str = Field(default="geo-measurement-artifact/v1", pattern=r"^geo-measurement-artifact/v1$")
    artifact_id: str = Field(default_factory=identifier)
    run_id: str = Field(min_length=1, max_length=64)
    kind: ArtifactKind = ArtifactKind.MEASUREMENT
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str = Field(min_length=1, max_length=120)
    size: int = Field(ge=1)
    storage_key: str = Field(min_length=1, max_length=500)
    created_at: datetime = Field(default_factory=utc_now)


class ArtifactStorage(Protocol):
    def put(self, storage_key: str, content: bytes) -> None: ...

    def get(self, storage_key: str) -> bytes: ...


class ArtifactRepository(Protocol):
    def get_brand_definition(self, run_id: str, owner: OwnerIdentity,
                             version: int | None = None) -> BrandDefinitionRecord | None: ...

    def get(self, run_id: str, owner: OwnerIdentity) -> MeasurementRun: ...

    def persist_export(
        self,
        artifact: MeasurementArtifact,
        owner: OwnerIdentity,
        revision: int,
    ) -> tuple[MeasurementArtifact, MeasurementRun]: ...

    def persist_companion(
        self,
        artifact: MeasurementArtifact,
        owner: OwnerIdentity,
        revision: int,
    ) -> MeasurementArtifact: ...

    def get_artifact(
        self,
        run_id: str,
        artifact_id: str,
        owner: OwnerIdentity,
    ) -> MeasurementArtifact: ...

    def get_run_artifact(self, run_id: str, owner: OwnerIdentity) -> MeasurementArtifact: ...

    def list_artifacts(
        self,
        run_id: str,
        owner: OwnerIdentity,
        limit: int = 50,
        *,
        before_created_at: datetime | None = None,
        before_artifact_id: str | None = None,
    ) -> tuple[MeasurementArtifact, ...]: ...


class LocalArtifactStorage:
    _PART = re.compile(r"^[A-Za-z0-9._-]+$")

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_key: str) -> Path:
        key = PurePosixPath(storage_key)
        if (
            key.is_absolute()
            or not key.parts
            or any(part in {"", ".", ".."} or not self._PART.fullmatch(part) for part in key.parts)
        ):
            raise ValueError("Artifact storage key must contain safe relative path segments")
        path = self.root.joinpath(*key.parts).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as error:
            raise ValueError("Artifact storage key escapes the configured root") from error
        return path

    def put(self, storage_key: str, content: bytes) -> None:
        if not content:
            raise ValueError("Artifact content cannot be empty")
        target = self._path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != content:
                raise Conflict("Artifact storage key is already bound to different content")
            return
        temporary = target.with_name(f".{target.name}.{identifier()}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, storage_key: str) -> bytes:
        try:
            return self._path(storage_key).read_bytes()
        except FileNotFoundError as error:
            raise NotFound("Artifact content not found") from error


class ArtifactService:
    media_type = "application/zip"

    def __init__(self, repository: ArtifactRepository, storage: ArtifactStorage):
        self.repository = repository
        self.storage = storage

    def export(
        self,
        run_id: str,
        owner: OwnerIdentity,
        revision: int,
        definition_version: int | None = None,
        definition_hash: str | None = None,
    ) -> tuple[MeasurementArtifact, MeasurementRun]:
        run = self.repository.get(run_id, owner)
        if run.state == MeasurementState.EXPORTED:
            return self.repository.get_run_artifact(run_id, owner), run
        if run.revision != revision:
            raise Conflict("Stale measurement revision; reload the run")
        if (
            run.state not in {MeasurementState.READY, MeasurementState.PARTIAL, MeasurementState.FAILED}
            or run.measurement is None
        ):
            raise Conflict("Only a completed measurement can be exported")
        record = self.repository.get_brand_definition(run_id, owner, definition_version) if definition_version else None
        if definition_hash is not None and (record is None or record.definition_hash != definition_hash):
            raise Conflict("Brand definition does not match requested assessment")
        assessment = build_evidence_assessment(run.measurement, record, run_id=run_id, run_revision=run.revision) if record else None
        content = render_measurement_bundle(
            run.measurement,
            run.recommendations,
            run.recommendation_review,
            assessment,
        )
        content_hash = sha256(content).hexdigest()
        artifact = MeasurementArtifact(
            run_id=run.run_id,
            kind=ArtifactKind.MEASUREMENT,
            content_hash=content_hash,
            media_type=self.media_type,
            size=len(content),
            storage_key=f"{owner.key}/{run.run_id}/{ArtifactKind.MEASUREMENT.value}/{content_hash}.zip",
        )
        self.storage.put(artifact.storage_key, content)
        return self.repository.persist_export(artifact, owner, revision)

    def download(
        self,
        run_id: str,
        artifact_id: str,
        owner: OwnerIdentity,
    ) -> tuple[MeasurementArtifact, bytes]:
        artifact = self.repository.get_artifact(run_id, artifact_id, owner)
        content = self.storage.get(artifact.storage_key)
        if len(content) != artifact.size or sha256(content).hexdigest() != artifact.content_hash:
            raise Conflict("Stored artifact failed its integrity check")
        return artifact, content

    def export_companion(
        self,
        run_id: str,
        owner: OwnerIdentity,
        revision: int,
        kind: ArtifactKind,
        *,
        definition_version: int | None = None,
        definition_hash: str | None = None,
        measurement_hash: str | None = None,
    ) -> MeasurementArtifact:
        if kind == ArtifactKind.MEASUREMENT:
            raise Conflict("Use the frozen measurement export path for the main artifact")
        run = self.repository.get(run_id, owner)
        if run.revision != revision:
            raise Conflict("Stale measurement revision; reload the run")
        if run.measurement is None:
            raise Conflict("No saved measurement is available for a companion export")
        if measurement_hash is not None and digest(run.measurement.model_dump(mode="json")) != measurement_hash:
            raise Conflict("Saved measurement changed; reload the report")
        if kind == ArtifactKind.ASSESSMENT:
            if definition_version is None:
                raise Conflict("Assessment exports require a brand definition version")
            record = self.repository.get_brand_definition(run_id, owner, definition_version)
            if record is None or (definition_hash is not None and record.definition_hash != definition_hash):
                raise Conflict("Brand definition does not match requested assessment")
            report = build_evidence_assessment(
                run.measurement,
                record,
                run_id=run_id,
                run_revision=run.revision,
            )
            from geo_agent.artifacts import render_assessment_bundle

            content = render_assessment_bundle(run.measurement, report)
        else:
            from geo_agent.artifacts import render_content_strategy_bundle

            content = render_content_strategy_bundle(run.measurement)
        content_hash = sha256(content).hexdigest()
        artifact = MeasurementArtifact(
            run_id=run.run_id,
            kind=kind,
            content_hash=content_hash,
            media_type=self.media_type,
            size=len(content),
            storage_key=f"{owner.key}/{run.run_id}/{kind.value}/{content_hash}.zip",
        )
        self.storage.put(artifact.storage_key, content)
        return self.repository.persist_companion(artifact, owner, revision)

    def read_text_entry(
        self,
        run_id: str,
        artifact_id: str,
        owner: OwnerIdentity,
        entry: str,
    ) -> tuple[MeasurementArtifact, str]:
        if not re.fullmatch(
            r"(?:manifest\.(?:json|md)|measurement\.json|scores\.json|"
            r"recommendations\.json|recommendation-review\.json|"
            r"evidence-assessment\.json|content-strategy\.json|"
            r"recommendations/rec-[1-3]\.md)",
            entry,
        ):
            raise NotFound("Export entry is not available for agent reading")
        artifact, content = self.download(run_id, artifact_id, owner)
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                if entry not in archive.namelist():
                    raise NotFound("Export entry not found")
                data = archive.read(entry)
        except zipfile.BadZipFile as error:
            raise Conflict("Stored artifact is not a valid ZIP archive") from error
        try:
            return artifact, data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise Conflict("Export entry is not UTF-8 text") from error