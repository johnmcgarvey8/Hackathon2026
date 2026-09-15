import io
import json
import zipfile

import pytest
import yaml
from fastapi.testclient import TestClient

from geo_agent.api import create_app
from geo_agent.artifacts import RunManifest


ALICE_TOKEN = "a" * 40
BOB_TOKEN = "b" * 40


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "runs.sqlite3", {ALICE_TOKEN: "alice", BOB_TOKEN: "bob"})
    with TestClient(app, headers={"Authorization": f"Bearer {ALICE_TOKEN}"}) as test_client:
        yield test_client


def test_authentication_is_required(client):
    assert client.post("/fixture-runs", headers={"Authorization": ""}).status_code == 401
    assert client.post("/fixture-runs", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/health").json()["live_ready"] is False


def test_api_approval_to_deterministic_export(client):
    created = client.post("/fixture-runs")
    assert created.status_code == 201
    run = created.json()
    route = f"/runs/{run['run_id']}"
    revision = {"expected_revision": 1}
    start = {**revision, "idempotency_key": "test-start"}
    assert client.post(f"{route}/start", json=start).status_code == 409
    assert client.post(f"{route}/exports", json=revision).status_code == 409
    assert client.post(f"{route}/query-approval", json={**revision, "input_hash": run["approval_hash"]}).status_code == 200
    completed = client.post(f"{route}/start", json=start)
    assert completed.status_code == 200
    result = completed.json()
    assert result["scores"]["overall"]["score"] == 40
    assert result["inputs"]["snapshot"]["provenance"] == "synthetic"
    assert client.post(f"{route}/start", json=start).json() == result
    evidence_id = result["results"][0]["sources"][0]["evidence_id"]
    assert client.get(f"{route}/evidence/{evidence_id}").status_code == 200
    exported = client.post(f"{route}/exports", json=revision).json()
    bundle = client.get(exported["download_url"])
    assert bundle.status_code == 200
    assert bundle.content == client.get(exported["download_url"]).content
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert sorted(archive.namelist()) == ["manifest.md", "run.json", "scores.json"]
        markdown = archive.read("manifest.md").decode()
        frontmatter = yaml.safe_load(markdown.split("---\n", 2)[1])
        manifest = RunManifest.model_validate(frontmatter)
        assert manifest.requires_human_approval is True
        assert manifest.publish_permission is False
        assert manifest.tasks == ()
        payload = json.loads(archive.read("run.json"))
        evidence = {source["evidence_id"] for answer in payload["results"] for source in answer["sources"]}
        assert set(manifest.evidence_ids) == evidence
        assert set(manifest.files) == set(archive.namelist())
        assert ALICE_TOKEN.encode() not in bundle.content
        assert "owner" not in payload
    assert client.post(f"{route}/start", json=start).json()["state"] == "exported"


def test_live_failure_never_creates_fixture(client):
    response = client.post("/briefs", json={"url": "https://example.org", "audience": "Buyers", "goal": "Compare", "locale": "en-GB", "idempotency_key": "test"})
    assert response.status_code == 503
    assert "no synthetic fallback" in response.json()["detail"]


def test_query_revision_invalidates_approval_and_rejects_duplicates(client):
    run = client.post("/fixture-runs").json()
    route = f"/runs/{run['run_id']}"
    client.post(f"{route}/query-approval", json={"expected_revision": 1, "input_hash": run["approval_hash"]})
    queries = run["inputs"]["queries"]
    queries[0]["text"] = "A revised buyer question?"
    revised = client.put(f"{route}/queries", json={"expected_revision": 1, "queries": queries}).json()
    assert revised["revision"] == 2
    assert revised["approval"] is None
    assert client.post(f"{route}/start", json={"expected_revision": 2, "idempotency_key": "new"}).status_code == 409
    queries[1]["text"] = queries[0]["text"]
    assert client.put(f"{route}/queries", json={"expected_revision": 2, "queries": queries}).status_code == 422


@pytest.mark.parametrize("method,suffix,body", [
    ("GET", "", None),
    ("GET", "/events", None),
    ("GET", "/evidence/any", None),
    ("GET", "/artifacts/bundle", None),
    ("POST", "/query-approval", {"expected_revision": 1, "input_hash": "a" * 64}),
    ("POST", "/start", {"expected_revision": 1, "idempotency_key": "start"}),
    ("POST", "/cancel", {"expected_revision": 1}),
    ("POST", "/exports", {"expected_revision": 1}),
])
def test_cross_user_routes(client, method, suffix, body):
    run = client.post("/fixture-runs").json()
    response = client.request(method, f"/runs/{run['run_id']}{suffix}", json=body, headers={"Authorization": f"Bearer {BOB_TOKEN}"})
    assert response.status_code == 404


def test_weak_token_configuration_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="32-character"):
        create_app(tmp_path / "runs.sqlite3", {"weak": "alice"})