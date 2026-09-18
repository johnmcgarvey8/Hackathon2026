import logging
from collections.abc import Callable
from threading import Event, Thread

from geo_agent.jobs import ClaimedOperationRunner, JobRepository, JobType, LeaseLost, WorkflowJob
from geo_agent.measurement_workflow import MeasurementRun, Mutation
from geo_agent.webiq import ProviderError


JobHandler = Callable[[WorkflowJob, ClaimedOperationRunner], Mutation]
logger = logging.getLogger(__name__)


class LeaseHeartbeat:
    def __init__(
        self,
        repository: JobRepository,
        job: WorkflowJob,
        worker_id: str,
        lease_seconds: int,
        interval_seconds: float,
    ):
        if job.lease_token is None:
            raise LeaseLost("Leased workflow job is missing a fencing token")
        self.repository = repository
        self.job = job
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self._stop = Event()
        self._error: Exception | None = None
        self._thread = Thread(
            target=self._run,
            name=f"geo-lease-{job.job_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.repository.renew_job_lease(
                    self.job.job_id,
                    self.worker_id,
                    self.job.lease_token or "",
                    self.lease_seconds,
                )
            except Exception as error:
                self._error = error
                logger.warning(
                    "Workflow lease renewal failed for job %s: %s",
                    self.job.job_id,
                    type(error).__name__,
                )
                self._stop.set()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds * 2))

    def ensure_active(self) -> None:
        if self._error is not None:
            raise LeaseLost("Worker lost its renewable job lease") from self._error
        self.repository.renew_job_lease(
            self.job.job_id,
            self.worker_id,
            self.job.lease_token or "",
            self.lease_seconds,
        )


class Worker:
    def __init__(
        self,
        repository: JobRepository,
        worker_id: str,
        handlers: dict[JobType, JobHandler],
        *,
        lease_seconds: int = 300,
        heartbeat_seconds: float | None = None,
        policy_id: str | None = None,
        policy_hash: str | None = None,
        owner_key: str | None = None,
    ):
        if not 3 <= lease_seconds <= 3600:
            raise ValueError("Worker lease duration must be between 3 and 3600 seconds")
        self.repository = repository
        self.worker_id = worker_id
        self.handlers = handlers
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = (
            heartbeat_seconds
            if heartbeat_seconds is not None
            else max(1.0, lease_seconds / 3)
        )
        if not 0 < self.heartbeat_seconds < lease_seconds:
            raise ValueError("Worker heartbeat must be shorter than the lease duration")
        self.policy_id = policy_id
        self.policy_hash = policy_hash
        self.owner_key = owner_key

    def run_once(self) -> tuple[WorkflowJob, MeasurementRun] | None:
        self.repository.recover_interrupted()
        job = self.repository.lease_one_job(
            self.worker_id,
            lease_seconds=self.lease_seconds,
            policy_id=self.policy_id,
            policy_hash=self.policy_hash,
            owner_key=self.owner_key,
        )
        if job is None:
            return None
        handler = self.handlers.get(job.job_type)
        if handler is None:
            return self.repository.fail_job(
                job.job_id,
                self.worker_id,
                "unsupported-job-type",
                job.lease_token,
            )
        operations = ClaimedOperationRunner(self.repository, job, self.worker_id)
        heartbeat = LeaseHeartbeat(
            self.repository,
            job,
            self.worker_id,
            self.lease_seconds,
            self.heartbeat_seconds,
        )
        heartbeat.start()
        try:
            mutation = handler(job, operations)
        except Exception as error:
            heartbeat.stop()
            if isinstance(error, LeaseLost):
                logger.warning("Worker lost ownership of job %s", job.job_id)
                return None
            code = error.code.value if isinstance(error, ProviderError) else type(error).__name__
            try:
                heartbeat.ensure_active()
                return self.repository.fail_job(
                    job.job_id,
                    self.worker_id,
                    code,
                    job.lease_token,
                )
            except LeaseLost:
                logger.warning("Worker could not finalize failed job %s after lease loss", job.job_id)
                return None
        heartbeat.stop()
        try:
            heartbeat.ensure_active()
            return self.repository.complete_job(
                job.job_id,
                self.worker_id,
                mutation,
                job.lease_token,
            )
        except LeaseLost:
            logger.warning("Worker could not complete job %s after lease loss", job.job_id)
            return None