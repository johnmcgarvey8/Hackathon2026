import os
import signal
from collections.abc import Mapping
from pathlib import Path
from threading import Event

from dotenv import load_dotenv

from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.mock_runtime import MockMeasurementRuntime
from geo_agent.persistence import SQLiteMeasurementRepository


def create_runtime_from_environment(
    environment: Mapping[str, str],
) -> tuple[MockMeasurementRuntime, SQLiteMeasurementRepository]:
    root = Path(__file__).resolve().parents[2]
    policy_path = Path(
        environment.get(
            "GEO_MEASUREMENT_POLICY",
            root / "measurement-policy.json",
        )
    )
    policy = MeasurementExecutionPolicy.model_validate_json(
        policy_path.read_text(encoding="utf-8")
    )
    if policy.execution_mode != "mock":
        raise ValueError("The mock measurement worker requires a mock execution policy")
    data_dir = Path(environment.get("GEO_DATA_DIR", root / ".data"))
    repository = SQLiteMeasurementRepository(data_dir / "runs.sqlite3")
    return MockMeasurementRuntime(repository, policy), repository


def run_worker(
    runtime: MockMeasurementRuntime,
    stop_event: Event,
    poll_seconds: float,
) -> None:
    if not 0.1 <= poll_seconds <= 60:
        raise ValueError("Worker poll interval must be between 0.1 and 60 seconds")
    while not stop_event.is_set():
        if runtime.worker.run_once() is None:
            stop_event.wait(poll_seconds)


def main() -> None:
    load_dotenv(
        Path(__file__).resolve().parents[2] / ".env",
        override=False,
        interpolate=False,
    )
    runtime, repository = create_runtime_from_environment(os.environ)
    try:
        poll_seconds = float(
            os.environ.get("GEO_MEASUREMENT_WORKER_POLL_SECONDS", "1")
        )
    except ValueError:
        repository.close()
        raise ValueError(
            "GEO_MEASUREMENT_WORKER_POLL_SECONDS must be a number"
        ) from None
    stop_event = Event()

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        run_worker(runtime, stop_event, poll_seconds)
    finally:
        repository.close()


if __name__ == "__main__":
    main()
