import json

from pydantic import Field

from geo_agent.contracts import Contract, digest
from geo_agent.workflow import Conflict, RunStore


class BudgetGrant(Contract):
    grant_id: str = Field(pattern=r"^[a-z0-9-]{1,80}$")
    owner: str = Field(min_length=1)
    chat_policy_id: str
    live_policy_id: str
    additional_chat_requests: int = Field(ge=0, le=1000)
    additional_live_runs: int = Field(ge=0, le=100)
    approval: str = Field(min_length=1, max_length=2000)


def initialise_grants(store: RunStore) -> None:
    with store.connect() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS budget_grants (grant_id TEXT PRIMARY KEY, owner TEXT NOT NULL, chat_policy_id TEXT NOT NULL, live_policy_id TEXT NOT NULL, chat_requests INTEGER NOT NULL, live_runs INTEGER NOT NULL, payload TEXT NOT NULL)")


def apply_grant(store: RunStore, grant: BudgetGrant) -> None:
    initialise_grants(store)
    payload = grant.model_dump(mode="json")
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT payload FROM budget_grants WHERE grant_id = ?", (grant.grant_id,)).fetchone()
        if existing:
            if digest(json.loads(existing[0])) != digest(payload):
                raise Conflict("Budget grant already exists with different authorisation")
            return
        connection.execute("INSERT INTO budget_grants VALUES (?, ?, ?, ?, ?, ?, ?)", (
            grant.grant_id, grant.owner, grant.chat_policy_id, grant.live_policy_id,
            grant.additional_chat_requests, grant.additional_live_runs, json.dumps(payload),
        ))