from pathlib import Path

from fastapi.testclient import TestClient

from geo_agent.api import create_app
from geo_agent.execution_policy import MeasurementExecutionPolicy


TOKEN = "p" * 40


def test_projects_are_default_and_legacy_measurements_is_disconnected(tmp_path):
    policy = MeasurementExecutionPolicy.model_validate_json(
        (Path(__file__).parents[1] / "measurement-live-demo-policy-v2.json").read_text(
            encoding="utf-8"
        )
    )
    app = create_app(
        tmp_path / "projects.sqlite3",
        {TOKEN: "local-developer"},
        measurement_policy=policy,
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}

    with TestClient(app, headers=headers) as client:
        root = client.get("/", follow_redirects=False)
        assert root.status_code == 307
        assert root.headers["location"] == "http://127.0.0.1:3000/projects"
        assert client.get("/measurements").status_code == 404

        project = client.post("/api/v2/projects", json={
            "name": "Microsoft Clarity",
            "primary_domain": "clarity.microsoft.com",
            "default_locale": "en-GB",
            "active_goal": "Compare behavioural analytics tools",
            "colour": "#0067b8",
        })
        assert project.status_code == 201
        project_id = project.json()["project_id"]

        measurement = client.post(
            f"/api/v2/projects/{project_id}/measurements",
            json={
                "url": "https://clarity.microsoft.com/",
                "audience": "Digital marketers and product teams",
                "goal": "Compare behavioural analytics tools",
                "locale": "en-GB",
            },
        )
        assert measurement.status_code == 202
        payload = measurement.json()
        assert payload["run"]["project_id"] == project_id
        assert payload["run"]["state"] == "preparing"
        assert payload["job"]["job_type"] == "prepare"
