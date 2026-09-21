from collections.abc import Callable
import logging

from geo_agent.jobs import ClaimedOperationRunner, JobRepository, JobType, WorkflowJob
from geo_agent.measurement_workflow import MeasurementRun, Mutation
from geo_agent.webiq import ProviderError


logger = logging.getLogger(__name__)


JobHandler = Callable[[WorkflowJob, ClaimedOperationRunner], Mutation]


class Worker:
    def __init__(
        self,
        repository: JobRepository,
        worker_id: str,
        handlers: dict[JobType, JobHandler],
        on_job_finished: Callable[[WorkflowJob, MeasurementRun], None] | None = None,
    ):
        self.repository = repository
        self.worker_id = worker_id
        self.handlers = handlers
        self.on_job_finished = on_job_finished

    def run_once(self) -> tuple[WorkflowJob, MeasurementRun] | None:
        self.repository.recover_interrupted()
        job = self.repository.lease_one_job(self.worker_id, lease_seconds=300)
        if job is None:
            return None
        handler = self.handlers.get(job.job_type)
        if handler is None:
            result = self.repository.fail_job(job.job_id, self.worker_id, "unsupported-job-type")
            self._notify_finished(result)
            return result
        operations = ClaimedOperationRunner(self.repository, job, self.worker_id)
        try:
            mutation = handler(job, operations)
        except Exception as error:
            code = error.code.value if isinstance(error, ProviderError) else type(error).__name__
            result = self.repository.fail_job(job.job_id, self.worker_id, code)
        else:
            result = self.repository.complete_job(job.job_id, self.worker_id, mutation)
        self._notify_finished(result)
        return result

    def _notify_finished(
        self,
        result: tuple[WorkflowJob, MeasurementRun],
    ) -> None:
        if self.on_job_finished is None:
            return
        try:
            self.on_job_finished(*result)
        except Exception:
            logger.exception(
                "Post-job reconciliation failed for job %s",
                result[0].job_id,
            )