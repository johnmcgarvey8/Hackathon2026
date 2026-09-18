import os
import secrets
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from geo_agent.api import create_app
from geo_agent.budget import BudgetGrant, apply_grant
from geo_agent.conversation import ChatPolicy, ConversationAgent
from geo_agent.foundry import Foundry
from geo_agent.live import LivePolicy, configured_live_workflow
from geo_agent.execution_policy import MeasurementExecutionPolicy
from geo_agent.agent_access import AgentPrincipal
from geo_agent.mcp_server import _persistent_secret
from geo_agent.measurement_workflow import OwnerIdentity
from geo_agent.page_analysis import AnalysisPolicy, PageAnalysisService
from geo_agent.webiq import WebIQ
from geo_agent.workflow import RunStore


def load_environment(env_file: Path | None = None) -> None:
    path = env_file if env_file is not None else Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(path, override=False, interpolate=False)


def main() -> None:
    load_environment()
    data_dir = Path(os.environ.get("GEO_DATA_DIR", Path(__file__).resolve().parents[2] / ".data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("GEO_API_TOKEN")
    if token is None:
        token_path = data_dir / "local-api-token"
        try:
            descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            token = token_path.read_text(encoding="ascii").strip()
        else:
            token = secrets.token_urlsafe(32)
            with os.fdopen(descriptor, "w", encoding="ascii") as token_file:
                token_file.write(token)
        print(f"Local API token: {token_path.resolve()} (value not logged)")
    database = data_dir / "runs.sqlite3"
    policy_file = os.environ.get("GEO_LIVE_POLICY")
    grant_file = os.environ.get("GEO_BUDGET_GRANT")
    if grant_file:
        grant = BudgetGrant.model_validate_json(Path(grant_file).read_text(encoding="utf-8"))
        chat_policy_file = os.environ.get("GEO_CHAT_POLICY")
        if not policy_file or not chat_policy_file:
            raise ValueError("Budget grants require both the live and chat policy files")
        live_policy = LivePolicy.model_validate_json(Path(policy_file).read_text(encoding="utf-8"))
        chat_policy = ChatPolicy.model_validate_json(Path(chat_policy_file).read_text(encoding="utf-8"))
        if (grant.live_policy_id != live_policy.policy_id or grant.chat_policy_id != chat_policy.policy_id
                or grant.owner != live_policy.owner or grant.owner != chat_policy.owner):
            raise ValueError("Budget grant does not match the configured policies and owner")
        apply_grant(RunStore(database), grant)
    live = configured_live_workflow(RunStore(database), Path(policy_file), dict(os.environ)) if policy_file else None
    chat_file = os.environ.get("GEO_CHAT_POLICY")
    chat = None
    if chat_file:
        policy = ChatPolicy.model_validate_json(Path(chat_file).read_text(encoding="utf-8"))
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT") or os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
        if os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME") != policy.deployment:
            raise ValueError("Chat policy does not match the configured Foundry deployment")
        chat = ConversationAgent(RunStore(database), policy, endpoint=endpoint)
    analysis = None
    analysis_file = os.environ.get("GEO_ANALYSIS_POLICY")
    if analysis_file:
        analysis_policy = AnalysisPolicy.model_validate_json(Path(analysis_file).read_text(encoding="utf-8"))
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT") or os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
        analysis = PageAnalysisService(RunStore(database), analysis_policy,
                                       WebIQ(os.environ.get("WEBIQ_API_KEY", "")),
                                       Foundry(endpoint, os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "")))
    measurement_policy = None
    measurement_policy_file = os.environ.get("GEO_MEASUREMENT_POLICY")
    if measurement_policy_file:
        measurement_policy = MeasurementExecutionPolicy.model_validate_json(
            Path(measurement_policy_file).read_text(encoding="utf-8")
        )
    mcp_agent_token = os.environ.get("GEO_MCP_AGENT_TOKEN", "").strip()
    mcp_principals = None
    mcp_principal_id = os.environ.get("GEO_MCP_PRINCIPAL_ID", "local-agent")
    if mcp_agent_token:
        mcp_principals = {
            mcp_agent_token: AgentPrincipal(
                principal_id=mcp_principal_id,
                owner=OwnerIdentity(
                    tenant_id=os.environ.get("GEO_MCP_TENANT_ID", "local-development"),
                    object_id=os.environ.get("GEO_MCP_OWNER", "local-developer"),
                ),
            )
        }
    app = create_app(
        database,
        {token: "local-developer"},
        live=live,
        chat=chat,
        analysis=analysis,
        measurement_policy=measurement_policy,
        measurement_auto_worker=measurement_policy is not None and measurement_policy.execution_mode == "mock",
        measurement_mcp_principals=mcp_principals,
        mcp_cursor_secret=_persistent_secret(data_dir / "mcp-cursor-key") if mcp_principals else None,
        human_base_url=os.environ.get(
            "GEO_HUMAN_BASE_URL",
            f"http://127.0.0.1:{os.environ.get('GEO_PORT', '8088')}",
        ),
        default_mcp_agent_principal_id=mcp_principal_id,
        allow_live_mcp=os.environ.get("GEO_MCP_ALLOW_LIVE", "").casefold() == "true",
    )
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("GEO_PORT", "8088")))


if __name__ == "__main__":
    main()