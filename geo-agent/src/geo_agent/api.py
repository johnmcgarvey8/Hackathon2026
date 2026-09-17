import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from geo_agent.artifacts import render_bundle, score_report
from geo_agent.artifact_storage import LocalArtifactStorage
from geo_agent.chat_protocol import conversation_router
from geo_agent.contracts import Brief, Contract, Provenance, Query, Run, RunInputs, State
from geo_agent.conversation import ChatRequest, ConversationAgent
from geo_agent.evaluation import match_citations
from geo_agent.fixtures import evaluate_synthetic, synthetic_inputs
from geo_agent.live import BudgetedLiveWorkflow, LiveWorkflow
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.measurement_api import OperatorPrincipal, create_measurement_router
from geo_agent.mock_runtime import MockMeasurementRuntime
from geo_agent.page_analysis import AnalysisRequest, EvaluationBriefRequest, PageAnalysisService
from geo_agent.persistence import SQLiteMeasurementRepository
from geo_agent.webiq import ProviderError
from geo_agent.workflow import Conflict, Coordinator, NotFound, RunStore


class RevisionRequest(Contract):
    expected_revision: int = Field(ge=1)


class ApprovalRequest(RevisionRequest):
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class StartRequest(RevisionRequest):
    idempotency_key: str = Field(min_length=1, max_length=128)


class QueryRevisionRequest(RevisionRequest):
    queries: tuple[Query, ...] = Field(min_length=5, max_length=10)


class LiveBriefRequest(Brief):
    idempotency_key: str = Field(min_length=1, max_length=128)


def create_app(database: Path, api_tokens: dict[str, str], live: LiveWorkflow | BudgetedLiveWorkflow | None = None,
               chat: ConversationAgent | None = None, analysis: PageAnalysisService | None = None,
               measurement_policy: MeasurementExecutionPolicy | None = None,
               measurement_auto_worker: bool = False) -> FastAPI:
    if not api_tokens or any(len(token) < 32 or not owner for token, owner in api_tokens.items()):
        raise ValueError("Configure at least one 32-character token mapped to an owner")
    tokens = dict(api_tokens)
    store = RunStore(database)
    coordinator = Coordinator(store)
    app = FastAPI(title="GEO Agent Backend", version="0.3.0", description="Approval-gated GEO evaluation and read-only conversational evidence assistant. Provider calls require explicit local policies. No publishing integration.")
    bearer = HTTPBearer(auto_error=False)

    def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
        supplied = credentials.credentials if credentials else ""
        for token, owner in tokens.items():
            if secrets.compare_digest(supplied.encode(), token.encode()):
                return owner
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})

    owner_dependency = Depends(authenticate)

    if measurement_policy is not None:
        measurement_repository = SQLiteMeasurementRepository(database)
        mock_runtime = MockMeasurementRuntime(measurement_repository, measurement_policy) if measurement_auto_worker else None

        def authenticate_operator(owner: Annotated[str, Depends(authenticate)]) -> OperatorPrincipal:
            return OperatorPrincipal(
                tenant_id="local-development",
                object_id=owner,
                roles=(measurement_policy.owner_role,),
            )

        app.include_router(create_measurement_router(
            measurement_repository,
            measurement_policy,
            authenticate_operator,
            LocalArtifactStorage(database.parent / "measurement-artifacts"),
            mock_runtime.drain if mock_runtime is not None else None,
        ))

    @app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
    def browser_chat() -> HTMLResponse:
        return HTMLResponse(Path(__file__).with_name("chat.html").read_text(encoding="utf-8"), headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        })

    @app.get("/measurements", response_class=HTMLResponse, include_in_schema=False)
    def browser_measurements() -> HTMLResponse:
        return HTMLResponse(Path(__file__).with_name("measurement.html").read_text(encoding="utf-8"), headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        })

    def chat_service(owner: str) -> ConversationAgent:
        if chat is None:
            raise HTTPException(503, "Conversational agent is not enabled")
        if owner != chat.chats.policy.owner:
            raise NotFound("Chat policy not found")
        return chat

    @app.get("/chat-policy")
    def get_chat_policy(owner: str = owner_dependency) -> dict:
        service = chat_service(owner)
        return {"policy": service.chats.policy.model_dump(mode="json", exclude={"owner"}), "budget": service.chats.budget()}

    @app.post("/conversations", status_code=201)
    def create_conversation(owner: str = owner_dependency) -> dict:
        return chat_service(owner).chats.create(owner)

    @app.get("/conversations/{chat_id}")
    def get_conversation(chat_id: str, owner: str = owner_dependency) -> dict:
        return chat_service(owner).chats.get(chat_id, owner)

    @app.post("/conversations/{chat_id}/messages")
    async def chat_message(chat_id: str, body: ChatRequest, owner: str = owner_dependency) -> dict:
        return await chat_service(owner).respond(chat_id, owner, body)

    app.include_router(conversation_router(chat_service, authenticate))

    def analysis_service(owner: str) -> PageAnalysisService:
        if analysis is None:
            raise HTTPException(503, "Page analysis is not enabled")
        if owner != analysis.policy.owner:
            raise NotFound("Page analysis policy not found")
        return analysis

    @app.get("/analysis-policy")
    def get_analysis_policy(owner: str = owner_dependency) -> dict:
        service = analysis_service(owner)
        return {"policy": service.policy.model_dump(mode="json", exclude={"owner"}), "budget": service.budget()}

    @app.get("/page-analyses")
    def list_page_analyses(owner: str = owner_dependency) -> list[dict]:
        return analysis_service(owner).list(owner)

    @app.post("/page-analyses", status_code=201)
    def analyse_page(body: AnalysisRequest, owner: str = owner_dependency) -> dict:
        return analysis_service(owner).analyse(body, owner)

    @app.get("/page-analyses/{analysis_id}")
    def get_page_analysis(analysis_id: str, owner: str = owner_dependency) -> dict:
        return analysis_service(owner).get(analysis_id, owner)

    @app.post("/page-analyses/{analysis_id}/evaluation")
    def prepare_page_evaluation(analysis_id: str, body: EvaluationBriefRequest, owner: str = owner_dependency) -> dict:
        return analysis_service(owner).prepare_evaluation(analysis_id, body, owner)

    def view(run: Run) -> dict:
        return {
            **run.model_dump(mode="json", exclude={"owner", "start_key"}),
            "approval_hash": run.inputs.approval_hash,
            "scores": score_report(run) if run.results else None,
            "citation_matches": [
                {"query_id": result.query_id, "profile_id": result.profile_id,
                 "matches": [match.model_dump() for match in match_citations(result, run.inputs.snapshot.url)]}
                for result in run.results
            ],
        }

    @app.exception_handler(Conflict)
    async def conflict_handler(request: Request, error: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(NotFound)
    async def missing_handler(request: Request, error: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Run or evidence not found"})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "mode": "live-policy-enabled" if live else "chat-policy-enabled" if chat else "synthetic-only", "live_ready": False, "live_configured": live is not None, "chat_configured": chat is not None}

    @app.exception_handler(ProviderError)
    async def provider_handler(request: Request, error: ProviderError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(error)})

    @app.get("/live-policy")
    def live_policy(owner: str = owner_dependency) -> dict:
        if live is None or owner != live.policy.owner:
            raise HTTPException(404, "No live policy for this owner")
        return live.policy.model_dump(mode="json")

    @app.get("/live-budget")
    def live_budget(owner: str = owner_dependency) -> dict:
        if live is None or owner != live.policy.owner:
            raise HTTPException(404, "No live policy for this owner")
        if not isinstance(live, BudgetedLiveWorkflow):
            return {"additional_runs": 0, "remaining_runs": 0}
        return live.budget()

    @app.post("/fixture-runs", status_code=201)
    def create_fixture(owner: str = owner_dependency) -> dict:
        return view(store.create(owner, synthetic_inputs()))

    @app.post("/briefs", status_code=201)
    def submit_live_brief(body: LiveBriefRequest, owner: str = owner_dependency) -> dict:
        if live is None:
            raise HTTPException(503, "Live providers are not enabled. No page was fetched; no synthetic fallback was used.")
        brief = Brief.model_validate(body.model_dump(exclude={"idempotency_key"}))
        return view(live.prepare(brief, owner, body.idempotency_key))

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, owner: str = owner_dependency) -> dict:
        return view(store.get(run_id, owner))

    @app.get("/runs/{run_id}/events")
    def get_events(run_id: str, owner: str = owner_dependency) -> dict:
        run = store.get(run_id, owner)
        return {"revision": run.revision, "events": run.events}

    @app.put("/runs/{run_id}/queries")
    def revise_queries(run_id: str, body: QueryRevisionRequest, owner: str = owner_dependency) -> dict:
        run = store.get(run_id, owner)
        try:
            inputs = RunInputs.model_validate({**run.inputs.model_dump(), "queries": body.queries})
        except ValueError:
            raise HTTPException(422, "Queries must have unique IDs and deduplicated text") from None
        return view(coordinator.revise(run_id, owner, body.expected_revision, inputs))

    @app.post("/runs/{run_id}/query-approval")
    def approve(run_id: str, body: ApprovalRequest, owner: str = owner_dependency) -> dict:
        return view(coordinator.approve(run_id, owner, body.expected_revision, body.input_hash))

    @app.post("/runs/{run_id}/start")
    def start(run_id: str, body: StartRequest, owner: str = owner_dependency) -> dict:
        run = store.get(run_id, owner)
        if analysis is not None and analysis.owns_run(run_id, owner):
            return view(analysis.execute(run_id, owner, body.expected_revision, body.idempotency_key))
        if run.inputs.snapshot.provenance == Provenance.LIVE and live is not None:
            return view(live.execute(run_id, owner, body.expected_revision, body.idempotency_key))
        if run.inputs.snapshot.provenance != Provenance.SYNTHETIC:
            raise HTTPException(503, "Live and recorded evaluators are not connected")
        run = coordinator.start(run_id, owner, body.expected_revision, body.idempotency_key)
        if run.state == State.EVALUATING:
            run = coordinator.finish(run_id, owner, body.expected_revision, evaluate_synthetic(run))
        return view(run)

    @app.post("/runs/{run_id}/cancel")
    def cancel(run_id: str, body: RevisionRequest, owner: str = owner_dependency) -> dict:
        return view(coordinator.cancel(run_id, owner, body.expected_revision))

    @app.get("/runs/{run_id}/evidence/{evidence_id}")
    def evidence(run_id: str, evidence_id: str, owner: str = owner_dependency) -> dict:
        run = store.get(run_id, owner)
        for result in run.results:
            for source in result.sources:
                if source.evidence_id == evidence_id:
                    return source.model_dump(mode="json")
        raise NotFound("Evidence not found")

    @app.post("/runs/{run_id}/exports")
    def export(run_id: str, body: RevisionRequest, owner: str = owner_dependency) -> dict:
        run = coordinator.mark_exported(run_id, owner, body.expected_revision)
        return {"run_id": run.run_id, "revision": run.revision, "artifact_id": "bundle",
                "download_url": f"/runs/{run.run_id}/artifacts/bundle"}

    @app.get("/runs/{run_id}/artifacts/{artifact_id}")
    def download(run_id: str, artifact_id: str, owner: str = owner_dependency) -> Response:
        run = store.get(run_id, owner)
        if artifact_id != "bundle" or run.state != State.EXPORTED:
            raise NotFound("Artifact not found")
        return Response(render_bundle(run), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="geo-{run.run_id}.zip"',
            "Cache-Control": "no-store",
        })

    return app