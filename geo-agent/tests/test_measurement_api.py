import io
import json
import sqlite3
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from geo_agent.api import create_app
from geo_agent.artifact_storage import LocalArtifactStorage
from geo_agent.evaluation_workflow import EvaluationHandler, EvaluationRequest
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.measurement_api import OperatorPrincipal, create_measurement_router
from geo_agent.measurement_workflow import (
    MeasurementCoordinator, MeasurementEvent, MeasurementState, OwnerIdentity,
)
from geo_agent.jobs import JobService, JobType
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.workflow import Conflict, NotFound
from geo_agent.worker import Worker
from test_evaluation_workflow import FakeEvaluator, FakeSearch
from test_artifacts import measurement_result, recommendation_report
from test_measurement_workflow import inputs


pytestmark = pytest.mark.skip(
    reason="The unscoped v2 measurement API is intentionally disconnected; use /api/v2/projects."
)


ALICE_TOKEN = "a" * 40
BOB_TOKEN = "b" * 40


def policy(profiles=None):
    return MeasurementExecutionPolicy(
        policy_id="mock-v2-api",
        allowed_domains=("example.com",),
        locale="en-GB",
        profiles=profiles or inputs().profiles,
    )


@pytest.fixture
def client(tmp_path):
    app = create_app(
        tmp_path / "api-v2.sqlite3",
        {ALICE_TOKEN: "alice", BOB_TOKEN: "bob"},
        measurement_policy=policy(),
    )
    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as test_client:
        yield test_client


def test_v2_brief_and_preparation_job_are_owner_scoped_and_idempotent(client):
    brief = inputs().brief.model_dump(mode="json")
    created = client.post("/api/v2/briefs", json=brief)
    assert created.status_code == 201
    run = created.json()
    assert run["state"] == "draft"
    assert "owner" not in run
    policy_response = client.get("/api/v2/policy")
    assert policy_response.status_code == 200
    assert policy_response.json()["execution_mode"] == "mock"
    assert "endpoint" not in str(policy_response.json())
    assert "deployment" not in str(policy_response.json())
    assert [item["run_id"] for item in client.get("/api/v2/runs").json()] == [run["run_id"]]
    rejected = client.post("/api/v2/briefs", json={**brief, "url": "https://contoso.com/page"})
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "Brief host must match an allowed domain: example.com"

    route = f"/api/v2/runs/{run['run_id']}"
    assert client.post(f"{route}/exports", json={"expected_revision": 1}).status_code == 409
    request = {
        "expected_revision": 1,
        "idempotency_key": "prepare-1",
        "confirm_preparation_calls": True,
    }
    queued = client.post(f"{route}/prepare", json=request)
    assert queued.status_code == 202
    payload = queued.json()
    assert payload["run"]["state"] == "preparing"
    assert payload["job"]["state"] == "queued"
    assert "owner" not in payload["job"]
    assert "request" not in payload["job"]
    assert client.post(f"{route}/prepare", json=request).json()["job"] == payload["job"]
    assert client.get(f"/api/v2/jobs/{payload['job']['job_id']}").json() == payload["job"]
    assert client.get(f"{route}/jobs").json() == [payload["job"]]

    bob_headers = {"Authorization": f"Bearer {BOB_TOKEN}"}
    assert client.get("/api/v2/runs", headers=bob_headers).json() == []
    assert client.get(route, headers=bob_headers).status_code == 404
    assert client.get(f"/api/v2/jobs/{payload['job']['job_id']}", headers=bob_headers).status_code == 404


def test_v2_approval_and_start_require_exact_revision_hash_and_confirmation(tmp_path):
    database = tmp_path / "approved-v2.sqlite3"
    execution_policy = policy()
    repository = SQLiteMeasurementRepository(database)
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    created = repository.create(owner, prepared)
    app = create_app(database, {ALICE_TOKEN: "alice"}, measurement_policy=execution_policy)

    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
        route = f"/api/v2/runs/{created.run_id}"
        denied = client.post(f"{route}/start", json={
            "expected_revision": 1,
            "idempotency_key": "evaluate-1",
            "confirm_evaluation_calls": True,
        })
        assert denied.status_code == 409
        approved = client.post(f"{route}/query-approval", json={
            "expected_revision": 1,
            "input_hash": prepared.approval_hash,
        })
        assert approved.status_code == 200
        assert "actor" not in approved.json()["approval"]
        unconfirmed = client.post(f"{route}/start", json={
            "expected_revision": 2,
            "idempotency_key": "evaluate-1",
            "confirm_evaluation_calls": False,
        })
        assert unconfirmed.status_code == 409
        queued = client.post(f"{route}/start", json={
            "expected_revision": 2,
            "idempotency_key": "evaluate-1",
            "confirm_evaluation_calls": True,
            "include_recommendations": True,
        })
        assert queued.status_code == 202
        assert queued.json()["run"]["state"] == "queued"
        assert queued.json()["job"]["job_type"] == "evaluate"


def test_v2_query_revision_rebinds_approval_hash_and_preserves_server_inputs(tmp_path):
    database = tmp_path / "revised-v2.sqlite3"
    execution_policy = policy()
    repository = SQLiteMeasurementRepository(database)
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id,
        owner,
        created.revision,
        prepared.approval_hash,
    )
    app = create_app(database, {ALICE_TOKEN: "alice"}, measurement_policy=execution_policy)
    changed_queries = [query.model_dump(mode="json") for query in prepared.query_plan.queries]
    changed_queries[0]["chat_query"] = "Which option best supports a revised family visit?"

    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
        route = f"/api/v2/runs/{created.run_id}"
        revised = client.put(f"{route}/queries", json={
            "expected_revision": approved.revision,
            "queries": changed_queries,
        })
        assert revised.status_code == 200
        payload = revised.json()
        assert payload["revision"] == approved.revision + 1
        assert payload["state"] == "awaiting-query-approval"
        assert payload["approval"] is None
        assert payload["approval_hash"] != prepared.approval_hash
        assert payload["inputs"]["query_plan"]["queries"][0]["chat_query"] == changed_queries[0]["chat_query"]
        assert payload["inputs"]["snapshot"] == prepared.snapshot.model_dump(mode="json")
        assert payload["inputs"]["profiles"] == [
            profile.model_dump(mode="json") for profile in prepared.profiles
        ]
        assert payload["events"][-1]["event_type"] == "inputs-revised"

        unsupported = [dict(query) for query in changed_queries]
        unsupported[0] = {
            **unsupported[0],
            "evidence": [{"evidence_id": "page-1", "quote": "Fabricated evidence"}],
        }
        rejected = client.put(f"{route}/queries", json={
            "expected_revision": payload["revision"],
            "queries": unsupported,
        })
        assert rejected.status_code == 422
        assert repository.get(created.run_id, owner).revision == payload["revision"]


def test_v2_router_requires_operator_role(tmp_path):
    repository = SQLiteMeasurementRepository(tmp_path / "role-v2.sqlite3")

    def authenticate_without_role() -> OperatorPrincipal:
        return OperatorPrincipal(tenant_id="tenant-a", object_id="user-a")

    app = FastAPI()
    app.include_router(create_measurement_router(
        repository,
        policy(),
        authenticate_without_role,
        LocalArtifactStorage(tmp_path / "role-artifacts"),
    ))

    @app.exception_handler(Conflict)
    async def conflict_handler(_request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(NotFound)
    async def missing_handler(_request, _error):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    with TestClient(app) as client:
        response = client.post("/api/v2/briefs", json=inputs().brief.model_dump(mode="json"))
    assert response.status_code == 403
    assert response.json()["detail"] == "Geo.Operator role required"


def test_v2_recommendation_review_is_owner_scoped_and_exports_accepted_task(tmp_path):
    database = tmp_path / "review-v2.sqlite3"
    measurement = measurement_result()
    recommendations = recommendation_report(measurement)
    execution_policy = policy(measurement.inputs.profiles)
    repository = SQLiteMeasurementRepository(database)
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    created = repository.create(owner, measurement.inputs)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id,
        owner,
        created.revision,
        measurement.inputs.approval_hash,
    )
    ready = repository.mutate(
        approved.run_id,
        owner,
        approved.revision,
        lambda run: run.model_copy(update={
            "measurement": measurement,
            "recommendations": recommendations,
            "state": MeasurementState.READY,
            "events": (*run.events, MeasurementEvent(
                sequence=len(run.events) + 1,
                event_type="ready",
            )),
        }),
    )
    app = create_app(
        database,
        {ALICE_TOKEN: "alice", BOB_TOKEN: "bob"},
        measurement_policy=execution_policy,
    )

    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
        route = f"/api/v2/runs/{ready.run_id}"
        rejected = client.post(f"{route}/recommendation-review", json={
            "expected_revision": ready.revision,
            "decisions": [{"task_id": "rec-2", "decision": "accepted"}],
        })
        assert rejected.status_code == 409

        bob_headers = {"Authorization": f"Bearer {BOB_TOKEN}"}
        assert client.post(f"{route}/recommendation-review", headers=bob_headers, json={
            "expected_revision": ready.revision,
            "decisions": [{"task_id": "rec-1", "decision": "accepted"}],
        }).status_code == 404

        reviewed = client.post(f"{route}/recommendation-review", json={
            "expected_revision": ready.revision,
            "decisions": [{"task_id": "rec-1", "decision": "accepted"}],
        })
        assert reviewed.status_code == 200
        payload = reviewed.json()
        assert payload["state"] == "ready"
        assert payload["recommendation_review"]["decisions"] == [
            {"task_id": "rec-1", "decision": "accepted"}
        ]
        assert "actor" not in payload["recommendation_review"]
        assert payload["events"][-1]["event_type"] == "recommendations-reviewed"

        exported = client.post(f"{route}/exports", json={"expected_revision": payload["revision"]})
        download = client.get(exported.json()["download_url"])
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            assert "recommendations/rec-1.md" in archive.namelist()
            assert json.loads(archive.read("recommendation-review.json"))["decisions"][0]["decision"] == "accepted"


def test_v2_export_is_durable_idempotent_and_owner_scoped(tmp_path):
    database = tmp_path / "export-v2.sqlite3"
    execution_policy = policy()
    repository = SQLiteMeasurementRepository(database)
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    created = repository.create(owner, prepared)
    approved = MeasurementCoordinator(repository).approve(
        created.run_id,
        owner,
        created.revision,
        prepared.approval_hash,
    )
    JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "evaluate-export",
        EvaluationRequest(confirm_evaluation_calls=True),
    )
    result = Worker(
        repository,
        "worker-a",
        {JobType.EVALUATE: EvaluationHandler(
            repository,
            execution_policy,
            FakeSearch(),
            (FakeEvaluator(prepared.profiles[0]),),
        )},
    ).run_once()
    assert result is not None
    _, completed = result
    app = create_app(
        database,
        {ALICE_TOKEN: "alice", BOB_TOKEN: "bob"},
        measurement_policy=execution_policy,
    )

    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as client:
        route = f"/api/v2/runs/{completed.run_id}"
        body = {"expected_revision": completed.revision}
        exported = client.post(f"{route}/exports", json=body)
        assert exported.status_code == 201
        payload = exported.json()
        assert payload["run"]["state"] == "exported"
        assert payload["run"]["events"][-1]["event_type"] == "exported"
        assert "storage_key" not in payload["artifact"]
        assert client.get(f"{route}/artifacts").json() == [payload["artifact"]]
        download = client.get(payload["download_url"])
        assert download.status_code == 200
        assert download.headers["cache-control"] == "private, no-store"
        assert download.headers["etag"] == f'"{payload["artifact"]["content_hash"]}"'
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["input_hash"] == prepared.approval_hash
            assert manifest["publish_permission"] is False

        replay = client.post(f"{route}/exports", json=body)
        assert replay.status_code == 201
        assert replay.json() == payload
        bob_headers = {"Authorization": f"Bearer {BOB_TOKEN}"}
        assert client.get(payload["download_url"], headers=bob_headers).status_code == 404

    with sqlite3.connect(database) as connection:
        artifact_count = connection.execute(
            "SELECT COUNT(*) FROM artifacts WHERE run_id = ?",
            (completed.run_id,),
        ).fetchone()[0]
    assert artifact_count == 1

    restarted_app = create_app(
        database,
        {ALICE_TOKEN: "alice"},
        measurement_policy=execution_policy,
    )
    with TestClient(restarted_app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as restarted:
        restored = restarted.get(payload["download_url"])
        assert restored.status_code == 200
        assert restored.content == download.content

        artifact = repository.get_run_artifact(completed.run_id, owner)
        artifact_path = tmp_path / "measurement-artifacts" / artifact.storage_key
        artifact_path.write_bytes(b"tampered")
        corrupted = restarted.get(payload["download_url"])
        assert corrupted.status_code == 409
        assert corrupted.json()["detail"] == "Stored artifact failed its integrity check"