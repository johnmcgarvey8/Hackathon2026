from datetime import datetime
from collections.abc import Callable
from typing import Any, Literal, Protocol

from geo_agent.agent_access import (
    AgentAuthorizationStatus,
    AgentExecutionAuthorization,
    AgentPrincipal,
    AgentScope,
    ExecutionStage,
)
from geo_agent.artifact_storage import (
    ArtifactKind,
    ArtifactRepository,
    ArtifactService,
)
from geo_agent.contracts import Brief, MeasurementInputs, QueryPair, QueryPlan, digest, utc_now
from geo_agent.evaluation import measurement_scores
from geo_agent.evidence_assessment import BrandDefinition, build_evidence_assessment
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.jobs import JobRepository, JobService, JobType, OperationClaimState
from geo_agent.measurement_views import (
    CursorCodec,
    artifact_view,
    job_view,
    parse_cursor_datetime,
    run_summary_view,
    run_view,
)
from geo_agent.measurement_workflow import (
    MeasurementRepository,
    MeasurementRun,
    MeasurementRunSummary,
    MeasurementState,
    OwnerIdentity,
)
from geo_agent.preparation import PreparationRequest
from geo_agent.recommendations import build_content_strategy
from geo_agent.evaluation_workflow import EvaluationRequest
from geo_agent.workflow import Conflict, NotFound


class MeasurementApplicationRepository(
    MeasurementRepository,
    JobRepository,
    ArtifactRepository,
    Protocol,
):
    def issue_execution_authorization(
        self,
        *,
        run_id: str,
        owner: OwnerIdentity,
        principal_id: str,
        stage: ExecutionStage,
        expected_revision: int,
        input_hash: str | None,
        policy: MeasurementExecutionPolicy,
        operation_ceiling: int,
        lifetime_seconds: int,
    ) -> AgentExecutionAuthorization: ...

    def list_execution_authorizations(
        self,
        run_id: str,
        owner: OwnerIdentity,
        principal_id: str,
    ) -> tuple[AgentExecutionAuthorization, ...]: ...

    def list_operation_checkpoints(
        self,
        run_id: str,
        owner: OwnerIdentity,
        job_id: str | None = None,
    ) -> tuple[Any, ...]: ...

    def worker_health(self) -> dict[str, str | None]: ...

    def queue_status(self, owner: OwnerIdentity) -> dict[str, int]: ...

    def reserve_export_request(
        self,
        owner: OwnerIdentity,
        idempotency_key: str,
        request_hash: str,
    ) -> Any | None: ...

    def bind_export_request(
        self,
        owner: OwnerIdentity,
        idempotency_key: str,
        request_hash: str,
        artifact: Any,
    ) -> Any: ...


class MeasurementApplicationService:
    schema_version = "geo-mcp-result/v1"
    default_limitations = (
        "Profiles are controlled evidence-packet simulations, not consumer assistant measurements.",
        "Citations do not prove claim support or causal ranking impact.",
        "Failed work lowers coverage; queued work is not a completed measurement.",
    )

    def __init__(
        self,
        repository: MeasurementApplicationRepository,
        policy: MeasurementExecutionPolicy,
        artifact_service: ArtifactService,
        cursor_codec: CursorCodec,
        *,
        human_base_url: str = "http://127.0.0.1:8088",
        pending_job_notifier: Callable[[], None] | None = None,
    ):
        self.repository = repository
        self.policy = policy
        self.artifacts = artifact_service
        self.cursor_codec = cursor_codec
        self.human_base_url = human_base_url.rstrip("/")
        self.pending_job_notifier = pending_job_notifier
        self.jobs = JobService(repository)

    def human_url(self, run_id: str) -> str:
        return f"{self.human_base_url}/measurements?run={run_id}"

    def _envelope(
        self,
        status: str,
        data: Any,
        *,
        run_id: str | None = None,
        job_id: str | None = None,
        run_revision: int | None = None,
        next_actions: tuple[str, ...] = (),
        limitations: tuple[str, ...] | None = None,
        continuation: str | None = None,
        truncated: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "status": status,
            "data": data,
            "next_actions": list(next_actions),
            "limitations": list(
                self.default_limitations if limitations is None else limitations
            ),
        }
        if run_id is not None:
            result["run_id"] = run_id
        if job_id is not None:
            result["job_id"] = job_id
        if run_revision is not None:
            result["run_revision"] = run_revision
        if continuation is not None:
            result["continuation"] = continuation
        if truncated:
            result["truncated"] = True
        return result

    @staticmethod
    def _next_actions(run: MeasurementRun) -> tuple[str, ...]:
        if run.state == MeasurementState.DRAFT:
            return ("authorize_preparation_in_human_ui", "geo_prepare_run")
        if run.state == MeasurementState.PREPARING:
            return ("geo_get_progress",)
        if run.state == MeasurementState.AWAITING_APPROVAL and run.approval is None:
            return ("geo_get_query_plan", "review_and_approve_queries_in_human_ui")
        if run.state == MeasurementState.AWAITING_APPROVAL:
            return ("authorize_evaluation_in_human_ui", "geo_start_measurement")
        if run.state in {
            MeasurementState.QUEUED,
            MeasurementState.EVALUATING,
            MeasurementState.RECOMMENDING,
        }:
            return ("geo_get_progress", "geo_cancel_job")
        if run.measurement is not None:
            return (
                "geo_get_results",
                "geo_get_assessment",
                "geo_get_content_strategy",
                "geo_create_export",
            )
        return ("geo_get_run",)

    def capabilities(self, principal: AgentPrincipal) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        queue = self.repository.queue_status(principal.owner)
        return self._envelope(
            "ready",
            {
                "execution_mode": self.policy.execution_mode,
                "policy_id": self.policy.policy_id,
                "allowed_domains": list(self.policy.allowed_domains),
                "locale": self.policy.locale,
                "profiles": [
                    {
                        "profile_id": profile.profile_id,
                        "provider": profile.provider,
                        "prompt_version": profile.prompt_version,
                    }
                    for profile in self.policy.profiles
                ],
                "limits": {
                    "active_jobs_global": 1,
                    "queued_jobs_global": 20,
                    "queued_jobs_per_owner": 4,
                    "preparation_operations": 3,
                    "evaluation_operations": 5 + 5 * len(self.policy.profiles),
                    "automatic_retries": False,
                    "default_payload_bytes": 16384,
                    "progress_payload_bytes": 8192,
                    "detail_payload_bytes": 65536,
                },
                "queue": queue,
                "worker": self.repository.worker_health(),
                "human_approval_required": True,
                "agent_scopes": [scope.value for scope in principal.scopes],
            },
        )

    def list_runs(
        self,
        principal: AgentPrincipal,
        *,
        state: str = "",
        cursor: str = "",
        page_size: int = 10,
    ) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        if not 1 <= page_size <= 50:
            raise Conflict("Run page size must be between 1 and 50")
        state_filter = MeasurementState(state) if state else None
        before_updated_at = None
        before_run_id = None
        if cursor:
            cursor_data = self.cursor_codec.decode(cursor, "runs", principal.owner.key)
            if cursor_data.get("state", "") != state:
                raise Conflict("Cursor does not match the run filter")
            before_updated_at = parse_cursor_datetime(cursor_data.get("updated_at"))
            before_run_id = cursor_data.get("run_id")
            if not isinstance(before_run_id, str):
                raise Conflict("Cursor is invalid")
        records = self.repository.list_run_summaries(
            principal.owner,
            page_size + 1,
            state=state_filter,
            before_updated_at=before_updated_at,
            before_run_id=before_run_id,
        )
        page = records[:page_size]
        continuation = None
        if len(records) > page_size and page:
            last = page[-1]
            continuation = self.cursor_codec.encode(
                "runs",
                principal.owner.key,
                {
                    "state": state,
                    "updated_at": last.updated_at.isoformat(),
                    "run_id": last.run_id,
                },
            )
        return self._envelope(
            "ready",
            {
                "items": [
                    run_summary_view(item, self.human_url(item.run_id))
                    for item in page
                ],
                "page_size": len(page),
            },
            next_actions=("geo_get_run",),
            continuation=continuation,
        )

    def create_run(
        self,
        principal: AgentPrincipal,
        *,
        url: str,
        audience: str,
        goal: str,
        locale: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        principal.require(AgentScope.WRITE)
        brief = Brief(url=url, audience=audience, goal=goal, locale=locale)
        self.policy.validate_brief(brief)
        run = self.repository.create_idempotent(
            principal.owner,
            brief,
            None,
            idempotency_key,
        )
        return self._envelope(
            run.state.value,
            {
                "run": run_summary_view(
                    MeasurementRunSummary.from_run(run),
                    self.human_url(run.run_id),
                ),
                "provider_calls_started_by_this_tool": 0,
            },
            run_id=run.run_id,
            run_revision=run.revision,
            next_actions=self._next_actions(run),
        )

    def create_human_run(
        self,
        owner: OwnerIdentity,
        *,
        brief: Brief,
        brand_definition: BrandDefinition | None = None,
    ) -> MeasurementRun:
        self.policy.validate_brief(brief)
        return self.repository.create(
            owner,
            brief=brief,
            brand_definition=brand_definition,
        )

    def _usable_authorizations(
        self,
        run: MeasurementRun,
        principal: AgentPrincipal,
    ) -> list[dict[str, Any]]:
        now = utc_now()
        records = self.repository.list_execution_authorizations(
            run.run_id,
            principal.owner,
            principal.principal_id,
        )
        return [
            AgentAuthorizationStatus.from_record(record).model_dump(mode="json")
            for record in records
            if (
                record.consumed_at is None
                and record.expires_at > now
                and record.run_revision == run.revision
                and (
                    record.stage != ExecutionStage.EVALUATE
                    or (
                        run.inputs is not None
                        and record.input_hash == run.inputs.approval_hash
                    )
                )
            )
        ]

    def get_run(self, principal: AgentPrincipal, run_id: str) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        run = self.repository.get(run_id, principal.owner)
        jobs = self.repository.list_run_jobs(run_id, principal.owner, 10)
        return self._envelope(
            run.state.value,
            {
                "run": run_summary_view(
                    MeasurementRunSummary.from_run(run),
                    self.human_url(run.run_id),
                ),
                "approval_hash": run.inputs.approval_hash if run.inputs is not None else None,
                "jobs": [job_view(job) for job in jobs],
                "events": [
                    event.model_dump(mode="json")
                    for event in run.events[-20:]
                ],
                "execution_authorizations": self._usable_authorizations(run, principal),
            },
            run_id=run.run_id,
            run_revision=run.revision,
            next_actions=self._next_actions(run),
        )

    def issue_execution_authorization(
        self,
        *,
        owner: OwnerIdentity,
        run_id: str,
        principal_id: str,
        stage: ExecutionStage,
        expected_revision: int,
        input_hash: str | None,
        lifetime_seconds: int,
    ) -> AgentExecutionAuthorization:
        ceiling = 3 if stage == ExecutionStage.PREPARE else 5 + 5 * len(self.policy.profiles)
        return self.repository.issue_execution_authorization(
            run_id=run_id,
            owner=owner,
            principal_id=principal_id,
            stage=stage,
            expected_revision=expected_revision,
            input_hash=input_hash,
            policy=self.policy,
            operation_ceiling=ceiling,
            lifetime_seconds=lifetime_seconds,
        )

    def prepare_run(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        expected_revision: int,
        idempotency_key: str,
        execution_authorization_id: str,
    ) -> dict[str, Any]:
        principal.require(AgentScope.EXECUTE)
        run = self.repository.get(run_id, principal.owner)
        if run.brief is None:
            raise Conflict("Preparation requires a submitted brief")
        job, updated = self.jobs.enqueue(
            run_id,
            principal.owner,
            expected_revision,
            JobType.PREPARE,
            idempotency_key,
            PreparationRequest(
                brief=run.brief,
                confirm_preparation_calls=True,
            ),
            execution_principal_id=principal.principal_id,
            execution_authorization_id=execution_authorization_id,
            policy_id=self.policy.policy_id,
            policy_hash=self.policy.policy_hash,
            operation_ceiling=3,
        )
        return self._envelope(
            "queued",
            {
                "job": job_view(job),
                "provider_calls_started_by_this_tool": 0,
                "poll_after_seconds": 5,
            },
            run_id=run_id,
            run_revision=updated.revision,
            job_id=job.job_id,
            next_actions=("geo_get_progress",),
        )

    def prepare_human_run(
        self,
        owner: OwnerIdentity,
        *,
        run_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> tuple[Any, MeasurementRun]:
        run = self.repository.get(run_id, owner)
        if run.brief is None:
            raise Conflict("Preparation requires a submitted brief")
        return self.jobs.enqueue(
            run_id,
            owner,
            expected_revision,
            JobType.PREPARE,
            idempotency_key,
            PreparationRequest(
                brief=run.brief,
                confirm_preparation_calls=True,
            ),
            policy_id=self.policy.policy_id,
            policy_hash=self.policy.policy_hash,
            operation_ceiling=3,
        )

    def _text_page(
        self,
        principal: AgentPrincipal,
        *,
        kind: str,
        binding: dict[str, Any],
        text: str,
        cursor: str,
        chunk_size: int,
    ) -> tuple[str, str | None, bool]:
        offset = 0
        if cursor:
            data = self.cursor_codec.decode(cursor, kind, principal.owner.key)
            for key, value in binding.items():
                if data.get(key) != value:
                    raise Conflict("Cursor does not match the requested content")
            raw_offset = data.get("offset")
            if not isinstance(raw_offset, int) or raw_offset < 0:
                raise Conflict("Cursor is invalid")
            offset = raw_offset
        chunk = text[offset:offset + chunk_size]
        next_offset = offset + len(chunk)
        truncated = next_offset < len(text)
        continuation = (
            self.cursor_codec.encode(
                kind,
                principal.owner.key,
                {**binding, "offset": next_offset},
            )
            if truncated
            else None
        )
        return chunk, continuation, truncated

    def _sequence_page(
        self,
        principal: AgentPrincipal,
        *,
        kind: str,
        binding: dict[str, Any],
        items: list[Any],
        cursor: str,
        page_size: int,
    ) -> tuple[list[Any], str | None, bool]:
        if not 1 <= page_size <= 20:
            raise Conflict("Detail page size must be between 1 and 20")
        offset = 0
        if cursor:
            data = self.cursor_codec.decode(cursor, kind, principal.owner.key)
            for key, value in binding.items():
                if data.get(key) != value:
                    raise Conflict("Cursor does not match the requested detail")
            raw_offset = data.get("offset")
            if not isinstance(raw_offset, int) or raw_offset < 0:
                raise Conflict("Cursor is invalid")
            offset = raw_offset
        page = items[offset:offset + page_size]
        next_offset = offset + len(page)
        truncated = next_offset < len(items)
        continuation = (
            self.cursor_codec.encode(
                kind,
                principal.owner.key,
                {**binding, "offset": next_offset},
            )
            if truncated
            else None
        )
        return page, continuation, truncated

    def get_preparation(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        section: Literal["summary", "page", "findings"],
        cursor: str = "",
    ) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        run = self.repository.get(run_id, principal.owner)
        if run.inputs is None:
            status = (
                "unavailable"
                if run.state in {
                    MeasurementState.CANCELLED,
                    MeasurementState.FAILED,
                    MeasurementState.NEEDS_REVIEW,
                }
                else "pending"
            )
            return self._envelope(
                status,
                {"section": section},
                run_id=run_id,
                run_revision=run.revision,
                next_actions=("geo_get_progress",),
            )
        if section == "summary":
            data = {
                "page": {
                    "url": str(run.inputs.snapshot.url),
                    "title": run.inputs.snapshot.title,
                    "provenance": run.inputs.snapshot.provenance.value,
                    "content_hash": run.inputs.snapshot.content_hash,
                    "content_characters": len(run.inputs.snapshot.content),
                },
                "query_count": len(run.inputs.query_plan.queries),
                "profile_ids": [profile.profile_id for profile in run.inputs.profiles],
                "has_page_analysis": run.page_analysis is not None,
            }
            continuation = None
            truncated = False
        elif section == "page":
            chunk, continuation, truncated = self._text_page(
                principal,
                kind="preparation-page",
                binding={
                    "run_id": run_id,
                    "content_hash": run.inputs.snapshot.content_hash,
                },
                text=run.inputs.snapshot.content,
                cursor=cursor,
                chunk_size=12000,
            )
            data = {
                "url": str(run.inputs.snapshot.url),
                "title": run.inputs.snapshot.title,
                "provenance": run.inputs.snapshot.provenance.value,
                "content": chunk,
            }
        else:
            data = {
                "page_analysis": (
                    run.page_analysis.model_dump(mode="json")
                    if run.page_analysis is not None
                    else None
                )
            }
            continuation = None
            truncated = False
        return self._envelope(
            "ready",
            data,
            run_id=run_id,
            run_revision=run.revision,
            next_actions=("geo_get_query_plan",),
            continuation=continuation,
            truncated=truncated,
        )

    def get_query_plan(
        self,
        principal: AgentPrincipal,
        run_id: str,
        query_id: str = "",
    ) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        run = self.repository.get(run_id, principal.owner)
        if run.inputs is None:
            return self._envelope(
                "pending",
                {"queries": []},
                run_id=run_id,
                run_revision=run.revision,
                next_actions=("geo_get_progress",),
            )
        queries = sorted(
            run.inputs.query_plan.queries,
            key=lambda item: item.priority,
        )
        if query_id:
            selected = next(
                (query for query in queries if query.query_id == query_id),
                None,
            )
            if selected is None:
                raise NotFound("Query pair not found")
            query_payloads = [selected.model_dump(mode="json")]
        else:
            query_payloads = [
                {
                    **query.model_dump(mode="json", exclude={"evidence"}),
                    "evidence": [
                        {
                            "evidence_id": item.evidence_id,
                            "quote_preview": item.quote[:120],
                            "quote_characters": len(item.quote),
                        }
                        for item in query.evidence
                    ],
                }
                for query in queries
            ]
        return self._envelope(
            "ready",
            {
                "queries": query_payloads,
                "detail": "query" if query_id else "summary",
                "input_hash": run.inputs.approval_hash,
                "human_approval_status": "approved" if run.approval is not None else "pending",
            },
            run_id=run_id,
            run_revision=run.revision,
            next_actions=self._next_actions(run),
        )

    def update_query_plan(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        expected_revision: int,
        queries: tuple[QueryPair, ...],
    ) -> dict[str, Any]:
        principal.require(AgentScope.WRITE)
        run = self.repository.get(run_id, principal.owner)
        if run.inputs is None:
            raise Conflict("Query revision requires prepared measurement inputs")
        plan = QueryPlan(queries=queries)
        inputs = MeasurementInputs.model_validate({
            **run.inputs.model_dump(mode="json"),
            "query_plan": plan.model_dump(mode="json"),
        })
        from geo_agent.measurement_workflow import MeasurementCoordinator

        updated = MeasurementCoordinator(self.repository).revise_inputs(
            run_id,
            principal.owner,
            expected_revision,
            inputs,
        )
        return self._envelope(
            updated.state.value,
            {
                "query_count": len(updated.inputs.query_plan.queries),
                "query_ids": [
                    query.query_id
                    for query in sorted(
                        updated.inputs.query_plan.queries,
                        key=lambda item: item.priority,
                    )
                ],
                "input_hash": updated.inputs.approval_hash,
                "human_approval_status": "pending",
                "provider_calls_started_by_this_tool": 0,
            },
            run_id=run_id,
            run_revision=updated.revision,
            next_actions=("review_and_approve_queries_in_human_ui",),
        )

    def set_brand_definition(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        definition: BrandDefinition,
        expected_definition_version: int,
    ) -> dict[str, Any]:
        principal.require(AgentScope.WRITE)
        record = self.repository.save_brand_definition(
            run_id,
            principal.owner,
            definition,
            expected_definition_version,
        )
        run = self.repository.get(run_id, principal.owner)
        return self._envelope(
            "ready",
            {
                **record.model_dump(mode="json"),
                "definition_hash": record.definition_hash,
                "origin": "agent-supplied-unverified",
                "provider_calls_started_by_this_tool": 0,
            },
            run_id=run_id,
            run_revision=run.revision,
            next_actions=("geo_get_assessment",),
        )

    def start_measurement(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        expected_revision: int,
        input_hash: str,
        idempotency_key: str,
        execution_authorization_id: str,
    ) -> dict[str, Any]:
        principal.require(AgentScope.EXECUTE)
        run = self.repository.get(run_id, principal.owner)
        if run.inputs is None or input_hash != run.inputs.approval_hash:
            raise Conflict("Measurement start does not match the current input hash")
        operation_ceiling = 5 + 5 * len(self.policy.profiles)
        job, updated = self.jobs.enqueue(
            run_id,
            principal.owner,
            expected_revision,
            JobType.EVALUATE,
            idempotency_key,
            EvaluationRequest(
                confirm_evaluation_calls=True,
                include_recommendations=False,
            ),
            execution_principal_id=principal.principal_id,
            execution_authorization_id=execution_authorization_id,
            policy_id=self.policy.policy_id,
            policy_hash=self.policy.policy_hash,
            operation_ceiling=operation_ceiling,
        )
        return self._envelope(
            "queued",
            {
                "job": job_view(job),
                "provider_calls_started_by_this_tool": 0,
                "poll_after_seconds": 5,
            },
            run_id=run_id,
            run_revision=updated.revision,
            job_id=job.job_id,
            next_actions=("geo_get_progress",),
        )

    def start_human_measurement(
        self,
        owner: OwnerIdentity,
        *,
        run_id: str,
        expected_revision: int,
        idempotency_key: str,
        include_recommendations: bool,
    ) -> tuple[Any, MeasurementRun]:
        return self.jobs.enqueue(
            run_id,
            owner,
            expected_revision,
            JobType.EVALUATE,
            idempotency_key,
            EvaluationRequest(
                confirm_evaluation_calls=True,
                include_recommendations=include_recommendations,
            ),
            policy_id=self.policy.policy_id,
            policy_hash=self.policy.policy_hash,
            operation_ceiling=(
                5
                + 5 * len(self.policy.profiles)
                + (1 if include_recommendations else 0)
            ),
        )

    def get_progress(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        job_id: str = "",
    ) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        if job_id:
            job = self.repository.get_job(job_id, principal.owner)
            if job.run_id != run_id:
                raise NotFound("Workflow job not found")
        progress = self.repository.get_run_progress(
            run_id,
            principal.owner,
            job_id or None,
        )
        checkpoints = self.repository.list_operation_checkpoints(
            run_id,
            principal.owner,
            job_id or progress.job_id,
        )
        return self._envelope(
            progress.job_state.value if progress.job_state is not None else progress.run_state,
            {
                **progress.model_dump(mode="json"),
                "checkpointed_operations": sum(
                    claim.state == OperationClaimState.COMPLETED
                    for claim in checkpoints
                ),
                "worker": self.repository.worker_health(),
            },
            run_id=run_id,
            run_revision=progress.run_revision,
            job_id=progress.job_id,
            next_actions=(
                ("geo_get_results",)
                if progress.run_state in {
                    MeasurementState.READY.value,
                    MeasurementState.PARTIAL.value,
                    MeasurementState.FAILED.value,
                    MeasurementState.EXPORTED.value,
                }
                else ("geo_get_progress", "geo_cancel_job")
            ),
            limitations=(
                "A completed job may contain a partial or failed measurement.",
                "Unknown provider outcomes are not retried automatically.",
            ),
        )

    def get_results(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        detail: Literal["summary", "answer"],
        query_id: str = "",
        profile_id: str = "",
    ) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        run = self.repository.get(run_id, principal.owner)
        if run.measurement is None:
            checkpoints = self.repository.list_operation_checkpoints(run_id, principal.owner)
            return self._envelope(
                "checkpointed-only" if checkpoints else "pending",
                {
                    "checkpointed_operations": len(checkpoints),
                    "measurement": None,
                },
                run_id=run_id,
                run_revision=run.revision,
                next_actions=("geo_get_progress",),
            )
        measurement = run.measurement
        if detail == "summary":
            data = {
                "scores": measurement_scores(measurement),
                "retrievals": [
                    {
                        "query_id": packet.query_id,
                        "status": packet.status,
                        "source_count": len(packet.sources),
                        "error": packet.error,
                    }
                    for packet in measurement.retrievals
                ],
                "outcomes": [
                    {
                        "query_id": result.query_id,
                        "profile_id": result.profile_id,
                        "status": result.status,
                        "citation_ids": list(result.citation_ids),
                        "error": result.error,
                    }
                    for result in measurement.results
                ],
            }
        else:
            if not query_id or not profile_id:
                raise Conflict("Answer detail requires one query ID and one profile ID")
            result = next(
                (
                    item
                    for item in measurement.results
                    if item.query_id == query_id and item.profile_id == profile_id
                ),
                None,
            )
            if result is None:
                raise NotFound("Measurement answer not found")
            data = result.model_dump(mode="json")
        return self._envelope(
            run.state.value,
            data,
            run_id=run_id,
            run_revision=run.revision,
            next_actions=("geo_get_evidence", "geo_get_assessment", "geo_get_content_strategy"),
        )

    def get_evidence(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        query_id: str,
        evidence_id: str,
    ) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        run = self.repository.get(run_id, principal.owner)
        packets = run.measurement.retrievals if run.measurement is not None else ()
        for packet in packets:
            if packet.query_id == query_id:
                for source in packet.sources:
                    if source.evidence_id == evidence_id:
                        return self._envelope(
                            "ready",
                            source.model_dump(mode="json"),
                            run_id=run_id,
                            run_revision=run.revision,
                            limitations=(
                                "This is retained evidence; the tool does not re-fetch the URL.",
                                "A retained passage does not prove claim support.",
                            ),
                        )
        for claim in self.repository.list_operation_checkpoints(run_id, principal.owner):
            if claim.operation_type != "webiq-search" or not isinstance(claim.output, dict):
                continue
            if claim.output.get("query_id") != query_id:
                continue
            for source in claim.output.get("sources", []):
                if isinstance(source, dict) and source.get("evidence_id") == evidence_id:
                    return self._envelope(
                        "checkpointed",
                        source,
                        run_id=run_id,
                        run_revision=run.revision,
                        limitations=(
                            "This evidence is checkpointed from interrupted work and is not a final measurement.",
                            "The source URL was not re-fetched.",
                        ),
                    )
        raise NotFound("Evidence not found")

    def get_assessment(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        view: Literal["summary", "grounding", "answers", "source_trace"],
        definition_version: int = 0,
        query_id: str = "",
        profile_id: str = "",
        cursor: str = "",
        page_size: int = 5,
    ) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        run = self.repository.get(run_id, principal.owner)
        record = self.repository.get_brand_definition(
            run_id,
            principal.owner,
            definition_version or None,
        )
        if run.measurement is None:
            return self._envelope(
                "pending-saved-measurement",
                {
                    "brand_definition": (
                        record.model_dump(mode="json") if record is not None else None
                    )
                },
                run_id=run_id,
                run_revision=run.revision,
                next_actions=("geo_get_progress",),
            )
        report = build_evidence_assessment(
            run.measurement,
            record,
            run_id=run_id,
            run_revision=run.revision,
        )
        if view == "summary":
            data = {
                "assessment_hash": report.assessment_hash,
                "measurement_hash": report.measurement_hash,
                "definition_version": report.definition_version,
                "definition_hash": report.definition_hash,
                "grounding": report.grounding,
                "answer": report.answer,
                "common_completed_query_ids": list(report.common_completed_query_ids),
                "limitations": list(report.limitations),
            }
            continuation = None
            truncated = False
        elif view == "grounding":
            queries = [
                item
                for item in report.queries
                if not query_id or item["query_id"] == query_id
            ]
            page, continuation, truncated = self._sequence_page(
                principal,
                kind="assessment-grounding",
                binding={
                    "run_id": run_id,
                    "assessment_hash": report.assessment_hash,
                    "query_id": query_id,
                },
                items=queries,
                cursor=cursor,
                page_size=page_size,
            )
            data = {
                "assessment_hash": report.assessment_hash,
                "queries": page,
            }
        elif view == "answers":
            answers = [
                item
                for item in report.answers
                if (not query_id or item["query_id"] == query_id)
                and (not profile_id or item["profile_id"] == profile_id)
            ]
            page, continuation, truncated = self._sequence_page(
                principal,
                kind="assessment-answers",
                binding={
                    "run_id": run_id,
                    "assessment_hash": report.assessment_hash,
                    "query_id": query_id,
                    "profile_id": profile_id,
                },
                items=answers,
                cursor=cursor,
                page_size=page_size,
            )
            data = {
                "assessment_hash": report.assessment_hash,
                "answers": page,
                "comparable_by_profile": report.comparable_by_profile,
            }
        else:
            traces = [
                {
                    "query_id": query["query_id"],
                    "sources": query.get("sources", []),
                }
                for query in report.queries
                if not query_id or query["query_id"] == query_id
            ]
            page, continuation, truncated = self._sequence_page(
                principal,
                kind="assessment-source-trace",
                binding={
                    "run_id": run_id,
                    "assessment_hash": report.assessment_hash,
                    "query_id": query_id,
                },
                items=traces,
                cursor=cursor,
                page_size=page_size,
            )
            data = {
                "assessment_hash": report.assessment_hash,
                "source_trace": page,
            }
        return self._envelope(
            "ready",
            data,
            run_id=run_id,
            run_revision=run.revision,
            next_actions=("geo_get_content_strategy", "geo_create_export"),
            limitations=report.limitations,
            continuation=continuation,
            truncated=truncated,
        )

    def get_content_strategy(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        view: Literal["summary", "patterns"],
        pattern_id: str = "",
        cursor: str = "",
        page_size: int = 3,
    ) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        run = self.repository.get(run_id, principal.owner)
        if run.measurement is None:
            return self._envelope(
                "pending-saved-measurement",
                {"report": None},
                run_id=run_id,
                run_revision=run.revision,
                next_actions=("geo_get_progress",),
            )
        report = build_content_strategy(run.measurement)
        if view == "summary":
            data = {
                key: value
                for key, value in report.model_dump(mode="json").items()
                if key != "patterns"
            }
            data["pattern_count"] = len(report.patterns)
            continuation = None
            truncated = False
        else:
            patterns = [
                pattern.model_dump(mode="json")
                for pattern in report.patterns
                if not pattern_id or pattern.pattern_id == pattern_id
            ]
            page, continuation, truncated = self._sequence_page(
                principal,
                kind="content-strategy-patterns",
                binding={
                    "run_id": run_id,
                    "measurement_hash": report.measurement_hash,
                    "pattern_id": pattern_id,
                },
                items=patterns,
                cursor=cursor,
                page_size=page_size,
            )
            data = {
                "measurement_hash": report.measurement_hash,
                "patterns": page,
            }
        return self._envelope(
            "ready",
            data,
            run_id=run_id,
            run_revision=run.revision,
            next_actions=("geo_create_export",),
            limitations=report.limitations,
            continuation=continuation,
            truncated=truncated,
        )

    def get_recommendations(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        task_id: str = "",
    ) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        run = self.repository.get(run_id, principal.owner)
        if run.recommendations is None:
            return self._envelope(
                "no-saved-drafts",
                {"recommendations": None, "review": None},
                run_id=run_id,
                run_revision=run.revision,
                next_actions=("geo_get_content_strategy",),
            )
        report = run.recommendations.model_dump(mode="json")
        if task_id:
            tasks = [task for task in report.get("tasks", []) if task["task_id"] == task_id]
            if not tasks:
                raise NotFound("Recommendation task not found")
            report["tasks"] = tasks
        else:
            report["tasks"] = [
                {
                    "task_id": task["task_id"],
                    "priority": task["priority"],
                    "query_id": task["query_id"],
                    "title": task["title"],
                }
                for task in report.get("tasks", [])
            ]
        review = (
            run.recommendation_review.model_dump(mode="json", exclude={"actor"})
            if run.recommendation_review is not None
            else None
        )
        return self._envelope(
            "ready",
            {"recommendations": report, "review": review},
            run_id=run_id,
            run_revision=run.revision,
            next_actions=(
                ("review_recommendation_drafts_in_human_ui",)
                if review is None and report.get("tasks")
                else ("geo_create_export",)
            ),
        )

    def cancel_job(
        self,
        principal: AgentPrincipal,
        *,
        job_id: str,
    ) -> dict[str, Any]:
        principal.require(AgentScope.CANCEL)
        job, run = self.jobs.cancel(job_id, principal.owner)
        return self._envelope(
            job.state.value,
            {
                "job": job_view(job),
                "consumed_operations_refunded": 0,
            },
            run_id=run.run_id,
            run_revision=run.revision,
            job_id=job.job_id,
            next_actions=("geo_get_progress",),
            limitations=(
                "In-flight provider calls may finish after cancellation.",
                "Consumed operation allowance is never refunded.",
            ),
        )

    def create_export(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        kind: ArtifactKind,
        expected_revision: int,
        idempotency_key: str,
        measurement_hash: str = "",
        definition_version: int = 0,
        definition_hash: str = "",
    ) -> dict[str, Any]:
        principal.require(AgentScope.EXPORT)
        if not idempotency_key.strip():
            raise Conflict("A bounded idempotency key is required")
        request_hash = digest({
            "run_id": run_id,
            "kind": kind.value,
            "expected_revision": expected_revision,
            "measurement_hash": measurement_hash,
            "definition_version": definition_version,
            "definition_hash": definition_hash,
        })
        replay = self.repository.reserve_export_request(
            principal.owner,
            idempotency_key,
            request_hash,
        )
        if replay is not None:
            run = self.repository.get(run_id, principal.owner)
            return self._envelope(
                "ready",
                {
                    "artifact": artifact_view(replay),
                    "resource_uri": f"geo://runs/{run_id}/exports/{replay.artifact_id}",
                    "download_url": (
                        f"{self.human_base_url}/api/v2/runs/{run_id}/artifacts/"
                        f"{replay.artifact_id}"
                    ),
                    "provider_calls_started_by_this_tool": 0,
                },
                run_id=run_id,
                run_revision=run.revision,
                next_actions=("geo_read_export",),
            )
        if kind == ArtifactKind.MEASUREMENT:
            artifact, run = self.artifacts.export(
                run_id,
                principal.owner,
                expected_revision,
                definition_version or None,
                definition_hash or None,
            )
        else:
            artifact = self.artifacts.export_companion(
                run_id,
                principal.owner,
                expected_revision,
                kind,
                definition_version=definition_version or None,
                definition_hash=definition_hash or None,
                measurement_hash=measurement_hash or None,
            )
            run = self.repository.get(run_id, principal.owner)
        artifact = self.repository.bind_export_request(
            principal.owner,
            idempotency_key,
            request_hash,
            artifact,
        )
        return self._envelope(
            "ready",
            {
                "artifact": artifact_view(artifact),
                "resource_uri": f"geo://runs/{run_id}/exports/{artifact.artifact_id}",
                "download_url": (
                    f"{self.human_base_url}/api/v2/runs/{run_id}/artifacts/"
                    f"{artifact.artifact_id}"
                ),
                "provider_calls_started_by_this_tool": 0,
            },
            run_id=run_id,
            run_revision=run.revision,
            next_actions=("geo_read_export",),
        )

    def list_exports(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        page_size: int = 20,
        cursor: str = "",
    ) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        if not 1 <= page_size <= 50:
            raise Conflict("Export page size must be between 1 and 50")
        run = self.repository.get(run_id, principal.owner)
        before_created_at = None
        before_artifact_id = None
        if cursor:
            data = self.cursor_codec.decode(cursor, "exports", principal.owner.key)
            if data.get("run_id") != run_id:
                raise Conflict("Cursor does not match the requested run")
            before_created_at = parse_cursor_datetime(data.get("created_at"))
            before_artifact_id = data.get("artifact_id")
            if not isinstance(before_artifact_id, str):
                raise Conflict("Cursor is invalid")
        records = self.repository.list_artifacts(
            run_id,
            principal.owner,
            page_size + 1,
            before_created_at=before_created_at,
            before_artifact_id=before_artifact_id,
        )
        items = records[:page_size]
        continuation = None
        if len(records) > page_size and items:
            last = items[-1]
            continuation = self.cursor_codec.encode(
                "exports",
                principal.owner.key,
                {
                    "run_id": run_id,
                    "created_at": last.created_at.isoformat(),
                    "artifact_id": last.artifact_id,
                },
            )
        return self._envelope(
            "ready",
            {"items": [artifact_view(item) for item in items]},
            run_id=run_id,
            run_revision=run.revision,
            next_actions=("geo_read_export",),
            continuation=continuation,
        )

    def read_export(
        self,
        principal: AgentPrincipal,
        *,
        run_id: str,
        artifact_id: str,
        entry: str,
        cursor: str = "",
    ) -> dict[str, Any]:
        principal.require(AgentScope.READ)
        artifact, text = self.artifacts.read_text_entry(
            run_id,
            artifact_id,
            principal.owner,
            entry,
        )
        chunk, continuation, truncated = self._text_page(
            principal,
            kind="export-entry",
            binding={
                "run_id": run_id,
                "artifact_id": artifact_id,
                "content_hash": artifact.content_hash,
                "entry": entry,
            },
            text=text,
            cursor=cursor,
            chunk_size=60000,
        )
        run = self.repository.get(run_id, principal.owner)
        return self._envelope(
            "ready",
            {
                "artifact": artifact_view(artifact),
                "entry": entry,
                "content": chunk,
            },
            run_id=run_id,
            run_revision=run.revision,
            continuation=continuation,
            truncated=truncated,
            limitations=(
                "This tool returns allowlisted UTF-8 archive entries, never inline ZIP bytes.",
            ),
        )
