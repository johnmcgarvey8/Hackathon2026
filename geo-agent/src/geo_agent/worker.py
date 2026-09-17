from collections.abc import Callable

from geo_agent.jobs import ClaimedOperationRunner, JobRepository, JobType, WorkflowJob
from geo_agent.measurement_workflow import MeasurementRun, Mutation
from geo_agent.webiq import ProviderError


JobHandler = Callable[[WorkflowJob, ClaimedOperationRunner], Mutation]


class Worker:
    def __init__(
        self,
        repository: JobRepository,
        worker_id: str,
        handlers: dict[JobType, JobHandler],
    ):
        self.repository = repository
        self.worker_id = worker_id
        self.handlers = handlers

    def run_once(self) -> tuple[WorkflowJob, MeasurementRun] | None:
        self.repository.recover_interrupted()
        job = self.repository.lease_one_job(self.worker_id, lease_seconds=300)
        if job is None:
            return None
        handler = self.handlers.get(job.job_type)
        if handler is None:
            return self.repository.fail_job(job.job_id, self.worker_id, "unsupported-job-type")
        operations = ClaimedOperationRunner(self.repository, job, self.worker_id)
        try:
            mutation = handler(job, operations)
        except Exception as error:
            code = error.code.value if isinstance(error, ProviderError) else type(error).__name__
            return self.repository.fail_job(job.job_id, self.worker_id, code)
        return self.repository.complete_job(job.job_id, self.worker_id, mutation)