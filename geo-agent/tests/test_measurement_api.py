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


def test_brand_definition_versions_are_separate_and_owner_scoped(client):
    definition = {"name": "Microsoft Clarity", "aliases": [{"text": "Clarity", "ambiguous": True}],
                  "domains": ["clarity.microsoft.com"]}
    created = client.post("/api/v2/briefs", json={**inputs().brief.model_dump(mode="json"), "brand_definition": definition})
    assert created.status_code == 201
    run = created.json()
    route = f"/api/v2/runs/{run['run_id']}"
    initial = client.get(f"{route}/evidence-assessment").json()
    assert initial["status"] == "pending-saved-measurement"
    assert initial["brand_definition"]["definition_version"] == 1
    assert "brand_definition" not in run["brief"]
    request = {"definition": definition, "expected_definition_version": 0}
    assert client.post(f"{route}/brand-definition", json=request).json()["definition_version"] == 1
    request["definition"] = {**definition, "name": "Clarity Analytics"}
    assert client.post(f"{route}/brand-definition", json=request).status_code == 409
    request["expected_definition_version"] = 1
    assert client.post(f"{route}/brand-definition", json=request).json()["definition_version"] == 2
    assert client.get(f"{route}/evidence-assessment?definition_version=1").json() == initial
    assert client.get(f"{route}/evidence-assessment?definition_version=99").status_code == 404
    assert client.get(route).json() == run
    bob = {"Authorization": f"Bearer {BOB_TOKEN}"}
    assert client.get(f"{route}/evidence-assessment", headers=bob).status_code == 404
    assert client.post(f"{route}/brand-definition", json=request, headers=bob).status_code == 404
    assert client.get(f"{route}/evidence-assessment", headers={"Authorization": ""}).status_code == 401


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
    progress = client.get(f"{route}/progress")
    assert progress.status_code == 200
    assert progress.json()["job_state"] == "queued"
    assert len(progress.json()["operations"]) == 3
    assert not {"owner", "request", "lease_holder", "idempotency_key"}.intersection(progress.json())

    bob_headers = {"Authorization": f"Bearer {BOB_TOKEN}"}
    assert client.get("/api/v2/runs", headers=bob_headers).json() == []
    assert client.get(route, headers=bob_headers).status_code == 404
    assert client.get(f"{route}/progress", headers=bob_headers).status_code == 404
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


def test_agent_execution_authorization_is_human_only_and_hash_bound(tmp_path):
    database = tmp_path / "agent-authorization.sqlite3"
    execution_policy = policy()
    repository = SQLiteMeasurementRepository(database)
    owner = OwnerIdentity(tenant_id="local-development", object_id="alice")
    prepared = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
    created = repository.create(owner, prepared)
    app = create_app(
        database,
        {ALICE_TOKEN: "alice", BOB_TOKEN: "bob"},
        measurement_policy=execution_policy,
        default_mcp_agent_principal_id="local-agent",
    )

    with TestClient(app, headers={"Authorization": "Bearer " + ALICE_TOKEN}) as client:
        route = f"/api/v2/runs/{created.run_id}"
        approved = client.post(f"{route}/query-approval", json={
            "expected_revision": created.revision,
            "input_hash": prepared.approval_hash,
        }).json()
        request = {
            "expected_revision": approved["revision"],
            "agent_principal_id": "local-agent",
            "stage": "evaluate",
            "input_hash": prepared.approval_hash,
            "lifetime_seconds": 900,
        }
        issued = client.post(
            f"{route}/agent-execution-authorizations",
            json=request,
        )
        assert issued.status_code == 201
        assert issued.json()["stage"] == "evaluate"
        assert issued.json()["operation_ceiling"] == 10
        assert "owner" not in issued.json()
        assert client.post(
            f"{route}/agent-execution-authorizations",
            json={**request, "input_hash": "0" * 64},
        ).status_code == 409
        assert client.post(
            f"{route}/agent-execution-authorizations",
            headers={"Authorization": "Bearer " + BOB_TOKEN},
            json=request,
        ).status_code == 404
        assert client.post(
            f"{route}/agent-execution-authorizations",
            headers={"Authorization": "Bearer " + "m" * 40},
            json=request,
        ).status_code == 401


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
        before = client.get(route).json()
        strategy = client.get(f"{route}/content-strategy")
        assert strategy.status_code == 200 and strategy.headers["Cache-Control"] == "private, no-store"
        report = strategy.json()["report"]
        assert report["schema_version"] == "geo-content-strategy/v1"
        strategy_download = client.get(f"{route}/content-strategy/download?measurement_hash={report['measurement_hash']}")
        assert strategy_download.status_code == 200
        with zipfile.ZipFile(io.BytesIO(strategy_download.content)) as archive:
            assert json.loads(archive.read("content-strategy.json")) == report
            assert json.loads(archive.read("measurement.json")) == measurement.model_dump(mode="json")
        assert client.get(f"{route}/content-strategy/download?measurement_hash={'b' * 64}").status_code == 409
        assert client.get(f"{route}/content-strategy", headers={"Authorization": f"Bearer {BOB_TOKEN}"}).status_code == 404
        assert client.get(f"{route}/content-strategy/download", headers={"Authorization": f"Bearer {BOB_TOKEN}"}).status_code == 404
        assert client.get(f"{route}/content-strategy", headers={"Authorization": ""}).status_code == 401
        assert client.get(route).json() == before
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


@pytest.mark.parametrize("assessed_export", [False, True])
def test_v2_export_is_durable_idempotent_and_owner_scoped(tmp_path, assessed_export):
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
        definition = {"name": "Microsoft Clarity", "aliases": [{"text": "Clarity", "ambiguous": True}],
                      "domains": ["clarity.microsoft.com"]}
        original_run = client.get(route).json()
        unconfigured = client.get(f"{route}/evidence-assessment").json()
        assert unconfigured["status"] == "unconfigured"
        assert unconfigured["assessment"]["answer"]["brand_presence"]["rate"] is None
        if assessed_export:
            record = client.post(f"{route}/brand-definition", json={"definition": definition, "expected_definition_version": 0}).json()
            body.update(definition_version=1, definition_hash=record["definition_hash"])
            assert client.post(f"{route}/exports", json={**body, "definition_hash": "0" * 64}).status_code == 409
        assert client.get(route).json() == original_run
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
            assert manifest["schema_version"] == ("geo-measurement-manifest/v2" if assessed_export else "geo-measurement-manifest/v1")
            if assessed_export:
                report = json.loads(archive.read("evidence-assessment.json"))
                assert report["input_hash"] == prepared.approval_hash

        replay = client.post(f"{route}/exports", json=body)
        assert replay.status_code == 201
        assert replay.json() == payload
        bob_headers = {"Authorization": f"Bearer {BOB_TOKEN}"}
        assert client.get(payload["download_url"], headers=bob_headers).status_code == 404
        saved_state = client.get(route).json()
        record = client.post(f"{route}/brand-definition", json={
            "definition": {**definition, "name": "Clarity Analytics"}, "expected_definition_version": 1 if assessed_export else 0,
        }).json()
        assessment = client.get(f"{route}/evidence-assessment").json()
        assert assessment["status"] == "ready"
        version = record["definition_version"]
        companion_path = f"{route}/evidence-assessment/download?definition_version={version}"
        companion = client.get(companion_path)
        assert companion.status_code == 200
        assert companion.content == client.get(companion_path).content
        assert companion.headers["cache-control"] == "private, no-store"
        assert client.get(companion_path, headers=bob_headers).status_code == 404
        assert client.get(companion_path + "&measurement_hash=" + "0" * 64).status_code == 409
        with zipfile.ZipFile(io.BytesIO(companion.content)) as archive:
            report = json.loads(archive.read("evidence-assessment.json"))
            manifest = json.loads(archive.read("manifest.json"))
            assert report == assessment["assessment"]
            assert manifest["assessment_hash"] == assessment["assessment_hash"]
            assert "owner" not in report
        assert client.get(route).json() == saved_state
        assert client.post(f"{route}/exports", json={**body, "definition_version": version,
                          "definition_hash": record["definition_hash"]}).json() == payload
        assert client.get(payload["download_url"]).content == download.content

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