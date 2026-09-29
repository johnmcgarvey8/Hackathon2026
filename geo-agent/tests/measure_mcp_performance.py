import argparse
import asyncio
import json
import math
import os
import socket
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from threading import Thread

import httpx
import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import insert
from uvicorn import Config, Server

from geo_agent.agent_access import AgentPrincipal
from geo_agent.api import create_app
from geo_agent.evaluation_workflow import EvaluationRequest
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import JobService, JobType
from geo_agent.measurement_workflow import (
    MeasurementEvent,
    MeasurementRun,
    MeasurementState,
    OwnerIdentity,
)
from geo_agent.persistence import SQLiteMeasurementRepository, measurement_runs
from test_artifacts import measurement_result


CALL_COUNT = 200
SAVED_RUN_COUNT = 1000
HUMAN_TOKEN = "p" * 40
AGENT_TOKEN = "q" * 40


def p95(values: list[float]) -> float:
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


def result_size(result) -> int:
    return len(
        json.dumps(
            result.structured_content,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def seed(database: Path, policy: MeasurementExecutionPolicy, owner: OwnerIdentity) -> str:
    repository = SQLiteMeasurementRepository(database)
    measurement = measurement_result()
    rows = []
    for _ in range(SAVED_RUN_COUNT):
        run = MeasurementRun(
            owner=owner,
            brief=measurement.inputs.brief,
            inputs=measurement.inputs,
            measurement=measurement,
            state=MeasurementState.READY,
            events=(MeasurementEvent(sequence=1, event_type="ready"),),
        )
        rows.append({
            "run_id": run.run_id,
            "owner_key": owner.key,
            **repository._run_values(run),
        })
    with repository.engine.begin() as connection:
        connection.execute(insert(measurement_runs), rows)
    active = repository.create(owner, inputs=measurement.inputs)
    from geo_agent.measurement_workflow import MeasurementCoordinator

    approved = MeasurementCoordinator(repository).approve(
        active.run_id,
        owner,
        active.revision,
        active.inputs.approval_hash,
    )
    JobService(repository).enqueue(
        approved.run_id,
        owner,
        approved.revision,
        JobType.EVALUATE,
        "performance-active-job",
        EvaluationRequest(confirm_evaluation_calls=True),
        policy_id=policy.policy_id,
        policy_hash=policy.policy_hash,
        operation_ceiling=5 + 5 * len(policy.profiles),
    )
    leased = repository.lease_one_job(
        "performance-slow-worker",
        policy_id=policy.policy_id,
        policy_hash=policy.policy_hash,
    )
    if leased is None:
        raise RuntimeError("Could not create the active performance fixture")
    repository.close()
    return active.run_id


async def exercise(client: Client, active_run_id: str, call_count: int) -> dict:
    tools = await client.list_tools()
    catalogue_bytes = sum(
        len(tool.model_dump_json().encode("utf-8"))
        for tool in tools.tools
    )
    durations = []
    sizes = []
    calls = (
        ("geo_get_capabilities", {}),
        ("geo_list_runs", {"page_size": 10}),
        ("geo_get_progress", {"run_id": active_run_id}),
    )
    for index in range(call_count):
        name, arguments = calls[index % len(calls)]
        started = time.perf_counter()
        result = await client.call_tool(name, arguments)
        durations.append(time.perf_counter() - started)
        if result.is_error:
            raise RuntimeError(f"{name} returned an error")
        sizes.append(result_size(result))
    return {
        "durations": durations,
        "sizes": sizes,
        "catalogue_bytes": catalogue_bytes,
    }


def aggregate(samples: list[dict]) -> dict:
    durations = [
        value
        for sample in samples
        for value in sample["durations"]
    ]
    sizes = [
        value
        for sample in samples
        for value in sample["sizes"]
    ]
    return {
        "clients": len(samples),
        "requests": len(durations),
        "p95_seconds": p95(durations),
        "max_seconds": max(durations),
        "max_payload_bytes": max(sizes),
        "catalogue_bytes": max(sample["catalogue_bytes"] for sample in samples),
    }


async def stdio_scenario(
    python: Path,
    data_dir: Path,
    policy_path: Path,
    active_run_id: str,
) -> dict:
    async def one_client() -> dict:
        parameters = StdioServerParameters(
            command=str(python),
            args=["-m", "geo_agent.mcp_server"],
            env={
                "GEO_DATA_DIR": str(data_dir),
                "GEO_MEASUREMENT_POLICY": str(policy_path),
                "GEO_MCP_TENANT_ID": "performance",
                "GEO_MCP_OWNER": "owner",
                "GEO_MCP_PRINCIPAL_ID": "performance-agent",
                "PYTHONUNBUFFERED": "1",
                "SYSTEMROOT": os.environ["SYSTEMROOT"],
                "PATH": os.environ["PATH"],
            },
        )
        async with Client(parameters) as client:
            return await exercise(client, active_run_id, CALL_COUNT // 5)

    return aggregate(list(await asyncio.gather(*(one_client() for _ in range(5)))))


def wait_for_server(base_url: str) -> None:
    for _ in range(200):
        try:
            if httpx.get(f"{base_url}/health", timeout=0.2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise RuntimeError("Performance HTTP server did not start")


async def http_scenario(base_url: str, active_run_id: str) -> dict:
    async def one_client() -> dict:
        async with httpx2.AsyncClient(
            headers={"Authorization": "Bearer " + AGENT_TOKEN},
        ) as http_client:
            transport = streamable_http_client(
                f"{base_url}/mcp",
                http_client=http_client,
            )
            async with Client(transport) as client:
                return await exercise(client, active_run_id, CALL_COUNT // 5)

    return aggregate(list(await asyncio.gather(*(one_client() for _ in range(5)))))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    policy_path = root / "measurement-policy.json"
    policy = MeasurementExecutionPolicy.model_validate_json(
        policy_path.read_text(encoding="utf-8")
    )
    owner = OwnerIdentity(tenant_id="performance", object_id="owner")
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="geo-mcp-performance-") as temporary:
        data_dir = Path(temporary)
        database = data_dir / "runs.sqlite3"
        active_run_id = seed(database, policy, owner)
        setup_seconds = time.perf_counter() - started
        stdio = asyncio.run(
            stdio_scenario(Path(sys.executable), data_dir, policy_path, active_run_id)
        )

        port = free_port()
        principal = AgentPrincipal(
            principal_id="performance-agent",
            owner=owner,
        )
        app = create_app(
            database,
            {HUMAN_TOKEN: owner.object_id},
            measurement_policy=policy,
            measurement_mcp_principals={AGENT_TOKEN: principal},
            mcp_cursor_secret=b"performance-cursor-key".ljust(32, b"x"),
            human_base_url=f"http://127.0.0.1:{port}",
        )
        server = Server(
            Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{port}"
            wait_for_server(base_url)
            http = asyncio.run(http_scenario(base_url, active_run_id))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Performance HTTP server did not stop")

        report = {
            "schema_version": "geo-mcp-performance/v1",
            "saved_runs": SAVED_RUN_COUNT,
            "simultaneous_client_target": 5,
            "active_jobs": 1,
            "setup_seconds": setup_seconds,
            "database_bytes": database.stat().st_size,
            "stdio": stdio,
            "streamable_http": http,
            "targets": {
                "warm_p95_seconds": 2,
                "default_payload_bytes": 16384,
                "progress_payload_bytes": 8192,
                "catalogue_bytes": 32768,
            },
        }
    for result in (report["stdio"], report["streamable_http"]):
        if result["p95_seconds"] > 2:
            raise SystemExit("MCP control-path p95 target failed")
        if result["max_payload_bytes"] > 16384:
            raise SystemExit("MCP default payload target failed")
        if result["catalogue_bytes"] > 32768:
            raise SystemExit("MCP catalogue target failed")
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
