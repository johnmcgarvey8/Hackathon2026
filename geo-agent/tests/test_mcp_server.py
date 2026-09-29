import asyncio
import json
import os
import socket
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread

import httpx
import httpx2
import pytest
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from uvicorn import Config, Server

from geo_agent.agent_access import AgentPrincipal, ExecutionStage
from geo_agent.api import create_app
from geo_agent.artifact_storage import ArtifactKind, ArtifactService, LocalArtifactStorage
from geo_agent.contracts import digest
from geo_agent.evidence_assessment import BrandDefinition
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.mcp_server import create_mcp_server
from geo_agent.measurement_service import MeasurementApplicationService
from geo_agent.measurement_views import CursorCodec
from geo_agent.measurement_workflow import MeasurementCoordinator, OwnerIdentity
from geo_agent.mock_runtime import MockMeasurementRuntime
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.workflow import Conflict


HUMAN_TOKEN = "h" * 40
AGENT_TOKEN = "m" * 40
TOOL_NAMES = (
    "geo_get_capabilities",
    "geo_list_runs",
    "geo_create_run",
    "geo_get_run",
    "geo_prepare_run",
    "geo_get_preparation",
    "geo_get_query_plan",
    "geo_update_query_plan",
    "geo_set_brand_definition",
    "geo_start_measurement",
    "geo_get_progress",
    "geo_get_results",
    "geo_get_evidence",
    "geo_get_assessment",
    "geo_get_content_strategy",
    "geo_get_recommendations",
    "geo_cancel_job",
    "geo_create_export",
    "geo_list_exports",
    "geo_read_export",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def policy() -> MeasurementExecutionPolicy:
    return MeasurementExecutionPolicy.model_validate_json(
        (project_root() / "measurement-policy.json").read_text(encoding="utf-8")
    )


def build_service(tmp_path):
    repository = SQLiteMeasurementRepository(tmp_path / "mcp.sqlite3")
    execution_policy = policy()
    service = MeasurementApplicationService(
        repository,
        execution_policy,
        ArtifactService(
            repository,
            LocalArtifactStorage(tmp_path / "artifacts"),
        ),
        CursorCodec(b"mcp-test-cursor-key".ljust(32, b"x")),
        human_base_url="http://127.0.0.1:8088",
    )
    principal = AgentPrincipal(
        principal_id="test-agent",
        owner=OwnerIdentity(tenant_id="local-development", object_id="alice"),
    )
    return repository, execution_policy, service, principal


def call_data(result):
    assert result.is_error is not True
    assert result.structured_content is not None
    return result.structured_content


def test_in_memory_mcp_completes_authorized_three_profile_flow(tmp_path):
    async def scenario():
        repository, execution_policy, service, principal = build_service(tmp_path)
        runtime = MockMeasurementRuntime(repository, execution_policy)
        server = create_mcp_server(service, lambda: principal)
        async with Client(server, raise_exceptions=True) as client:
            tools = await client.list_tools()
            assert tuple(tool.name for tool in tools.tools) == TOOL_NAMES
            assert sum(len(tool.model_dump_json().encode("utf-8")) for tool in tools.tools) <= 32768
            assert all(
                "anyOf" not in json.dumps(tool.input_schema)
                and "allOf" not in json.dumps(tool.input_schema)
                for tool in tools.tools
            )

            created = call_data(await client.call_tool("geo_create_run", {
                "url": "https://example.com/mock-page",
                "audience": "Research buyers",
                "goal": "Compare trusted options",
                "locale": "en-GB",
                "idempotency_key": "create-mcp-flow",
            }))
            run_id = created["run_id"]
            assert created["data"]["provider_calls_started_by_this_tool"] == 0
            assert call_data(await client.call_tool("geo_create_run", {
                "url": "https://example.com/mock-page",
                "audience": "Research buyers",
                "goal": "Compare trusted options",
                "locale": "en-GB",
                "idempotency_key": "create-mcp-flow",
            }))["run_id"] == run_id

            preparation_auth = service.issue_execution_authorization(
                owner=principal.owner,
                run_id=run_id,
                principal_id=principal.principal_id,
                stage=ExecutionStage.PREPARE,
                expected_revision=1,
                input_hash=None,
                lifetime_seconds=900,
            )
            queued = call_data(await client.call_tool("geo_prepare_run", {
                "run_id": run_id,
                "expected_revision": 1,
                "idempotency_key": "prepare-mcp-flow",
                "execution_authorization_id": preparation_auth.authorization_id,
            }))
            assert queued["status"] == "queued"
            assert queued["data"]["provider_calls_started_by_this_tool"] == 0
            runtime.drain()

            replay = call_data(await client.call_tool("geo_prepare_run", {
                "run_id": run_id,
                "expected_revision": 1,
                "idempotency_key": "prepare-mcp-flow",
                "execution_authorization_id": preparation_auth.authorization_id,
            }))
            assert replay["job_id"] == queued["job_id"]

            query_plan = call_data(await client.call_tool("geo_get_query_plan", {
                "run_id": run_id,
            }))
            assert len(query_plan["data"]["queries"]) == 5
            prepared = repository.get(run_id, principal.owner)
            approved = MeasurementCoordinator(repository).approve(
                run_id,
                principal.owner,
                prepared.revision,
                prepared.inputs.approval_hash,
            )
            evaluation_auth = service.issue_execution_authorization(
                owner=principal.owner,
                run_id=run_id,
                principal_id=principal.principal_id,
                stage=ExecutionStage.EVALUATE,
                expected_revision=approved.revision,
                input_hash=approved.inputs.approval_hash,
                lifetime_seconds=900,
            )
            started = call_data(await client.call_tool("geo_start_measurement", {
                "run_id": run_id,
                "expected_revision": approved.revision,
                "input_hash": approved.inputs.approval_hash,
                "idempotency_key": "evaluate-mcp-flow",
                "execution_authorization_id": evaluation_auth.authorization_id,
            }))
            assert started["status"] == "queued"
            preparation_progress = call_data(await client.call_tool(
                "geo_get_progress",
                {
                    "run_id": run_id,
                    "job_id": queued["job_id"],
                },
            ))
            assert preparation_progress["job_id"] == queued["job_id"]
            assert preparation_progress["data"]["job_type"] == "prepare"
            assert preparation_progress["data"]["checkpointed_operations"] == 3
            runtime.drain()

            results = call_data(await client.call_tool("geo_get_results", {
                "run_id": run_id,
                "detail": "summary",
            }))
            assert results["status"] == "ready"
            assert len(results["data"]["retrievals"]) == 5
            assert len(results["data"]["outcomes"]) == 15
            evidence = call_data(await client.call_tool("geo_get_evidence", {
                "run_id": run_id,
                "query_id": "q-1",
                "evidence_id": "q-1-target",
            }))
            assert evidence["data"]["provenance"] == "synthetic"
            strategy = call_data(await client.call_tool("geo_get_content_strategy", {
                "run_id": run_id,
                "view": "summary",
            }))
            assert strategy["data"]["measurement_hash"]

            completed = repository.get(run_id, principal.owner)
            exported = call_data(await client.call_tool("geo_create_export", {
                "run_id": run_id,
                "kind": "measurement",
                "expected_revision": completed.revision,
                "idempotency_key": "export-mcp-flow",
            }))
            artifact_id = exported["data"]["artifact"]["artifact_id"]
            replayed_export = call_data(await client.call_tool("geo_create_export", {
                "run_id": run_id,
                "kind": "measurement",
                "expected_revision": completed.revision,
                "idempotency_key": "export-mcp-flow",
            }))
            assert replayed_export["data"]["artifact"]["artifact_id"] == artifact_id
            conflicting_export = await client.call_tool("geo_create_export", {
                "run_id": run_id,
                "kind": "content_strategy",
                "expected_revision": replayed_export["run_revision"],
                "idempotency_key": "export-mcp-flow",
            })
            assert conflicting_export.is_error is True
            entry = call_data(await client.call_tool("geo_read_export", {
                "run_id": run_id,
                "artifact_id": artifact_id,
                "entry": "manifest.json",
            }))
            assert json.loads(entry["data"]["content"])["publish_permission"] is False

        with sqlite3.connect(tmp_path / "mcp.sqlite3") as connection:
            assert connection.execute("SELECT COUNT(*) FROM operation_claims").fetchone()[0] == 23
            assert connection.execute(
                "SELECT COUNT(*) FROM operation_claims WHERE checkpoint_payload IS NOT NULL"
            ).fetchone()[0] == 23
        repository.close()

    asyncio.run(scenario())


def test_stdio_transport_discovers_and_calls_without_starting_work(tmp_path):
    async def scenario():
        environment = {
            "GEO_DATA_DIR": str(tmp_path / "stdio-data"),
            "GEO_MEASUREMENT_POLICY": str(project_root() / "measurement-policy.json"),
            "GEO_MCP_TENANT_ID": "local-development",
            "GEO_MCP_OWNER": "stdio-owner",
            "GEO_MCP_PRINCIPAL_ID": "stdio-agent",
            "PYTHONUNBUFFERED": "1",
            "SYSTEMROOT": os.environ["SYSTEMROOT"],
            "PATH": os.environ["PATH"],
        }
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "geo_agent.mcp_server"],
            env=environment,
        )
        async with Client(parameters) as client:
            tools = await client.list_tools()
            assert tuple(tool.name for tool in tools.tools) == TOOL_NAMES
            capabilities = call_data(await client.call_tool("geo_get_capabilities", {}))
            assert capabilities["data"]["queue"]["active_global"] == 0
            created = call_data(await client.call_tool("geo_create_run", {
                "url": "https://example.com/stdio",
                "audience": "Researchers",
                "goal": "Validate stdio framing",
                "locale": "en-GB",
                "idempotency_key": "stdio-create",
            }))
            assert created["status"] == "draft"
        with sqlite3.connect(tmp_path / "stdio-data" / "runs.sqlite3") as connection:
            assert connection.execute("SELECT COUNT(*) FROM workflow_jobs").fetchone()[0] == 0

    asyncio.run(scenario())


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_streamable_http_transport_uses_separate_agent_identity(tmp_path):
    repository, execution_policy, _service, principal = build_service(tmp_path)
    repository.close()
    port = _free_port()
    app = create_app(
        tmp_path / "http.sqlite3",
        {HUMAN_TOKEN: "alice"},
        measurement_policy=execution_policy,
        measurement_mcp_principals={AGENT_TOKEN: principal},
        mcp_cursor_secret=b"http-mcp-test-key".ljust(32, b"x"),
        human_base_url=f"http://127.0.0.1:{port}",
    )
    server = Server(Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base_url}/health", timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        else:
            raise AssertionError("Uvicorn test server did not start")

        assert httpx.get(
            f"{base_url}/api/v2/policy",
            headers={"Authorization": "Bearer " + AGENT_TOKEN},
        ).status_code == 401
        initialization = httpx.post(
            f"{base_url}/mcp",
            headers={
                "Authorization": "Bearer " + AGENT_TOKEN,
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "identity-test", "version": "1"},
                },
            },
        )
        assert initialization.status_code == 200
        assert "mcp-session-id" not in initialization.headers

        async def scenario():
            async with httpx2.AsyncClient(
                headers={"Authorization": "Bearer " + AGENT_TOKEN},
            ) as http_client:
                transport = streamable_http_client(
                    f"{base_url}/mcp",
                    http_client=http_client,
                )
                async with Client(transport) as client:
                    tools = await client.list_tools()
                    assert tuple(tool.name for tool in tools.tools) == TOOL_NAMES
                    capabilities = call_data(
                        await client.call_tool("geo_get_capabilities", {})
                    )
                    assert capabilities["data"]["agent_scopes"]

        asyncio.run(scenario())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


def test_live_mcp_requires_separate_explicit_opt_in(tmp_path):
    from test_live_runtime import live_policy

    principal = AgentPrincipal(
        principal_id="live-agent",
        owner=OwnerIdentity(tenant_id="tenant-a", object_id="user-a"),
    )
    with pytest.raises(ValueError, match="GEO_MCP_ALLOW_LIVE"):
        create_app(
            tmp_path / "live-mcp.sqlite3",
            {HUMAN_TOKEN: "user-a"},
            measurement_policy=live_policy(),
            measurement_mcp_principals={AGENT_TOKEN: principal},
            mcp_cursor_secret=b"live-mcp-test-key".ljust(32, b"x"),
        )


def test_maximal_query_plan_has_compact_summary_and_exact_detail(tmp_path):
    from geo_agent.contracts import EvidenceQuote, QueryPair, QueryPlan
    from test_measurement_workflow import inputs

    async def scenario():
        repository, execution_policy, service, principal = build_service(tmp_path)
        packet = inputs().model_copy(update={"policy_hash": execution_policy.policy_hash})
        snapshot = packet.snapshot.model_copy(update={"content": "x" * 10000})
        queries = tuple(
            QueryPair(
                query_id=f"q-{index}",
                priority=index,
                rationale=f"{index}" + "r" * 499,
                intent=f"{index}" + "i" * 499,
                chat_query=f"{index}" + "c" * 499,
                grounding_query=f"{index}" + "g" * 499,
                evidence=(EvidenceQuote(
                    evidence_id="page-1",
                    quote="x" * 500,
                ),),
            )
            for index in range(1, 6)
        )
        packet = packet.model_copy(update={
            "snapshot": snapshot,
            "query_plan": QueryPlan(queries=queries),
        })
        run = repository.create(principal.owner, packet)
        server = create_mcp_server(service, lambda: principal)
        async with Client(server, raise_exceptions=True) as client:
            updated = await client.call_tool(
                "geo_update_query_plan",
                {
                    "run_id": run.run_id,
                    "expected_revision": run.revision,
                    "queries": [
                        query.model_dump(mode="json")
                        for query in queries
                    ],
                },
            )
            updated_data = call_data(updated)
            assert len(json.dumps(updated_data).encode("utf-8")) <= 16384
            assert updated_data["data"]["query_count"] == 5
            summary = await client.call_tool(
                "geo_get_query_plan",
                {"run_id": run.run_id},
            )
            summary_data = call_data(summary)
            assert len(json.dumps(summary_data).encode("utf-8")) <= 16384
            assert summary_data["data"]["detail"] == "summary"
            detail = await client.call_tool(
                "geo_get_query_plan",
                {"run_id": run.run_id, "query_id": "q-1"},
            )
            detail_data = call_data(detail)
            assert len(json.dumps(detail_data).encode("utf-8")) <= 16384
            assert detail_data["data"]["queries"][0]["evidence"][0]["quote"] == "x" * 500
        repository.close()

    asyncio.run(scenario())


def test_conflicting_export_key_is_reserved_before_artifact_persistence(tmp_path):
    from geo_agent.measurement_workflow import MeasurementEvent, MeasurementState
    from test_artifacts import measurement_result

    repository, _policy, service, principal = build_service(tmp_path)
    measurement = measurement_result()
    created = repository.create(principal.owner, measurement.inputs)
    ready = repository.mutate(
        created.run_id,
        principal.owner,
        created.revision,
        lambda run: run.model_copy(update={
            "measurement": measurement,
            "state": MeasurementState.READY,
            "events": (*run.events, MeasurementEvent(
                sequence=len(run.events) + 1,
                event_type="ready",
            )),
        }),
    )
    definition = repository.save_brand_definition(
        ready.run_id,
        principal.owner,
        BrandDefinition(name="Example", domains=("example.com",)),
        0,
    )
    measurement_hash = digest(measurement.model_dump(mode="json"))

    def create(kind: ArtifactKind):
        return service.create_export(
            principal,
            run_id=ready.run_id,
            kind=kind,
            expected_revision=ready.revision,
            idempotency_key="conflicting-export-key",
            measurement_hash=measurement_hash,
            definition_version=definition.definition_version,
            definition_hash=definition.definition_hash,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(create, kind)
            for kind in (ArtifactKind.ASSESSMENT, ArtifactKind.CONTENT_STRATEGY)
        ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except Conflict as error:
                outcomes.append(error)

    assert sum(isinstance(item, Conflict) for item in outcomes) == 1
    assert len(repository.list_artifacts(ready.run_id, principal.owner)) == 1
    repository.close()
