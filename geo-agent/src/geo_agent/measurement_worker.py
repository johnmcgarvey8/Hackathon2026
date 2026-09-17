import os
import signal
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Event

import httpx
from dotenv import load_dotenv

from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.foundry import azure_cli_token
from geo_agent.live_runtime import LiveMeasurementRuntime
from geo_agent.measurement_budget import MeasurementBudgetGrant
from geo_agent.persistence import SQLiteMeasurementRepository


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required for the live measurement worker")
    return value


def _preparation_endpoint(environment: Mapping[str, str]) -> str:
    endpoint = environment.get("AZURE_OPENAI_ENDPOINT", "").strip()
    if endpoint:
        return endpoint
    endpoint = environment.get("AZURE_AI_PROJECT_ENDPOINT", "").strip()
    if endpoint:
        return endpoint
    raise ValueError(
        "AZURE_OPENAI_ENDPOINT or AZURE_AI_PROJECT_ENDPOINT is required for the live measurement worker"
    )


def create_runtime_from_environment(
    environment: Mapping[str, str],
    *,
    token_provider: Callable[[], str] = azure_cli_token,
    webiq_transport: httpx.BaseTransport | None = None,
    foundry_transport: httpx.BaseTransport | None = None,
) -> LiveMeasurementRuntime:
    policy = MeasurementExecutionPolicy.model_validate_json(
        Path(_required(environment, "GEO_MEASUREMENT_POLICY")).read_text(encoding="utf-8")
    )
    grant = MeasurementBudgetGrant.model_validate_json(
        Path(_required(environment, "GEO_MEASUREMENT_BUDGET_GRANT")).read_text(encoding="utf-8")
    )
    data_dir = Path(environment.get(
        "GEO_DATA_DIR",
        Path(__file__).resolve().parents[2] / ".data",
    ))
    data_dir.mkdir(parents=True, exist_ok=True)
    repository = SQLiteMeasurementRepository(data_dir / "runs.sqlite3")
    try:
        return LiveMeasurementRuntime(
            repository,
            policy,
            budget_grant=grant,
            webiq_api_key=_required(environment, "WEBIQ_API_KEY"),
            preparation_endpoint=_preparation_endpoint(environment),
            preparation_deployment=_required(environment, "AZURE_AI_MODEL_DEPLOYMENT_NAME"),
            token_provider=token_provider,
            webiq_transport=webiq_transport,
            foundry_transport=foundry_transport,
            worker_id=environment.get("GEO_MEASUREMENT_WORKER_ID", "local-live-worker"),
        )
    except Exception:
        repository.close()
        raise


def run_worker(
    runtime: LiveMeasurementRuntime,
    stop_event: Event,
    poll_seconds: float,
) -> None:
    if not 0.1 <= poll_seconds <= 60:
        raise ValueError("Worker poll interval must be between 0.1 and 60 seconds")
    while not stop_event.is_set():
        if runtime.run_once() is None:
            stop_event.wait(poll_seconds)


def main() -> None:
    load_dotenv(
        Path(__file__).resolve().parents[2] / ".env",
        override=False,
        interpolate=False,
    )
    runtime = create_runtime_from_environment(os.environ)
    try:
        poll_seconds = float(os.environ.get("GEO_MEASUREMENT_WORKER_POLL_SECONDS", "1"))
    except ValueError:
        runtime.close()
        raise ValueError("GEO_MEASUREMENT_WORKER_POLL_SECONDS must be a number") from None
    stop_event = Event()

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        run_worker(runtime, stop_event, poll_seconds)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()