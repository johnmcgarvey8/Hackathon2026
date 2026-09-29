import base64
import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

from geo_agent.artifact_storage import MeasurementArtifact
from geo_agent.evaluation import measurement_scores
from geo_agent.jobs import WorkflowJob
from geo_agent.measurement_workflow import MeasurementRun, MeasurementRunSummary
from geo_agent.workflow import Conflict


class CursorCodec:
    def __init__(self, secret: bytes):
        if len(secret) < 32:
            raise ValueError("Cursor signing secret must contain at least 32 bytes")
        self._secret = secret

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        try:
            return base64.urlsafe_b64decode(value + padding)
        except (ValueError, TypeError) as error:
            raise Conflict("Cursor is invalid") from error

    def encode(self, kind: str, owner_key: str, data: dict[str, Any]) -> str:
        payload = json.dumps(
            {"kind": kind, "owner": owner_key, "data": data},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return f"{self._encode(payload)}.{self._encode(signature)}"

    def decode(self, cursor: str, kind: str, owner_key: str) -> dict[str, Any]:
        try:
            payload_part, signature_part = cursor.split(".", 1)
        except ValueError as error:
            raise Conflict("Cursor is invalid") from error
        payload = self._decode(payload_part)
        signature = self._decode(signature_part)
        expected = hmac.new(self._secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise Conflict("Cursor is invalid")
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise Conflict("Cursor is invalid") from error
        if value.get("kind") != kind or value.get("owner") != owner_key:
            raise Conflict("Cursor does not belong to this request")
        data = value.get("data")
        if not isinstance(data, dict):
            raise Conflict("Cursor is invalid")
        return data


def run_summary_view(summary: MeasurementRunSummary, human_url: str) -> dict[str, Any]:
    return {
        **summary.model_dump(mode="json"),
        "human_url": human_url,
    }


def run_view(run: MeasurementRun) -> dict[str, Any]:
    payload = run.model_dump(mode="json", exclude={"owner"})
    if payload["approval"] is not None:
        payload["approval"].pop("actor", None)
    if payload["recommendation_review"] is not None:
        payload["recommendation_review"].pop("actor", None)
    payload["approval_hash"] = run.inputs.approval_hash if run.inputs is not None else None
    payload["scores"] = measurement_scores(run.measurement) if run.measurement is not None else None
    return payload


def job_view(job: WorkflowJob) -> dict[str, Any]:
    return job.model_dump(
        mode="json",
        exclude={
            "owner",
            "request",
            "lease_holder",
            "lease_token",
            "idempotency_key",
            "execution_principal_id",
            "execution_authorization_id",
        },
    )


def artifact_view(artifact: MeasurementArtifact) -> dict[str, Any]:
    return artifact.model_dump(mode="json", exclude={"storage_key"})


def parse_cursor_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise Conflict("Cursor is invalid")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise Conflict("Cursor is invalid") from error
