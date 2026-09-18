from collections.abc import Callable

import httpx

from geo_agent.evaluation_workflow import EvaluationHandler
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.foundry import Foundry, azure_cli_token
from geo_agent.jobs import JobType
from geo_agent.measurement_budget import MeasurementBudgetGrant
from geo_agent.persistence import SQLAlchemyMeasurementRepository
from geo_agent.preparation import PreparationHandler
from geo_agent.providers import create_evaluator
from geo_agent.recommendations import RecommendationService
from geo_agent.webiq import WebIQ
from geo_agent.worker import Worker


class LiveMeasurementRuntime:
    def __init__(
        self,
        repository: SQLAlchemyMeasurementRepository,
        policy: MeasurementExecutionPolicy,
        *,
        budget_grant: MeasurementBudgetGrant,
        webiq_api_key: str,
        preparation_endpoint: str,
        preparation_deployment: str,
        token_provider: Callable[[], str] = azure_cli_token,
        webiq_transport: httpx.BaseTransport | None = None,
        foundry_transport: httpx.BaseTransport | None = None,
        webiq_url_validator: Callable[[str], str] | None = None,
        worker_id: str = "local-live-worker",
    ):
        if policy.execution_mode != "live":
            raise ValueError("The live measurement runtime requires a live execution policy")
        repository.bind_measurement_budget(budget_grant, policy)
        webiq_options = {"url_validator": webiq_url_validator} if webiq_url_validator is not None else {}
        webiq = WebIQ(webiq_api_key, transport=webiq_transport, **webiq_options)
        preparation = Foundry(
            preparation_endpoint,
            preparation_deployment,
            token_provider=token_provider,
            transport=foundry_transport,
        )
        evaluators = tuple(
            create_evaluator(profile, token_provider=token_provider, transport=foundry_transport)
            for profile in policy.profiles
        )
        self.worker = Worker(repository, worker_id, {
            JobType.PREPARE: PreparationHandler(policy, webiq, preparation, preparation),
            JobType.EVALUATE: EvaluationHandler(
                repository,
                policy,
                webiq,
                evaluators,
                RecommendationService(preparation),
            ),
        },
            policy_id=policy.policy_id,
            policy_hash=policy.policy_hash,
            owner_key=budget_grant.owner.key,
        )
        self.repository = repository

    def run_once(self):
        return self.worker.run_once()

    def close(self) -> None:
        self.repository.close()