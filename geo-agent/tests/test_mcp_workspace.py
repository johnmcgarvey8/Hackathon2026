import json
from pathlib import Path

import pytest

from verify_vscode_mcp_e2e import temporary_data_directory


WORKSPACE = Path(__file__).resolve().parents[2]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_workspace_mcp_configuration_matches_synthetic_contract():
    config = load_json(WORKSPACE / ".vscode" / "mcp.json")

    assert set(config["servers"]) == {"geoAgent"}
    server = config["servers"]["geoAgent"]
    assert server == {
        "type": "stdio",
        "command": "${workspaceFolder}\\geo-agent\\.venv\\Scripts\\python.exe",
        "args": ["-m", "geo_agent.mcp_server"],
        "cwd": "${workspaceFolder}",
        "env": {
            "GEO_DATA_DIR": "${workspaceFolder}\\geo-agent\\.data",
            "GEO_MEASUREMENT_POLICY": (
                "${workspaceFolder}\\geo-agent\\measurement-policy.json"
            ),
            "GEO_MCP_TENANT_ID": "local-development",
            "GEO_MCP_OWNER": "local-developer",
            "GEO_MCP_PRINCIPAL_ID": "local-agent",
            "GEO_HUMAN_BASE_URL": "http://127.0.0.1:8088",
            "PYTHONUNBUFFERED": "1",
        },
    }
    assert all("${input:" not in str(value) for value in server["env"].values())
    assert not {
        "GEO_API_TOKEN",
        "GEO_MCP_AGENT_TOKEN",
        "WEBIQ_API_KEY",
    }.intersection(server["env"])


def test_vscode_tasks_and_launch_profiles_share_the_mcp_synthetic_runtime():
    tasks = load_json(WORKSPACE / ".vscode" / "tasks.json")["tasks"]
    task_by_label = {task["label"]: task for task in tasks}
    required_tasks = {
        "GEO: Bootstrap Python Environment",
        "GEO: Run Synthetic Human API",
        "GEO: Run Mock Measurement Worker",
        "GEO: Start Synthetic MCP Prerequisites",
        "GEO: Verify MCP Workspace Setup",
        "GEO: Verify MCP End to End",
    }
    assert required_tasks <= set(task_by_label)

    api_environment = task_by_label["GEO: Run Synthetic Human API"]["options"]["env"]
    worker_environment = task_by_label["GEO: Run Mock Measurement Worker"]["options"]["env"]
    for name in ("GEO_DATA_DIR", "GEO_MEASUREMENT_POLICY"):
        assert api_environment[name] == worker_environment[name]

    launch = load_json(WORKSPACE / ".vscode" / "launch.json")
    configurations = {
        configuration["name"]: configuration
        for configuration in launch["configurations"]
    }
    assert {
        "GEO: Synthetic Human API",
        "GEO: Mock Measurement Worker",
    } <= set(configurations)
    for name in ("GEO_DATA_DIR", "GEO_MEASUREMENT_POLICY"):
        assert (
            configurations["GEO: Synthetic Human API"]["env"][name]
            == configurations["GEO: Mock Measurement Worker"]["env"][name]
        )
    compounds = {compound["name"]: compound for compound in launch["compounds"]}
    assert compounds["GEO: Synthetic MCP Prerequisites"]["configurations"] == [
        "GEO: Synthetic Human API",
        "GEO: Mock Measurement Worker",
    ]


def test_e2e_temporary_data_is_removed_when_the_verifier_fails():
    data_path = None

    with pytest.raises(RuntimeError, match="synthetic verifier failure"):
        with temporary_data_directory() as data_dir:
            data_path = Path(data_dir)
            (data_path / "partial-run.txt").write_text("partial", encoding="utf-8")
            raise RuntimeError("synthetic verifier failure")

    assert data_path is not None
    assert not data_path.exists()
