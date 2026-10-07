import asyncio
from contextlib import contextmanager
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from mcp import Client, StdioServerParameters


WORKSPACE = Path(__file__).resolve().parents[2]
CONFIG_PATH = WORKSPACE / ".vscode" / "mcp.json"
HUMAN_TOKEN = "h" * 40


@contextmanager
def temporary_data_directory():
    path = Path(tempfile.mkdtemp(prefix="geo-vscode-mcp-e2e-"))
    try:
        yield str(path)
    finally:
        for _ in range(50):
            if not path.exists():
                break
            try:
                shutil.rmtree(path)
                break
            except PermissionError:
                time.sleep(0.1)
        else:
            raise RuntimeError(f"Could not remove temporary data directory {path}")


def expand_workspace(value: str) -> str:
    return value.replace("${workspaceFolder}", str(WORKSPACE))


def free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


async def call_tool(client: Client, name: str, arguments: dict) -> dict:
    result = await client.call_tool(name, arguments)
    if result.is_error is True or result.structured_content is None:
        raise RuntimeError(f"{name} failed: {result.content}")
    return result.structured_content


async def wait_for_api(base_url: str) -> None:
    async with httpx.AsyncClient() as client:
        for _ in range(200):
            try:
                response = await client.get(f"{base_url}/health", timeout=0.2)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
    raise RuntimeError("Synthetic human API did not start")


async def wait_for_worker(process: subprocess.Popen, log_path: Path) -> None:
    for _ in range(200):
        if process.poll() is not None:
            raise RuntimeError(
                "Mock worker exited during startup:\n"
                + log_path.read_text(encoding="utf-8", errors="replace")
            )
        if (
            log_path.is_file()
            and "Mock measurement worker ready"
            in log_path.read_text(encoding="utf-8", errors="replace")
        ):
            return
        await asyncio.sleep(0.05)
    raise RuntimeError("Mock worker did not report readiness")


async def wait_for_state(
    client: Client,
    run_id: str,
    state: str,
    worker: subprocess.Popen,
    worker_log: Path,
) -> dict:
    last_status = "unknown"
    for _ in range(300):
        if worker.poll() is not None:
            raise RuntimeError(
                "Mock worker exited before completing the run:\n"
                + worker_log.read_text(encoding="utf-8", errors="replace")
            )
        run = await call_tool(client, "geo_get_run", {"run_id": run_id})
        last_status = run["status"]
        if run["status"] == state:
            return run
        if run["status"] in {"failed", "cancelled", "needs-review"}:
            raise RuntimeError(f"Run entered terminal state {run['status']}")
        await asyncio.sleep(0.1)
    raise RuntimeError(
        f"Run remained {last_status} instead of reaching {state}.\n"
        + worker_log.read_text(encoding="utf-8", errors="replace")
    )


async def exercise(
    parameters: StdioServerParameters,
    base_url: str,
    database: Path,
    worker: subprocess.Popen,
    worker_log: Path,
) -> dict:
    headers = {"Authorization": f"Bearer {HUMAN_TOKEN}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers) as human:
        async with Client(parameters) as client:
            created = await call_tool(client, "geo_create_run", {
                "url": "https://example.com/mock-page",
                "audience": "Research buyers",
                "goal": "Compare trusted options",
                "locale": "en-GB",
                "idempotency_key": "vscode-e2e-create",
            })
            run_id = created["run_id"]

            preparation_authorization = await human.post(
                f"/api/v2/runs/{run_id}/agent-execution-authorizations",
                json={
                    "expected_revision": created["run_revision"],
                    "agent_principal_id": "local-agent",
                    "stage": "prepare",
                    "input_hash": None,
                    "lifetime_seconds": 900,
                },
            )
            preparation_authorization.raise_for_status()
            await call_tool(client, "geo_prepare_run", {
                "run_id": run_id,
                "expected_revision": created["run_revision"],
                "idempotency_key": "vscode-e2e-prepare",
                "execution_authorization_id": (
                    preparation_authorization.json()["authorization_id"]
                ),
            })

            prepared = await wait_for_state(
                client,
                run_id,
                "awaiting-query-approval",
                worker,
                worker_log,
            )
            query_plan = await call_tool(
                client,
                "geo_get_query_plan",
                {"run_id": run_id},
            )
            if len(query_plan["data"]["queries"]) != 5:
                raise RuntimeError("Synthetic preparation did not produce five queries")

            approval = await human.post(
                f"/api/v2/runs/{run_id}/query-approval",
                json={
                    "expected_revision": prepared["run_revision"],
                    "input_hash": prepared["data"]["approval_hash"],
                },
            )
            approval.raise_for_status()
            approved = approval.json()

            evaluation_authorization = await human.post(
                f"/api/v2/runs/{run_id}/agent-execution-authorizations",
                json={
                    "expected_revision": approved["revision"],
                    "agent_principal_id": "local-agent",
                    "stage": "evaluate",
                    "input_hash": approved["approval_hash"],
                    "lifetime_seconds": 900,
                },
            )
            evaluation_authorization.raise_for_status()
            await call_tool(client, "geo_start_measurement", {
                "run_id": run_id,
                "expected_revision": approved["revision"],
                "input_hash": approved["approval_hash"],
                "idempotency_key": "vscode-e2e-evaluate",
                "execution_authorization_id": (
                    evaluation_authorization.json()["authorization_id"]
                ),
            })

            completed = await wait_for_state(
                client,
                run_id,
                "ready",
                worker,
                worker_log,
            )
            results = await call_tool(
                client,
                "geo_get_results",
                {"run_id": run_id, "detail": "summary"},
            )
            if (
                len(results["data"]["retrievals"]) != 5
                or len(results["data"]["outcomes"]) != 15
            ):
                raise RuntimeError("Synthetic measurement result counts are incomplete")
            await call_tool(client, "geo_get_evidence", {
                "run_id": run_id,
                "query_id": "q-1",
                "evidence_id": "q-1-target",
            })

            exported = await call_tool(client, "geo_create_export", {
                "run_id": run_id,
                "kind": "measurement",
                "expected_revision": completed["run_revision"],
                "idempotency_key": "vscode-e2e-export",
            })
            artifact_id = exported["data"]["artifact"]["artifact_id"]
            manifest = await call_tool(client, "geo_read_export", {
                "run_id": run_id,
                "artifact_id": artifact_id,
                "entry": "manifest.json",
            })
            if json.loads(manifest["data"]["content"])["publish_permission"] is not False:
                raise RuntimeError("Synthetic export unexpectedly permits publishing")

        async with Client(parameters) as restarted:
            persisted = await call_tool(
                restarted,
                "geo_get_run",
                {"run_id": run_id},
            )
            if persisted["status"] not in {"ready", "exported"}:
                raise RuntimeError(
                    "Run did not survive MCP process restart: "
                    f"{persisted['status']}"
                )

    connection = sqlite3.connect(database)
    try:
        operation_count = connection.execute(
            "SELECT COUNT(*) FROM operation_claims"
        ).fetchone()[0]
    finally:
        connection.close()
    if operation_count != 23:
        raise RuntimeError(f"Expected 23 provider operations, found {operation_count}")

    return {
        "run_id": run_id,
        "final_state": persisted["status"],
        "queries": 5,
        "profile_outcomes": 15,
        "provider_operations": operation_count,
        "export_manifest": "ok",
        "restart": "ok",
    }


async def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    server = config["servers"]["geoAgent"]
    command = expand_workspace(server["command"])
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"

    with temporary_data_directory() as data_dir:
        api_log_path = Path(data_dir) / "api.log"
        worker_log_path = Path(data_dir) / "worker.log"
        common_environment = os.environ.copy()
        common_environment.update({
            name: expand_workspace(str(value))
            for name, value in server["env"].items()
        })
        common_environment.update({
            "GEO_DATA_DIR": data_dir,
            "GEO_HUMAN_BASE_URL": base_url,
        })
        api_environment = {
            **common_environment,
            "GEO_API_TOKEN": HUMAN_TOKEN,
            "GEO_PORT": str(port),
        }
        worker_environment = {
            **common_environment,
            "GEO_MEASUREMENT_WORKER_POLL_SECONDS": "0.1",
        }

        with (
            api_log_path.open("w", encoding="utf-8") as api_log,
            worker_log_path.open("w", encoding="utf-8") as worker_log,
        ):
            api = subprocess.Popen(
                [command, "-m", "geo_agent"],
                cwd=WORKSPACE,
                env=api_environment,
                stdout=api_log,
                stderr=subprocess.STDOUT,
            )
            worker = subprocess.Popen(
                [command, "-m", "geo_agent.mock_measurement_worker"],
                cwd=WORKSPACE,
                env=worker_environment,
                stdout=worker_log,
                stderr=subprocess.STDOUT,
            )
            try:
                await wait_for_api(base_url)
                await wait_for_worker(worker, worker_log_path)
                parameters = StdioServerParameters(
                    command=command,
                    args=[expand_workspace(str(value)) for value in server["args"]],
                    env=common_environment,
                    cwd=expand_workspace(server["cwd"]),
                )
                result = await exercise(
                    parameters,
                    base_url,
                    Path(data_dir) / "runs.sqlite3",
                    worker,
                    worker_log_path,
                )
                print(json.dumps(result, sort_keys=True))
            except Exception:
                api_log.flush()
                worker_log.flush()
                print(
                    "Synthetic API log:\n"
                    + api_log_path.read_text(encoding="utf-8", errors="replace"),
                    file=sys.stderr,
                )
                print(
                    "Mock worker log:\n"
                    + worker_log_path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    ),
                    file=sys.stderr,
                )
                raise
            finally:
                for process in (worker, api):
                    if process.poll() is None:
                        process.terminate()
                for process in (worker, api):
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                await asyncio.sleep(0.2)


if __name__ == "__main__":
    asyncio.run(main())
