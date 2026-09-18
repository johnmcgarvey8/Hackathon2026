import json
import os
import secrets
import asyncio
from contextvars import ContextVar
from functools import partial
from pathlib import Path
from typing import Any, Callable, Literal

import anyio
from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.types import ToolAnnotations

from geo_agent.agent_access import AgentPrincipal
from geo_agent.artifact_storage import ArtifactKind, ArtifactService, LocalArtifactStorage
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.mcp_contracts import (
    BrandAliases,
    BrandDomains,
    BriefText,
    Cursor,
    DefinitionVersion,
    EvidenceId,
    IdempotencyKey,
    Identifier,
    InputHash,
    Locale,
    ProfileId,
    PublicUrl,
    QueryId,
    QueryPairs,
    Revision,
    brand_definition,
)
from geo_agent.measurement_service import MeasurementApplicationService
from geo_agent.measurement_views import CursorCodec
from geo_agent.measurement_workflow import OwnerIdentity
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.workflow import Conflict, NotFound


PrincipalProvider = Callable[[], AgentPrincipal]
_http_principal: ContextVar[AgentPrincipal | None] = ContextVar(
    "geo_mcp_http_principal",
    default=None,
)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
WRITE_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
QUEUE_WORK = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)


def _current_http_principal() -> AgentPrincipal:
    principal = _http_principal.get()
    if principal is None:
        raise PermissionError("Authenticated agent identity is required")
    return principal


async def _invoke(
    function: Callable[..., dict[str, Any]],
    *args: Any,
    payload_limit: int = 16384,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = await anyio.to_thread.run_sync(partial(function, *args, **kwargs))
    except (Conflict, NotFound, PermissionError, ValueError) as error:
        raise ToolError(str(error)) from error
    size = len(
        json.dumps(
            result,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if size > payload_limit:
        raise ToolError(
            f"Result is {size} bytes and exceeds the {payload_limit}-byte tool limit; "
            "request a narrower view or continuation"
        )
    return result


def _notify_pending(service: MeasurementApplicationService) -> None:
    if service.pending_job_notifier is not None:
        asyncio.get_running_loop().run_in_executor(
            None,
            service.pending_job_notifier,
        )


def create_mcp_server(
    service: MeasurementApplicationService,
    principal_provider: PrincipalProvider,
) -> MCPServer:
    server = MCPServer(
        "geo-agent",
        version="0.2.0",
        instructions=(
            "Operate the saved GEO measurement workflow. Preparation and measurement are "
            "asynchronous. Exact-query approval and legacy recommendation decisions remain "
            "human-only."
        ),
    )

    def principal() -> AgentPrincipal:
        return principal_provider()

    @server.tool(annotations=READ_ONLY)
    async def geo_get_capabilities() -> dict[str, Any]:
        """Discover policy, capacity, permissions, and worker readiness."""
        return await _invoke(service.capabilities, principal())

    @server.tool(annotations=READ_ONLY)
    async def geo_list_runs(
        state: str = "",
        cursor: Cursor = "",
        page_size: int = 10,
    ) -> dict[str, Any]:
        """List owner-scoped run summaries with stable pagination."""
        return await _invoke(
            service.list_runs,
            principal(),
            state=state,
            cursor=cursor,
            page_size=page_size,
        )

    @server.tool(annotations=WRITE_IDEMPOTENT)
    async def geo_create_run(
        url: PublicUrl,
        audience: BriefText,
        goal: BriefText,
        locale: Locale,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        """Create an idempotent draft without invoking a provider."""
        return await _invoke(
            service.create_run,
            principal(),
            url=url,
            audience=audience,
            goal=goal,
            locale=locale,
            idempotency_key=idempotency_key,
        )

    @server.tool(annotations=READ_ONLY)
    async def geo_get_run(run_id: Identifier) -> dict[str, Any]:
        """Inspect bounded run, job, event, approval, and handoff state."""
        return await _invoke(service.get_run, principal(), run_id)

    @server.tool(annotations=QUEUE_WORK)
    async def geo_prepare_run(
        run_id: Identifier,
        expected_revision: Revision,
        idempotency_key: IdempotencyKey,
        execution_authorization_id: Identifier,
    ) -> dict[str, Any]:
        """Queue preparation using a human-issued stage authorization."""
        result = await _invoke(
            service.prepare_run,
            principal(),
            run_id=run_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            execution_authorization_id=execution_authorization_id,
        )
        _notify_pending(service)
        return result

    @server.tool(annotations=READ_ONLY)
    async def geo_get_preparation(
        run_id: Identifier,
        section: Literal["summary", "page", "findings"] = "summary",
        cursor: Cursor = "",
    ) -> dict[str, Any]:
        """Read saved preparation output using bounded sections."""
        return await _invoke(
            service.get_preparation,
            principal(),
            run_id=run_id,
            section=section,
            cursor=cursor,
            payload_limit=65536 if section != "summary" else 16384,
        )

    @server.tool(annotations=READ_ONLY)
    async def geo_get_query_plan(
        run_id: Identifier,
        query_id: str = "",
    ) -> dict[str, Any]:
        """Read a compact five-query packet or one exact query with full quotes."""
        return await _invoke(
            service.get_query_plan,
            principal(),
            run_id,
            query_id,
        )

    @server.tool(annotations=DESTRUCTIVE)
    async def geo_update_query_plan(
        run_id: Identifier,
        expected_revision: Revision,
        queries: QueryPairs,
    ) -> dict[str, Any]:
        """Save all five query pairs; this invalidates earlier approval."""
        return await _invoke(
            service.update_query_plan,
            principal(),
            run_id=run_id,
            expected_revision=expected_revision,
            queries=tuple(item.to_domain() for item in queries),
        )

    @server.tool(annotations=WRITE_IDEMPOTENT)
    async def geo_set_brand_definition(
        run_id: Identifier,
        expected_definition_version: DefinitionVersion,
        name: str,
        aliases: BrandAliases,
        domains: BrandDomains,
    ) -> dict[str, Any]:
        """Save a versioned, agent-origin brand definition."""
        return await _invoke(
            service.set_brand_definition,
            principal(),
            run_id=run_id,
            definition=brand_definition(name, aliases, domains),
            expected_definition_version=expected_definition_version,
        )

    @server.tool(annotations=QUEUE_WORK)
    async def geo_start_measurement(
        run_id: Identifier,
        expected_revision: Revision,
        input_hash: InputHash,
        idempotency_key: IdempotencyKey,
        execution_authorization_id: Identifier,
    ) -> dict[str, Any]:
        """Queue an approved measurement without a hidden recommendation call."""
        result = await _invoke(
            service.start_measurement,
            principal(),
            run_id=run_id,
            expected_revision=expected_revision,
            input_hash=input_hash,
            idempotency_key=idempotency_key,
            execution_authorization_id=execution_authorization_id,
        )
        _notify_pending(service)
        return result

    @server.tool(annotations=READ_ONLY)
    async def geo_get_progress(run_id: Identifier, job_id: str = "") -> dict[str, Any]:
        """Read compact durable progress and worker liveness."""
        return await _invoke(
            service.get_progress,
            principal(),
            run_id=run_id,
            job_id=job_id,
            payload_limit=8192,
        )

    @server.tool(annotations=READ_ONLY)
    async def geo_get_results(
        run_id: Identifier,
        detail: Literal["summary", "answer"] = "summary",
        query_id: str = "",
        profile_id: str = "",
    ) -> dict[str, Any]:
        """Read score summaries or one selected query/profile answer."""
        return await _invoke(
            service.get_results,
            principal(),
            run_id=run_id,
            detail=detail,
            query_id=query_id,
            profile_id=profile_id,
            payload_limit=65536 if detail == "answer" else 16384,
        )

    @server.tool(annotations=READ_ONLY)
    async def geo_get_evidence(
        run_id: Identifier,
        query_id: QueryId,
        evidence_id: EvidenceId,
    ) -> dict[str, Any]:
        """Read one retained source scoped by run, query, and evidence ID."""
        return await _invoke(
            service.get_evidence,
            principal(),
            run_id=run_id,
            query_id=query_id,
            evidence_id=evidence_id,
            payload_limit=65536,
        )

    @server.tool(annotations=READ_ONLY)
    async def geo_get_assessment(
        run_id: Identifier,
        view: Literal["summary", "grounding", "answers", "source_trace"] = "summary",
        definition_version: int = 0,
        query_id: str = "",
        profile_id: str = "",
        cursor: Cursor = "",
        page_size: int = 5,
    ) -> dict[str, Any]:
        """Read a version-bound brand and citation assessment."""
        return await _invoke(
            service.get_assessment,
            principal(),
            run_id=run_id,
            view=view,
            definition_version=definition_version,
            query_id=query_id,
            profile_id=profile_id,
            cursor=cursor,
            page_size=page_size,
            payload_limit=65536 if view != "summary" else 16384,
        )

    @server.tool(annotations=READ_ONLY)
    async def geo_get_content_strategy(
        run_id: Identifier,
        view: Literal["summary", "patterns"] = "summary",
        pattern_id: str = "",
        cursor: Cursor = "",
        page_size: int = 3,
    ) -> dict[str, Any]:
        """Read deterministic content experiments with zero provider calls."""
        return await _invoke(
            service.get_content_strategy,
            principal(),
            run_id=run_id,
            view=view,
            pattern_id=pattern_id,
            cursor=cursor,
            page_size=page_size,
            payload_limit=65536 if view == "patterns" else 16384,
        )

    @server.tool(annotations=READ_ONLY)
    async def geo_get_recommendations(
        run_id: Identifier,
        task_id: str = "",
    ) -> dict[str, Any]:
        """Read legacy saved drafts and their human decision state."""
        return await _invoke(
            service.get_recommendations,
            principal(),
            run_id=run_id,
            task_id=task_id,
            payload_limit=65536 if task_id else 16384,
        )

    @server.tool(annotations=DESTRUCTIVE)
    async def geo_cancel_job(job_id: Identifier) -> dict[str, Any]:
        """Cancel queued or future work without refunding consumed calls."""
        return await _invoke(service.cancel_job, principal(), job_id=job_id)

    @server.tool(annotations=WRITE_IDEMPOTENT)
    async def geo_create_export(
        run_id: Identifier,
        kind: Literal["measurement", "assessment", "content_strategy"],
        expected_revision: Revision,
        idempotency_key: IdempotencyKey,
        measurement_hash: str = "",
        definition_version: int = 0,
        definition_hash: str = "",
    ) -> dict[str, Any]:
        """Persist an immutable main or companion export descriptor."""
        return await _invoke(
            service.create_export,
            principal(),
            run_id=run_id,
            kind=ArtifactKind(kind),
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            measurement_hash=measurement_hash,
            definition_version=definition_version,
            definition_hash=definition_hash,
        )

    @server.tool(annotations=READ_ONLY)
    async def geo_list_exports(
        run_id: Identifier,
        page_size: int = 20,
        cursor: Cursor = "",
    ) -> dict[str, Any]:
        """List owner-scoped export metadata without storage keys."""
        return await _invoke(
            service.list_exports,
            principal(),
            run_id=run_id,
            page_size=page_size,
            cursor=cursor,
        )

    @server.tool(annotations=READ_ONLY)
    async def geo_read_export(
        run_id: Identifier,
        artifact_id: Identifier,
        entry: str,
        cursor: Cursor = "",
    ) -> dict[str, Any]:
        """Read an allowlisted UTF-8 ZIP entry with continuation."""
        return await _invoke(
            service.read_export,
            principal(),
            run_id=run_id,
            artifact_id=artifact_id,
            entry=entry,
            cursor=cursor,
            payload_limit=65536,
        )

    @server.resource(
        "geo://runs/{run_id}",
        name="GEO measurement run",
        description="Bounded owner-scoped run state and human handoff.",
        mime_type="application/json",
    )
    async def geo_run_resource(run_id: str) -> dict[str, Any]:
        try:
            return await anyio.to_thread.run_sync(
                partial(service.get_run, principal(), run_id)
            )
        except (Conflict, NotFound, PermissionError, ValueError) as error:
            raise ResourceNotFoundError(str(error)) from error

    return server


class AgentBearerMiddleware:
    def __init__(
        self,
        app: Any,
        token_principals: dict[str, AgentPrincipal],
    ):
        if not token_principals or any(len(token) < 32 for token in token_principals):
            raise ValueError("Configure at least one 32-character MCP agent token")
        self.app = app
        self.token_principals = dict(token_principals)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = ""
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                decoded = value.decode("latin-1")
                if decoded.lower().startswith("bearer "):
                    supplied = decoded[7:]
                break
        principal = next(
            (
                principal
                for token, principal in self.token_principals.items()
                if secrets.compare_digest(supplied.encode(), token.encode())
            ),
            None,
        )
        if principal is None:
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b"Bearer"),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": b'{"error":"Authentication required"}',
            })
            return
        token = _http_principal.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _http_principal.reset(token)


def create_streamable_http_app(
    service: MeasurementApplicationService,
    token_principals: dict[str, AgentPrincipal],
) -> tuple[MCPServer, Any]:
    server = create_mcp_server(service, _current_http_principal)
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=False,
        stateless_http=True,
        max_request_body_size=1048576,
        session_idle_timeout=900,
        max_sessions=25,
        host="127.0.0.1",
    )
    return server, AgentBearerMiddleware(app, token_principals)


def _persistent_secret(path: Path) -> bytes:
    try:
        encoded = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_bytes(32)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(value.hex())
        return value
    try:
        value = bytes.fromhex(encoded)
    except ValueError as error:
        raise ValueError("MCP cursor key file is invalid") from error
    if len(value) != 32:
        raise ValueError("MCP cursor key file must contain 32 bytes")
    return value


def build_local_mcp() -> tuple[MCPServer, SQLiteMeasurementRepository]:
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env", override=False, interpolate=False)
    data_dir = Path(os.environ.get("GEO_DATA_DIR", root / ".data"))
    policy_path = Path(os.environ.get("GEO_MEASUREMENT_POLICY", root / "measurement-policy.json"))
    policy = MeasurementExecutionPolicy.model_validate_json(
        policy_path.read_text(encoding="utf-8")
    )
    if (
        policy.execution_mode == "live"
        and os.environ.get("GEO_MCP_ALLOW_LIVE", "").casefold() != "true"
    ):
        raise ValueError("Live MCP access requires explicit GEO_MCP_ALLOW_LIVE=true")
    repository = SQLiteMeasurementRepository(data_dir / "runs.sqlite3")
    artifacts = ArtifactService(
        repository,
        LocalArtifactStorage(data_dir / "measurement-artifacts"),
    )
    service = MeasurementApplicationService(
        repository,
        policy,
        artifacts,
        CursorCodec(_persistent_secret(data_dir / "mcp-cursor-key")),
        human_base_url=os.environ.get("GEO_HUMAN_BASE_URL", "http://127.0.0.1:8088"),
    )
    principal = AgentPrincipal(
        principal_id=os.environ.get("GEO_MCP_PRINCIPAL_ID", "local-agent"),
        owner=OwnerIdentity(
            tenant_id=os.environ.get("GEO_MCP_TENANT_ID", "local-development"),
            object_id=os.environ.get("GEO_MCP_OWNER", "local-developer"),
        ),
    )
    return create_mcp_server(service, lambda: principal), repository


def main() -> None:
    server, repository = build_local_mcp()
    try:
        server.run(transport="stdio")
    finally:
        repository.close()


if __name__ == "__main__":
    main()
