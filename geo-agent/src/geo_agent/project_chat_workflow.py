import asyncio
import re
from urllib.parse import urlsplit

from geo_agent.contracts import Brief
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.goal_summary import GoalSummaryRequest, ProjectGoalSummaryService
from geo_agent.jobs import JobState, JobType, WorkflowJob
from geo_agent.measurement_workflow import (
    MeasurementRun,
    MeasurementState,
)
from geo_agent.project_measurements import ProjectMeasurementOrchestrator
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
DOMAIN_PATTERN = re.compile(
    r"(?<![\w@.-])(?:www\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+(?![\w.-])",
    re.IGNORECASE,
)
RUN_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
MEASUREMENT_PATTERN = re.compile(
    r"\b(?:analyse|analyze|audit|evaluate|evaluation|measure|measurement|run)\b",
    re.IGNORECASE,
)
CANCEL_PATTERN = re.compile(
    r"^\s*(?:cancel(?: that| this)?|stop(?: that| this)?|"
    r"never mind(?:,? cancel that)?|nevermind(?:,? cancel that)?|"
    r"forget it|different topic)\s*[.!]?\s*$",
    re.IGNORECASE,
)
CONFIRM_PATTERN = re.compile(
    r"^\s*(?:yes|y|confirm|confirmed|start|start it|go ahead|proceed|looks good)\s*[.!]?\s*$",
    re.IGNORECASE,
)
COMPARE_PATTERN = re.compile(r"\b(?:compare|comparison|versus|vs\.?)\b", re.IGNORECASE)
DOMAIN_SCOPE_PATTERN = re.compile(
    r"\b(?:domain[- ]level|whole domain|entire domain|any (?:of )?(?:my|our) urls?|"
    r"any (?:of )?(?:my|our) pages?|brand urls?|sitewide|site-wide)\b",
    re.IGNORECASE,
)
PAGE_SCOPE_PATTERN = re.compile(
    r"\b(?:specific page|landing page|page-level|this page)\b",
    re.IGNORECASE,
)


def _in_project(url: str, project: Project) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".").removeprefix("www.")
    return (
        parsed.scheme in {"http", "https"}
        and parsed.username is None
        and parsed.password is None
        and parsed.fragment == ""
        and any(
            host == domain.casefold().rstrip(".").removeprefix("www.")
            or host.endswith(
                f".{domain.casefold().rstrip('.').removeprefix('www.')}"
            )
            for domain in project.domains
        )
    )


def _objective(message: str, url: str | None) -> str | None:
    value = message
    if url is not None:
        value = value.replace(url, " ")
    value = MEASUREMENT_PATTERN.sub(" ", value)
    value = " ".join(value.strip(" .,:;-").split())
    value = re.sub(r"^(?:for|to)\s+", "", value, flags=re.IGNORECASE)
    return value if len(value) >= 8 else None


def _labelled_value(message: str, label: str) -> str | None:
    match = re.search(
        rf"\b{label}\s*(?:is|:|=)\s*(.+)$",
        message,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip(" .") if match else None


def _inferred_audience(message: str) -> str | None:
    patterns = (
        r"\bqueries?\s+related\s+to\s+([^.,;!?]+)",
        r"\b(?:aimed|targeted)\s+at\s+([^.,;!?]+)",
        r"\bintended\s+for\s+([^.,;!?]+)",
        r"\baudience\s+(?:is|of)\s+([^.,;!?]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _inferred_goal(message: str, target: str | None) -> str | None:
    lowered = message.casefold()
    if "crawlability" in lowered or re.search(r"\bcrawl(?:ing|able|ability)?\b", lowered):
        return "Evaluate crawlability"
    if re.search(r"\b(?:show up|appear|visibility|citation|cited)\b", lowered):
        return "Evaluate AI visibility"
    return _objective(message, target) if MEASUREMENT_PATTERN.search(message) else None


def _inferred_outcome(message: str) -> str | None:
    match = re.search(
        r"\b(?:i(?:'m| am)?\s+looking\s+to|i\s+want\s+to|we\s+want\s+to|"
        r"i\s+need\s+to|we\s+need\s+to)\s+(.+)$",
        message,
        flags=re.IGNORECASE,
    )
    if match:
        value = match.group(1).strip(" .")
        return value[0].upper() + value[1:] if value else None
    return None


def _target(message: str, project: Project) -> str | None:
    urls = URL_PATTERN.findall(message)
    if urls:
        return urls[0].rstrip(".,;")
    for candidate in DOMAIN_PATTERN.findall(message):
        url = f"https://{candidate.rstrip('.,;')}/"
        if _in_project(url, project):
            return url
    return None


def _root_target(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.path in {"", "/"} and not parsed.query


class ProjectMeasurementChatWorkflow:
    def __init__(
        self,
        repository,
        policy: MeasurementExecutionPolicy | None,
        goal_summaries: ProjectGoalSummaryService | None = None,
    ):
        self.repository = repository
        self.policy = policy
        self.store = ProjectConversationStore(repository)
        self.orchestrator = ProjectMeasurementOrchestrator(repository, policy)
        self.goal_summaries = goal_summaries

    def _workflow_reply(
        self,
        answer: str,
        run_id: str | None = None,
    ) -> ProjectAgentReply:
        citations = (
            (ProjectChatCitation(
                source_class="geo-evidence",
                geo_evidence_type="measurement-run",
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
        run_ids: tuple[str, ...],
    ) -> tuple[ProjectConversation, ProjectAgentReply | None]:
        for run_id in run_ids:
            try:
                self.repository.get_project_run(
                    project.project_id,
                    run_id,
                    project.owner,
                )
            except NotFound:
                return conversation, self._workflow_reply(
                    f"Run {run_id} was not found in this project."
                )
        comparison = run_ids if len(run_ids) > 1 else ()
        conversation = self.store.update_active(
            conversation,
            run_id=run_ids[0],
            linked_run_ids=run_ids,
            comparison_run_ids=comparison,
        )
        return conversation, None

    async def _next_question(
        self,
        project: Project,
        conversation: ProjectConversation,
        workflow: ProjectMeasurementWorkflow,
    ) -> tuple[ProjectConversation, ProjectAgentReply]:
        if workflow.url is None:
            workflow = workflow.model_copy(update={
                "status": "collecting",
                "pending_field": "target",
            })
            answer = "Which project URL or domain would you like to measure?"
        elif workflow.target_kind is None and _root_target(workflow.url):
            workflow = workflow.model_copy(update={
                "status": "collecting",
                "pending_field": "target-kind",
            })
            answer = (
                "Should this be a domain-level brand URL visibility check, to establish "
                "whether any of your owned URLs appear, or do you want to measure a "
                "specific page?"
            )
        elif workflow.goal is None:
            workflow = workflow.model_copy(update={
                "status": "collecting",
                "pending_field": "goal",
            })
            answer = "What is the overall goal of this measurement?"
        elif workflow.audience is None:
            workflow = workflow.model_copy(update={
                "status": "collecting",
                "pending_field": "audience",
            })
            answer = "Who is the intended audience for this page or domain?"
        elif workflow.desired_outcome is None:
            workflow = workflow.model_copy(update={
                "status": "collecting",
                "pending_field": "desired-outcome",
            })
            answer = (
                "What outcome do you want from the run, for example identifying "
                "citation gaps, AI visibility, or crawlability issues?"
            )
        else:
            if workflow.summarised_goal is None:
                summary_source = (
                    f"{workflow.raw_goal or workflow.goal}. "
                    f"Desired outcome: {workflow.desired_outcome}."
                )
                if workflow.target_kind == "domain":
                    summary_source = (
                        "Domain-level brand URL visibility check across owned URLs. "
                        + summary_source
                    )
                if self.goal_summaries is None:
                    workflow = workflow.model_copy(update={
                        "raw_goal": workflow.raw_goal or workflow.goal,
                        "summarised_goal": summary_source,
                        "goal_summary_fallback_used": True,
                    })
                else:
                    summary = await self.goal_summaries.summarize(
                        project,
                        project.owner,
                        GoalSummaryRequest(
                            raw_goal=summary_source,
                            url=workflow.url,
                            audience=workflow.audience,
                            desired_outcome=workflow.desired_outcome,
                            target_kind=workflow.target_kind,
                            idempotency_key=(
                                f"chat-{conversation.conversation_id}-"
                                f"{workflow.workflow_id}-goal-summary"
                            ),
                        ),
                    )
                    workflow = workflow.model_copy(update={
                        "raw_goal": workflow.raw_goal or workflow.goal,
                        "summarised_goal": summary.summary,
                        "goal_summary_operation_id": summary.operation_id,
                        "goal_summary_fallback_used": summary.fallback_used,
                    })
            workflow = workflow.model_copy(update={
                "status": "awaiting-confirmation",
                "pending_field": "confirmation",
            })
            scope = (
                "Domain-level brand URL visibility"
                if workflow.target_kind == "domain"
                else "Specific-page measurement"
            )
            answer = (
                "Please confirm this measurement setup:\n\n"
                f"- **Scope:** {scope}\n"
                f"- **Target:** {workflow.url}\n"
                f"- **Concise run goal:** {workflow.summarised_goal}\n"
                f"- **Original goal:** {workflow.raw_goal or workflow.goal}\n"
                f"- **Intended audience:** {workflow.audience}\n"
                f"- **Desired outcome:** {workflow.desired_outcome}\n\n"
                + (
                    "Foundry was unavailable, so the original goal is being used.\n\n"
                    if workflow.goal_summary_fallback_used else ""
                )
                +
                "Reply **confirm** to start, or tell me what you want to change."
            )
        conversation = self.store.update_active(conversation, workflow=workflow)
        return conversation, self._workflow_reply(answer)

    @staticmethod
    def _apply_pending_answer(
        workflow: ProjectMeasurementWorkflow,
        message: str,
        project: Project,
    ) -> ProjectMeasurementWorkflow:
        target = _target(message, project)
        updates = {}
        if target is not None:
            updates["url"] = target
            updates["target_kind"] = None if _root_target(target) else "page"
        if workflow.pending_field == "target-kind":
            if DOMAIN_SCOPE_PATTERN.search(message):
                updates["target_kind"] = "domain"
            elif PAGE_SCOPE_PATTERN.search(message):
                updates["target_kind"] = "page"
                if target is None:
                    updates["url"] = None
        elif workflow.pending_field == "goal":
            updates["goal"] = message.strip()
            updates["raw_goal"] = message.strip()
        elif workflow.pending_field == "audience":
            updates["audience"] = message.strip()
        elif workflow.pending_field == "desired-outcome":
            updates["desired_outcome"] = message.strip()

        labelled = {
            "goal": _labelled_value(message, "goal"),
            "audience": _labelled_value(message, "(?:intended )?audience"),
            "desired_outcome": _labelled_value(message, "(?:desired )?outcome"),
        }
        updates.update({key: value for key, value in labelled.items() if value})
        if any(
            key in updates
            for key in ("url", "target_kind", "goal", "audience", "desired_outcome")
        ):
            if "goal" in updates:
                updates["raw_goal"] = updates["goal"]
            updates.update({
                "summarised_goal": None,
                "goal_summary_operation_id": None,
                "goal_summary_fallback_used": False,
            })
        if workflow.pending_field in {"target", "target-kind"}:
            if DOMAIN_SCOPE_PATTERN.search(message):
                updates["target_kind"] = "domain"
            elif PAGE_SCOPE_PATTERN.search(message) and (
                target is not None or workflow.url is not None
            ):
                updates["target_kind"] = "page"
        return workflow.model_copy(update=updates)

    async def handle(
        self,
        project: Project,
        conversation: ProjectConversation,
        request: ProjectChatRequest,
    ) -> tuple[ProjectConversation, ProjectAgentReply | None]:
        run_ids = tuple(dict.fromkeys(
            run_id.lower() for run_id in RUN_PATTERN.findall(request.message)
        ))
        if run_ids:
            if (
                COMPARE_PATTERN.search(request.message)
                and conversation.run_id is not None
                and conversation.run_id not in run_ids
            ):
                run_ids = (conversation.run_id, *run_ids)
            return self._bind_existing_run(
                project,
                conversation,
                request,
                run_ids,
            )
        existing = conversation.measurement_workflow
        if existing is not None and existing.status in {
            "collecting",
            "awaiting-confirmation",
        }:
            if CANCEL_PATTERN.search(request.message):
                conversation = self.store.clear_workflow(conversation)
                return conversation, self._workflow_reply(
                    "Measurement setup was cancelled. What would you like to discuss instead?"
                )
            existing = self._apply_pending_answer(existing, request.message, project)
            if (
                existing.status == "awaiting-confirmation"
                and existing.pending_field == "confirmation"
                and CONFIRM_PATTERN.match(request.message)
            ):
                pass
            else:
                return await self._next_question(project, conversation, existing)
        elif existing is not None:
            existing = None

        target = _target(request.message, project)
        measurement_requested = bool(
            target or existing or MEASUREMENT_PATTERN.search(request.message)
        )
        if not measurement_requested:
            return conversation, None
        if self.policy is None:
            raise Conflict("Measurement execution is not configured for this project")

        if existing is None:
            goal = _labelled_value(request.message, "goal") or _inferred_goal(
                request.message,
                target,
            )
            audience = _labelled_value(
                request.message,
                "(?:intended )?audience",
            ) or _inferred_audience(request.message)
            desired_outcome = _labelled_value(
                request.message,
                "(?:desired )?outcome",
            ) or _inferred_outcome(request.message)
            target_kind = (
                "domain"
                if target is not None and DOMAIN_SCOPE_PATTERN.search(request.message)
                else "page"
                if target is not None and not _root_target(target)
                else None
            )
            existing = ProjectMeasurementWorkflow(
                status="collecting",
                target_kind=target_kind,
                url=target,
                goal=goal,
                raw_goal=goal,
                audience=audience,
                desired_outcome=desired_outcome,
                objective=goal,
                source_idempotency_key=request.idempotency_key,
            )

        url = existing.url
        if url is not None and not _in_project(url, project):
            workflow = existing.model_copy(update={
                "status": "collecting",
                "url": None,
                "target_kind": None,
                "pending_field": "target",
                "error": "The supplied page is outside the project domains.",
            })
            conversation = self.store.update_active(conversation, workflow=workflow)
            return conversation, self._workflow_reply(
                f"Please provide an HTTP(S) page or domain within {', '.join(project.domains)}."
            )

        if existing.status != "awaiting-confirmation":
            return await self._next_question(project, conversation, existing)
        if not CONFIRM_PATTERN.match(request.message):
            return await self._next_question(project, conversation, existing)

        goal = existing.summarised_goal or existing.raw_goal or existing.goal
        brief = Brief(
            url=url,
            audience=existing.audience,
            goal=goal,
            locale=project.default_locale,
        )
        _, run = self.orchestrator.start(
            project,
            brief,
            idempotency_key=(
                f"chat-{conversation.conversation_id}-"
                f"{existing.source_idempotency_key}-prepare"
            ),
        )
        workflow = existing.model_copy(update={
            "status": "preparing",
            "url": str(brief.url),
            "objective": brief.goal,
            "pending_field": None,
            "run_id": run.run_id,
            "error": None,
        })
        conversation = self.store.update_active(
            conversation,
            workflow=workflow,
            run_id=run.run_id,
            linked_run_ids=(run.run_id,),
            comparison_run_ids=(),
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
        workflow = next((
            item for item in conversation.measurement_workflows
            if item.run_id == run.run_id
        ), None) if conversation else None
        if conversation is None or workflow is None:
            return
        if job.state == JobState.FAILED:
            self.store.set_workflow(conversation, workflow.model_copy(update={
                "status": "failed",
                "error": f"The {job.job_type.value} job failed with {job.error_code or 'an unknown error'}.",
            }))
            return
        if job.job_type == JobType.PREPARE and run.state in {
            MeasurementState.QUEUED,
            MeasurementState.EVALUATING,
            MeasurementState.READY,
            MeasurementState.PARTIAL,
            MeasurementState.FAILED,
        }:
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
        latest_workflow = next((
            item for item in latest.measurement_workflows
            if item.workflow_id == workflow.workflow_id
        ), workflow)
        if latest_workflow.status == "completed":
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
            reply = asyncio.run(agent.respond(
                project,
                claimed.model_copy(update={
                    "comparison_run_ids": (run.run_id,),
                }),
                request,
            ))
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
