from collections.abc import Callable

import httpx

from geo_agent.evaluation_workflow import EvaluationHandler
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.foundry import Foundry, azure_cli_token, model_base_url
from geo_agent.jobs import JobType
from geo_agent.measurement_budget import MeasurementBudgetGrant
from geo_agent.persistence import SQLAlchemyMeasurementRepository
from geo_agent.preparation import PreparationHandler
from geo_agent.providers import create_evaluator
from geo_agent.query_agent import HostedQueryPlanner, QueryAgentSettings
from geo_agent.recommendations import RecommendationService
from geo_agent.specialist_foundry import HostedSpecialistAgent
from geo_agent.specialist_workflow import SpecialistStageHandler
from geo_agent.webiq import WebIQ
from geo_agent.worker import Worker


class LiveMeasurementRuntime:
    def __init__(
        self,
        repository: SQLAlchemyMeasurementRepository,
        policy: MeasurementExecutionPolicy,
        *,
        budget_grant: MeasurementBudgetGrant | tuple[MeasurementBudgetGrant, ...],
        enforce_budget: bool = True,
        webiq_api_key: str,
        preparation_endpoint: str,
        preparation_deployment: str,
        query_agent_settings: QueryAgentSettings | None = None,
        token_provider: Callable[[], str] = azure_cli_token,
        webiq_transport: httpx.BaseTransport | None = None,
        foundry_transport: httpx.BaseTransport | None = None,
        query_agent_transport: httpx.BaseTransport | None = None,
        webiq_url_validator: Callable[[str], str] | None = None,
        worker_id: str = "local-live-worker",
        on_job_finished=None,
    ):
        if policy.execution_mode != "live":
            raise ValueError("The live measurement runtime requires a live execution policy")
        if query_agent_settings is not None and policy.max_query_plan_calls != 2:
            raise ValueError("Hosted query-agent fallback requires two query-plan calls in the live policy")
        preparation_base_url = model_base_url(preparation_endpoint)
        if any(
            profile.provider == "openai-responses"
            and profile.endpoint != preparation_base_url
            for profile in policy.profiles
        ):
            raise ValueError(
                "OpenAI evaluator profiles must use the environment-configured Azure OpenAI endpoint"
            )
        grants = budget_grant if isinstance(budget_grant, tuple) else (budget_grant,)
        if len({grant.owner.key for grant in grants}) != 1:
            raise ValueError("Measurement budget grants must belong to one owner")
        repository.bind_measurement_budgets(grants, policy)
        repository.set_measurement_budget_enforcement(enforce_budget)
        webiq_options = {"url_validator": webiq_url_validator} if webiq_url_validator is not None else {}
        webiq = WebIQ(webiq_api_key, transport=webiq_transport, **webiq_options)
        preparation = Foundry(
            preparation_endpoint,
            preparation_deployment,
            token_provider=token_provider,
            transport=foundry_transport,
        )
        query_planner = (
            HostedQueryPlanner(
                query_agent_settings,
                token_provider=token_provider,
                transport=query_agent_transport or foundry_transport,
            )
            if query_agent_settings is not None
            else preparation
        )
        evaluators = tuple(
            create_evaluator(profile, token_provider=token_provider, transport=foundry_transport)
            for profile in policy.profiles
        )
        recommendations = RecommendationService(preparation)
        evaluation_handler = EvaluationHandler(
            repository,
            policy,
            webiq,
            evaluators,
            recommendations,
        )
        self.worker = Worker(repository, worker_id, {
            JobType.PREPARE: PreparationHandler(
                policy,
                webiq,
                preparation,
                query_planner,
                preparation if query_agent_settings is not None else None,
                query_planner_operation_type=(
                    "missions-query-plan"
                    if query_agent_settings is not None
                    else "paired-query-plan"
                ),
            ),
            JobType.EVALUATE: evaluation_handler,
            JobType.RECOVER_EVALUATORS: evaluation_handler,
            JobType.AGENT_STAGE: SpecialistStageHandler(
                repository,
                recommendations,
                HostedSpecialistAgent(
                    token_provider=token_provider,
                    transport=foundry_transport,
                ),
            ),
        },
            policy_id=policy.policy_id,
            policy_hash=policy.policy_hash,
            owner_key=grants[0].owner.key,
            on_job_finished=on_job_finished,
        )
        self.repository = repository

    def run_once(self):
        return self.worker.run_once()

    def close(self) -> None:
        self.repository.close()
