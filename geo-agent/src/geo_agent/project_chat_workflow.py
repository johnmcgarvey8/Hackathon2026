import asyncio
import re
from urllib.parse import urlsplit

from geo_agent.contracts import Brief
from geo_agent.evaluation_workflow import EvaluationRequest
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import JobService, JobState, JobType, WorkflowJob
from geo_agent.measurement_workflow import (
    MeasurementCoordinator,
    MeasurementRun,
    MeasurementState,
)
from geo_agent.preparation import PreparationRequest
from geo_agent.project_chat import (
    ProjectAgent,
    ProjectAgentReply,
    ProjectChatCitation,
    ProjectChatRequest,
    ProjectConversation,
    ProjectConversationStore,
    ProjectMeasurementWorkflow,
)
from geo_agent.projects import Project
from geo_agent.workflow import Conflict, NotFound


URL_PATTERN = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
RUN_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
MEASUREMENT_PATTERN = re.compile(
    r"\b(?:analyse|analyze|audit|evaluate|evaluation|measure|measurement|run)\b",
    re.IGNORECASE,
)
CANCEL_PATTERN = re.compile(
    r"\b(?:cancel|stop|never mind|nevermind|forget it|different topic)\b",
    re.IGNORECASE,
)


def _in_project(url: str, project: Project) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme in {"http", "https"}
        and parsed.username is None
        and parsed.password is None
        and parsed.fragment == ""
        and any(host == domain or host.endswith(f".{domain}") for domain in project.domains)
    )


def _objective(message: str, url: str | None) -> str | None:
    value = message
    if url is not None:
        value = value.replace(url, " ")
    value = MEASUREMENT_PATTERN.sub(" ", value)
    value = " ".join(value.strip(" .,:;-").split())
    return value if len(value) >= 8 else None


class ProjectMeasurementChatWorkflow:
    def __init__(
        self,
        repository,
        policy: MeasurementExecutionPolicy | None,
    ):
        self.repository = repository
        self.policy = policy
        self.store = ProjectConversationStore(repository)
        self.coordinator = MeasurementCoordinator(repository)
        self.jobs = JobService(repository)

    def _workflow_reply(
        self,
        answer: str,
        run_id: str | None = None,
    ) -> ProjectAgentReply:
        citations = (
            (ProjectChatCitation(
                source_class="geo-evidence",
                source_id=run_id,
                title="Project measurement run",
            ),)
            if run_id else ()
        )
        return ProjectAgentReply(
            answer=answer,
            citations=citations,
            mode="workflow",
            agent_name="GEO workflow coordinator",
        )

    def _bind_existing_run(
        self,
        project: Project,
        conversation: ProjectConversation,
        request: ProjectChatRequest,
        run_id: str,
    ) -> tuple[ProjectConversation, ProjectAgentReply | None]:
        try:
            self.repository.get_project_run(project.project_id, run_id, project.owner)
        except NotFound:
            return conversation, self._workflow_reply(
                f"Run {run_id} was not found in this project."
            )
        if conversation.run_id is not None and conversation.run_id != run_id:
            return conversation, self._workflow_reply(
                "This conversation is already bound to another run. Start a new chat and paste the run ID there."
            )
        conversation = self.store.update_active(conversation, run_id=run_id)
        return conversation, None

    def handle(
        self,
        project: Project,
        conversation: ProjectConversation,
        request: ProjectChatRequest,
    ) -> tuple[ProjectConversation, ProjectAgentReply | None]:
        run_ids = RUN_PATTERN.findall(request.message)
        if run_ids and conversation.measurement_workflow is None:
            return self._bind_existing_run(
                project, conversation, request, run_ids[0].lower(),
            )
        if conversation.run_id is not None:
            return conversation, None

        existing = conversation.measurement_workflow
        urls = URL_PATTERN.findall(request.message)
        if existing is not None and existing.status == "collecting":
            if CANCEL_PATTERN.search(request.message):
                conversation = self.store.clear_workflow(conversation)
                return conversation, self._workflow_reply(
                    "Measurement setup was cancelled. What would you like to discuss instead?"
                )
            if existing.url is None and not urls and not MEASUREMENT_PATTERN.search(request.message):
                conversation = self.store.clear_workflow(conversation)
                return conversation, None
        measurement_requested = bool(urls or existing or MEASUREMENT_PATTERN.search(request.message))
        if not measurement_requested:
            return conversation, None
        if self.policy is None:
            raise Conflict("Measurement execution is not configured for this project")

        url = urls[0].rstrip(".,;") if urls else existing.url if existing else None
        if url is not None and not _in_project(url, project):
            workflow = ProjectMeasurementWorkflow(
                status="collecting",
                url=None,
                objective=existing.objective if existing else None,
                source_idempotency_key=(
                    existing.source_idempotency_key if existing else request.idempotency_key
                ),
                error="The supplied page is outside the project domains.",
            )
            conversation = self.store.update_active(conversation, workflow=workflow)
            return conversation, self._workflow_reply(
                f"Please provide an exact HTTP(S) page within {', '.join(project.domains)}."
            )

        stated_objective = _objective(request.message, url)
        objective = (
            stated_objective
            or (existing.objective if existing else None)
            or project.active_goal
        )
        source_key = existing.source_idempotency_key if existing else request.idempotency_key
        if url is None or objective is None:
            workflow = ProjectMeasurementWorkflow(
                status="collecting",
                url=url,
                objective=objective,
                source_idempotency_key=source_key,
            )
            conversation = self.store.update_active(conversation, workflow=workflow)
            missing = "exact page URL" if url is None else "measurement objective"
            return conversation, self._workflow_reply(
                f"I can start the measurement as soon as you provide the {missing}."
            )

        brief = Brief(
            url=url,
            audience=objective,
            goal=objective,
            locale=project.default_locale,
        )
        self.policy.validate_brief(brief, project_bound=True)
        run = self.repository.create(
            project.owner,
            brief=brief,
            project_id=project.project_id,
        )
        workflow = ProjectMeasurementWorkflow(
            status="preparing",
            url=str(brief.url),
            objective=objective,
            run_id=run.run_id,
            source_idempotency_key=source_key,
        )
        conversation = self.store.update_active(
            conversation, workflow=workflow, run_id=run.run_id,
        )
        self.jobs.enqueue(
            run.run_id,
            project.owner,
            run.revision,
            JobType.PREPARE,
            f"chat-{conversation.conversation_id}-{source_key}-prepare",
            PreparationRequest(
                brief=brief,
                confirm_preparation_calls=True,
                project_bound=True,
            ),
        )
        return conversation, self._workflow_reply(
            f"Run {run.run_id} has started for {brief.url}. "
            "I will add an evidence-grounded summary to this conversation when it completes.",
            run.run_id,
        )

    def reconcile_job(
        self,
        job: WorkflowJob,
        run: MeasurementRun,
        agent: ProjectAgent,
    ) -> None:
        conversation = self.store.find_workflow_by_run(run.run_id, run.owner)
        workflow = conversation.measurement_workflow if conversation else None
        if conversation is None or workflow is None or workflow.run_id != run.run_id:
            return
        if job.state == JobState.FAILED:
            self.store.set_workflow(conversation, workflow.model_copy(update={
                "status": "failed",
                "error": f"The {job.job_type.value} job failed with {job.error_code or 'an unknown error'}.",
            }))
            return
        if job.job_type == JobType.PREPARE and run.state == MeasurementState.AWAITING_APPROVAL:
            try:
                approved = self.coordinator.approve(
                    run.run_id,
                    run.owner,
                    run.revision,
                    run.inputs.approval_hash,
                )
            except Conflict:
                approved = self.repository.get_project_run(
                    conversation.project_id,
                    run.run_id,
                    run.owner,
                )
                if approved.state not in {
                    MeasurementState.QUEUED,
                    MeasurementState.EVALUATING,
                    MeasurementState.READY,
                    MeasurementState.PARTIAL,
                    MeasurementState.FAILED,
                }:
                    raise
                latest = self.store.get(
                    conversation.project_id,
                    conversation.conversation_id,
                    conversation.owner,
                )
                self.store.set_workflow(latest, workflow.model_copy(update={
                    "status": (
                        "completed"
                        if approved.state in {
                            MeasurementState.READY,
                            MeasurementState.PARTIAL,
                            MeasurementState.FAILED,
                        }
                        else "evaluating"
                    ),
                    "error": None,
                }))
                return
            _, queued = self.jobs.enqueue(
                approved.run_id,
                approved.owner,
                approved.revision,
                JobType.EVALUATE,
                f"chat-{conversation.conversation_id}-{workflow.source_idempotency_key}-evaluate",
                EvaluationRequest(
                    confirm_evaluation_calls=True,
                    include_recommendations=True,
                ),
            )
            latest = self.store.get(
                conversation.project_id,
                conversation.conversation_id,
                conversation.owner,
            )
            self.store.set_workflow(latest, workflow.model_copy(update={
                "status": "evaluating",
                "error": None,
            }))
            return
        if job.job_type != JobType.EVALUATE or run.state not in {
            MeasurementState.READY,
            MeasurementState.PARTIAL,
            MeasurementState.FAILED,
        }:
            return
        latest = self.store.get(
            conversation.project_id,
            conversation.conversation_id,
            conversation.owner,
        )
        if latest.measurement_workflow.status == "completed":
            return
        request = ProjectChatRequest(
            message=(
                "Provide the key evidence-backed insights from this completed measurement. "
                "Separate observed evidence, citation performance, recommendations, and limitations."
            ),
            expected_revision=latest.revision,
            idempotency_key=f"chat-{run.run_id}-automatic-insights",
        )
        try:
            claimed, created = self.store.claim(
                latest.project_id,
                latest.conversation_id,
                latest.owner,
                request,
                origin="workflow",
            )
        except Conflict:
            refreshed = self.store.get(
                latest.project_id,
                latest.conversation_id,
                latest.owner,
            )
            self.store.set_workflow(refreshed, workflow.model_copy(update={
                "status": "completed",
                "error": (
                    "The run completed while another chat turn was active. "
                    "That turn can use the saved run evidence."
                ),
            }))
            return
        if not created:
            return
        try:
            project = self.repository.get_project(latest.project_id, latest.owner)
            reply = asyncio.run(agent.respond(project, claimed, request))
        except Exception:
            failed = self.store.finish(
                latest.project_id,
                latest.conversation_id,
                latest.owner,
                request.idempotency_key,
                None,
                "Automatic insight generation failed. Ask about this run to try a new response.",
            )
            self.store.set_workflow(failed, workflow.model_copy(update={
                "status": "failed",
                "error": "Automatic insight generation failed. Ask about this run to try again.",
            }))
            return
        finished = self.store.finish(
            latest.project_id,
            latest.conversation_id,
            latest.owner,
            request.idempotency_key,
            reply,
            None,
        )
        self.store.set_workflow(finished, workflow.model_copy(update={
            "status": "completed",
            "error": None,
        }))
