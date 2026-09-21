from collections.abc import Awaitable, Callable
import asyncio
from datetime import datetime
import logging
from typing import Literal, Protocol

from pydantic import Field
from sqlalchemy import insert, select, update

from geo_agent.contracts import Contract, identifier, utc_now
from geo_agent.measurement_workflow import OwnerIdentity
from geo_agent.persistence import SQLAlchemyMeasurementRepository, project_conversations
from geo_agent.projects import Project
from geo_agent.workflow import Conflict, NotFound


logger = logging.getLogger(__name__)


class ProjectChatCitation(Contract):
    source_class: Literal["geo-evidence", "org-knowledge", "work-context", "model-knowledge"]
    source_id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=500)
    url: str | None = Field(default=None, max_length=2000)


class ProjectAgentReply(Contract):
    answer: str = Field(min_length=1, max_length=10000)
    citations: tuple[ProjectChatCitation, ...] = ()
    provider_response_id: str | None = Field(default=None, max_length=500)
    mode: Literal["mock", "foundry", "workflow"]
    agent_name: str | None = None
    agent_version: str | None = None
    context_hash: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)


class ProjectChatRequest(Contract):
    message: str = Field(min_length=1, max_length=4000)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)
    use_organisational_context: bool = False


class ProjectMeasurementWorkflow(Contract):
    status: Literal["collecting", "preparing", "evaluating", "completed", "failed"]
    url: str | None = Field(default=None, max_length=2000)
    objective: str | None = Field(default=None, max_length=1000)
    run_id: str | None = None
    source_idempotency_key: str
    error: str | None = Field(default=None, max_length=1000)


class BoundProjectContext(Contract):
    name: str
    domains: tuple[str, ...]
    locale: str
    goal: str | None = None
    brand_definition_version: int | None = Field(default=None, ge=1)
    brand_definition_hash: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$",
    )


class ProjectConversationTurn(Contract):
    sequence: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)
    use_organisational_context: bool
    origin: Literal["user", "workflow"] = "user"
    status: Literal["running", "completed", "failed"]
    answer: str | None = None
    error: str | None = None
    citations: tuple[ProjectChatCitation, ...] = ()
    provider_response_id: str | None = None
    mode: Literal["mock", "foundry", "workflow"] | None = None
    agent_name: str | None = None
    agent_version: str | None = None
    context_hash: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class ProjectConversation(Contract):
    schema_version: Literal["geo-project-conversation/v1"] = "geo-project-conversation/v1"
    conversation_id: str = Field(default_factory=identifier)
    project_id: str
    run_id: str | None = None
    measurement_workflow: ProjectMeasurementWorkflow | None = None
    bound_project_context: BoundProjectContext | None = None
    owner: OwnerIdentity
    revision: int = Field(default=0, ge=0)
    title: str = "New conversation"
    turns: tuple[ProjectConversationTurn, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProjectAgent(Protocol):
    def status(self, project: Project) -> dict: ...

    async def respond(
        self,
        project: Project,
        conversation: ProjectConversation,
        request: ProjectChatRequest,
    ) -> ProjectAgentReply: ...


class ProjectChatWorkflowHandler(Protocol):
    def handle(
        self,
        project: Project,
        conversation: ProjectConversation,
        request: ProjectChatRequest,
    ) -> tuple[ProjectConversation, ProjectAgentReply | None]: ...


class FoundryAgentClient:
    """Runtime seam for a manually created Foundry agent."""

    def __init__(
        self,
        callback: Callable[
            [Project, ProjectConversation, ProjectChatRequest],
            Awaitable[ProjectAgentReply],
        ],
    ):
        self._callback = callback

    async def respond(
        self,
        project: Project,
        conversation: ProjectConversation,
        request: ProjectChatRequest,
    ) -> ProjectAgentReply:
        if project.foundry is None:
            raise Conflict("Project Foundry configuration is missing")
        return await self._callback(project, conversation, request)

    def status(self, project: Project) -> dict:
        return {
            "mode": "foundry" if project.foundry else "unavailable",
            "can_send": project.foundry is not None,
            "organisational_context_available": False,
            "detail": "Injected Foundry runtime.",
        }


class MockProjectAgent:
    def __init__(self, repository: SQLAlchemyMeasurementRepository):
        self.repository = repository

    def status(self, project: Project) -> dict:
        return {
            "mode": "mock",
            "can_send": not project.archived,
            "organisational_context_available": False,
            "detail": "Local mock assistant. No live model or organisational retrieval is used.",
        }

    async def respond(
        self,
        project: Project,
        conversation: ProjectConversation,
        request: ProjectChatRequest,
    ) -> ProjectAgentReply:
        run_summary = "No measurement run is selected."
        citations: tuple[ProjectChatCitation, ...] = ()
        if conversation.run_id is not None:
            run = self.repository.get_project_run(
                project.project_id,
                conversation.run_id,
                conversation.owner,
            )
            run_summary = f"The selected run is {run.state.value} at revision {run.revision}."
            if run.inputs is not None:
                run_summary += f" It contains {len(run.inputs.query_plan.queries)} approved query candidates."
            citations = (
                ProjectChatCitation(
                    source_class="geo-evidence",
                    source_id=run.run_id,
                    title=f"{project.name} measurement run",
                ),
            )
        context_note = (
            "Organisational grounding is simulated in mock mode and no SharePoint content was retrieved."
            if request.use_organisational_context
            else "Organisational grounding was disabled for this turn."
        )
        return ProjectAgentReply(
            answer=(
                f"This is the local mock assistant for {project.name}. {run_summary} "
                f"{context_note} Your question was: {request.message}"
            ),
            citations=citations,
            mode="mock",
        )


class UnavailableProjectAgent:
    def status(self, project: Project) -> dict:
        return {
            "mode": "unavailable",
            "can_send": False,
            "organisational_context_available": False,
            "detail": (
                "No hosted agent is configured. Set GEO_FOUNDRY_AGENT_ENDPOINT, "
                "GEO_FOUNDRY_AGENT_NAME and GEO_FOUNDRY_AGENT_VERSION in geo-agent/.env. "
                "No mock fallback is enabled."
            ),
        }

    async def respond(
        self,
        project: Project,
        conversation: ProjectConversation,
        request: ProjectChatRequest,
    ) -> ProjectAgentReply:
        if project.foundry is None:
            raise Conflict(
                "Foundry chat is not configured for this project. Complete the manual setup checklist."
            )
        raise Conflict(
            "Foundry runtime integration is not enabled. Local measurement workflows remain available."
        )


class ProjectConversationStore:
    def __init__(self, repository: SQLAlchemyMeasurementRepository):
        self.repository = repository

    @staticmethod
    def _read_payload(row) -> ProjectConversation:
        return ProjectConversation.model_validate_json(row.payload)

    def _bound_context(
        self,
        project_id: str,
        run_id: str,
        owner: OwnerIdentity,
    ) -> BoundProjectContext:
        project = self.repository.get_project(project_id, owner)
        self.repository.get_project_run(project_id, run_id, owner)
        definition = self.repository.get_brand_definition(run_id, owner)
        return BoundProjectContext(
            name=project.name,
            domains=project.domains,
            locale=project.default_locale,
            goal=project.active_goal,
            brand_definition_version=(
                definition.definition_version if definition else None
            ),
            brand_definition_hash=(
                definition.definition_hash if definition else None
            ),
        )

    def _backfill_bound_context(
        self,
        conversation: ProjectConversation,
    ) -> ProjectConversation:
        if conversation.run_id is None or conversation.bound_project_context is not None:
            return conversation
        try:
            bound_context = self._bound_context(
                conversation.project_id,
                conversation.run_id,
                conversation.owner,
            )
        except NotFound:
            return conversation
        updated = conversation.model_copy(update={
            "bound_project_context": bound_context,
        })
        with self.repository.engine.begin() as connection:
            result = connection.execute(update(project_conversations).where(
                project_conversations.c.conversation_id == conversation.conversation_id,
                project_conversations.c.project_id == conversation.project_id,
                project_conversations.c.owner_key == conversation.owner.key,
                project_conversations.c.revision == conversation.revision,
            ).values(payload=updated.model_dump_json()))
            if result.rowcount != 1:
                return conversation
        return updated

    def create(
        self,
        project_id: str,
        owner: OwnerIdentity,
        run_id: str | None = None,
    ) -> ProjectConversation:
        self.repository.get_project(project_id, owner)
        bound_project_context = None
        if run_id is not None:
            bound_project_context = self._bound_context(project_id, run_id, owner)
        conversation = ProjectConversation(
            project_id=project_id,
            run_id=run_id,
            bound_project_context=bound_project_context,
            owner=owner,
        )
        with self.repository.engine.begin() as connection:
            connection.execute(insert(project_conversations).values(
                conversation_id=conversation.conversation_id,
                project_id=project_id,
                run_id=run_id,
                owner_key=owner.key,
                revision=conversation.revision,
                payload=conversation.model_dump_json(),
                created_at=conversation.created_at.isoformat(),
                updated_at=conversation.updated_at.isoformat(),
            ))
        return conversation

    def get(
        self,
        project_id: str,
        conversation_id: str,
        owner: OwnerIdentity,
    ) -> ProjectConversation:
        with self.repository.engine.connect() as connection:
            row = connection.execute(select(project_conversations).where(
                project_conversations.c.conversation_id == conversation_id,
                project_conversations.c.project_id == project_id,
                project_conversations.c.owner_key == owner.key,
            )).first()
            if row is None:
                raise NotFound("Project conversation not found")
            conversation = self._read_payload(row)
        return self._backfill_bound_context(conversation)

    def list(
        self,
        project_id: str,
        owner: OwnerIdentity,
    ) -> tuple[ProjectConversation, ...]:
        self.repository.get_project(project_id, owner)
        with self.repository.engine.connect() as connection:
            rows = connection.execute(select(project_conversations).where(
                project_conversations.c.project_id == project_id,
                project_conversations.c.owner_key == owner.key,
            )).all()
            conversations = sorted(
                (self._read_payload(row) for row in rows),
                key=lambda item: (item.updated_at, item.conversation_id),
                reverse=True,
            )
        return tuple(
            self._backfill_bound_context(conversation)
            for conversation in conversations
        )

    def find_workflow_by_run(
        self,
        run_id: str,
        owner: OwnerIdentity,
    ) -> ProjectConversation | None:
        with self.repository.engine.connect() as connection:
            rows = connection.execute(select(project_conversations).where(
                project_conversations.c.run_id == run_id,
                project_conversations.c.owner_key == owner.key,
            ).order_by(project_conversations.c.created_at.asc())).all()
        for row in rows:
            conversation = self._read_payload(row)
            if (
                conversation.measurement_workflow is not None
                and conversation.measurement_workflow.run_id == run_id
            ):
                return conversation
        return None

    def update_active(
        self,
        conversation: ProjectConversation,
        *,
        workflow: ProjectMeasurementWorkflow | None = None,
        run_id: str | None = None,
    ) -> ProjectConversation:
        effective_run_id = run_id if run_id is not None else conversation.run_id
        bound_context = conversation.bound_project_context
        if effective_run_id is not None and effective_run_id != conversation.run_id:
            bound_context = self._bound_context(
                conversation.project_id,
                effective_run_id,
                conversation.owner,
            )
        updated = conversation.model_copy(update={
            "run_id": effective_run_id,
            "bound_project_context": bound_context,
            "measurement_workflow": (
                workflow if workflow is not None else conversation.measurement_workflow
            ),
            "updated_at": utc_now(),
        })
        with self.repository.engine.begin() as connection:
            result = connection.execute(update(project_conversations).where(
                project_conversations.c.conversation_id == conversation.conversation_id,
                project_conversations.c.project_id == conversation.project_id,
                project_conversations.c.owner_key == conversation.owner.key,
                project_conversations.c.revision == conversation.revision,
            ).values(
                run_id=effective_run_id,
                payload=updated.model_dump_json(),
                updated_at=updated.updated_at.isoformat(),
            ))
            if result.rowcount != 1:
                raise Conflict("Conversation changed concurrently; reload it")
        return updated

    def set_workflow(
        self,
        conversation: ProjectConversation,
        workflow: ProjectMeasurementWorkflow,
    ) -> ProjectConversation:
        updated = conversation.model_copy(update={
            "revision": conversation.revision + 1,
            "measurement_workflow": workflow,
            "updated_at": utc_now(),
        })
        with self.repository.engine.begin() as connection:
            result = connection.execute(update(project_conversations).where(
                project_conversations.c.conversation_id == conversation.conversation_id,
                project_conversations.c.owner_key == conversation.owner.key,
                project_conversations.c.revision == conversation.revision,
            ).values(
                revision=updated.revision,
                payload=updated.model_dump_json(),
                updated_at=updated.updated_at.isoformat(),
            ))
            if result.rowcount != 1:
                raise Conflict("Conversation changed concurrently; reload it")
        return updated

    def clear_workflow(
        self,
        conversation: ProjectConversation,
    ) -> ProjectConversation:
        updated = conversation.model_copy(update={
            "measurement_workflow": None,
            "updated_at": utc_now(),
        })
        with self.repository.engine.begin() as connection:
            result = connection.execute(update(project_conversations).where(
                project_conversations.c.conversation_id == conversation.conversation_id,
                project_conversations.c.owner_key == conversation.owner.key,
                project_conversations.c.revision == conversation.revision,
            ).values(
                payload=updated.model_dump_json(),
                updated_at=updated.updated_at.isoformat(),
            ))
            if result.rowcount != 1:
                raise Conflict("Conversation changed concurrently; reload it")
        return updated

    def claim(
        self,
        project_id: str,
        conversation_id: str,
        owner: OwnerIdentity,
        request: ProjectChatRequest,
        *,
        origin: Literal["user", "workflow"] = "user",
    ) -> tuple[ProjectConversation, bool]:
        with self.repository.engine.begin() as connection:
            row = connection.execute(select(project_conversations).where(
                project_conversations.c.conversation_id == conversation_id,
                project_conversations.c.project_id == project_id,
                project_conversations.c.owner_key == owner.key,
            )).first()
            if row is None:
                raise NotFound("Project conversation not found")
            current = self._read_payload(row)
            if current.run_id is not None and current.bound_project_context is None:
                current = current.model_copy(update={
                    "bound_project_context": self._bound_context(
                        current.project_id,
                        current.run_id,
                        current.owner,
                    ),
                })
                connection.execute(update(project_conversations).where(
                    project_conversations.c.conversation_id == conversation_id,
                    project_conversations.c.revision == current.revision,
                ).values(payload=current.model_dump_json()))
            for turn in current.turns:
                if turn.idempotency_key == request.idempotency_key:
                    if (
                        turn.message != request.message
                        or turn.expected_revision != request.expected_revision
                        or turn.use_organisational_context != request.use_organisational_context
                    ):
                        raise Conflict("Idempotency key is bound to another chat request")
                    return current, False
            if current.revision != request.expected_revision:
                raise Conflict("Stale conversation revision; reload the conversation")
            if any(turn.status == "running" for turn in current.turns):
                raise Conflict("A conversation turn is already running")
            turn = ProjectConversationTurn(
                sequence=len(current.turns) + 1,
                message=request.message,
                idempotency_key=request.idempotency_key,
                expected_revision=request.expected_revision,
                use_organisational_context=request.use_organisational_context,
                origin=origin,
                status="running",
            )
            updated = current.model_copy(update={
                "revision": current.revision + 1,
                "title": request.message[:80] if not current.turns else current.title,
                "turns": (*current.turns, turn),
                "updated_at": utc_now(),
            })
            result = connection.execute(update(project_conversations).where(
                project_conversations.c.conversation_id == conversation_id,
                project_conversations.c.revision == current.revision,
            ).values(
                revision=updated.revision,
                payload=updated.model_dump_json(),
                updated_at=updated.updated_at.isoformat(),
            ))
            if result.rowcount != 1:
                raise Conflict("Conversation changed concurrently; reload it")
            return updated, True

    def finish(
        self,
        project_id: str,
        conversation_id: str,
        owner: OwnerIdentity,
        idempotency_key: str,
        reply: ProjectAgentReply | None,
        error: str | None,
    ) -> ProjectConversation:
        with self.repository.engine.begin() as connection:
            row = connection.execute(select(project_conversations).where(
                project_conversations.c.conversation_id == conversation_id,
                project_conversations.c.project_id == project_id,
                project_conversations.c.owner_key == owner.key,
            )).first()
            if row is None:
                raise NotFound("Project conversation not found")
            current = self._read_payload(row)
            if not current.turns:
                raise Conflict("Conversation has no active turn")
            turn = current.turns[-1]
            if turn.idempotency_key != idempotency_key or turn.status != "running":
                raise Conflict("Conversation turn is no longer current")
            completed_turn = turn.model_copy(update={
                "status": "failed" if error else "completed",
                "answer": reply.answer if reply is not None else None,
                "error": error,
                "citations": reply.citations if reply is not None else (),
                "provider_response_id": reply.provider_response_id if reply is not None else None,
                "mode": reply.mode if reply is not None else None,
                "agent_name": reply.agent_name if reply is not None else None,
                "agent_version": reply.agent_version if reply is not None else None,
                "context_hash": reply.context_hash if reply is not None else None,
                "usage": reply.usage if reply is not None else {},
                "completed_at": utc_now(),
            })
            updated = current.model_copy(update={
                "revision": current.revision + 1,
                "turns": (*current.turns[:-1], completed_turn),
                "updated_at": utc_now(),
            })
            result = connection.execute(update(project_conversations).where(
                project_conversations.c.conversation_id == conversation_id,
                project_conversations.c.revision == current.revision,
            ).values(
                revision=updated.revision,
                payload=updated.model_dump_json(),
                updated_at=updated.updated_at.isoformat(),
            ))
            if result.rowcount != 1:
                raise Conflict("Conversation changed concurrently; reload it")
            return updated


class ProjectChatService:
    def __init__(
        self,
        repository: SQLAlchemyMeasurementRepository,
        agent: ProjectAgent,
        workflow: ProjectChatWorkflowHandler | None = None,
    ):
        self.repository = repository
        self.store = ProjectConversationStore(repository)
        self.agent = agent
        self.workflow = workflow

    async def respond(
        self,
        project_id: str,
        conversation_id: str,
        owner: OwnerIdentity,
        request: ProjectChatRequest,
    ) -> ProjectConversation:
        conversation, claimed = self.store.claim(
            project_id,
            conversation_id,
            owner,
            request,
        )
        if not claimed:
            return conversation
        try:
            project = self.repository.get_project(project_id, owner)
            if project.archived:
                raise Conflict("This project is archived")
            reply = None
            if self.workflow is not None:
                conversation, reply = self.workflow.handle(
                    project, conversation, request,
                )
            if reply is None:
                reply = await self.agent.respond(project, conversation, request)
        except Conflict as failure:
            return self.store.finish(
                project_id,
                conversation_id,
                owner,
                request.idempotency_key,
                None,
                str(failure),
            )
        except asyncio.CancelledError:
            self.store.finish(
                project_id, conversation_id, owner, request.idempotency_key, None,
                "The request was interrupted and may have been processed. No automatic retry.",
            )
            raise
        except Exception:
            logger.error(
                "Project agent request failed for conversation %s",
                conversation_id,
            )
            return self.store.finish(
                project_id,
                conversation_id,
                owner,
                request.idempotency_key,
                None,
                "The agent request failed. Check the configured runtime and try again.",
            )
        return self.store.finish(
            project_id, conversation_id, owner, request.idempotency_key, reply, None,
        )
