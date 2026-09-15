import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from geo_agent.budget import initialise_grants
from geo_agent.contracts import Brief, Contract, EvaluationResult, ModelCall, Provenance, Run, RunInputs, State, digest
from geo_agent.foundry import Foundry
from geo_agent.webiq import ProviderError, WebIQ
from geo_agent.workflow import Conflict, Coordinator, RunStore


class LivePolicy(Contract):
    policy_id: str = Field(pattern=r"^[a-z0-9-]+$")
    owner: str = Field(min_length=1)
    brief: Brief
    deployment: str
    endpoint: str
    max_browse_calls: Literal[1] = 1
    max_query_generation_calls: Literal[1] = 1
    max_search_calls: Literal[5] = 5
    max_evaluator_calls: Literal[5] = 5
    max_output_tokens_per_call: Literal[2000] = 2000
    browse_mode: Literal["indexed-only"] = "indexed-only"
    retain_local_evidence: Literal[True] = True
    monetary_ceiling: Literal["not-specified-call-cap-approved"] = "not-specified-call-cap-approved"

    @property
    def policy_hash(self) -> str:
        return digest(self.model_dump(mode="json"))


class LiveWorkflow:
    def __init__(self, store: RunStore, policy: LivePolicy, webiq: WebIQ, foundry: Foundry):
        self.store = store
        self.coordinator = Coordinator(store)
        self.policy = policy
        self.webiq = webiq
        self.foundry = foundry
        if foundry.deployment != policy.deployment:
            raise ProviderError("Foundry deployment does not match the approved live policy")
        with store.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS live_calls (policy_id TEXT, operation TEXT, payload TEXT, PRIMARY KEY (policy_id, operation))")

    def _claim(self, operation: str, payload: dict) -> bool:
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload FROM live_calls WHERE policy_id = ? AND operation = ?", (self.policy.policy_id, operation)).fetchone()
            if existing:
                return False
            connection.execute("INSERT INTO live_calls VALUES (?, ?, ?)", (self.policy.policy_id, operation, json.dumps(payload)))
            return True

    def _record(self, operation: str, payload: dict) -> None:
        with self.store.connect() as connection:
            connection.execute("UPDATE live_calls SET payload = ? WHERE policy_id = ? AND operation = ?", (json.dumps(payload), self.policy.policy_id, operation))

    def _get(self, operation: str) -> dict | None:
        with self.store.connect() as connection:
            row = connection.execute("SELECT payload FROM live_calls WHERE policy_id = ? AND operation = ?", (self.policy.policy_id, operation)).fetchone()
            return json.loads(row[0]) if row else None

    def prepare(self, brief: Brief, owner: str, key: str) -> Run:
        if owner != self.policy.owner or brief != self.policy.brief:
            raise Conflict("Brief or owner does not match the approved live test policy")
        if not key.strip() or len(key) > 128:
            raise Conflict("Preparation requires a bounded idempotency key")
        claim = {"key": key, "policy_hash": self.policy.policy_hash, "status": "started"}
        if not self._claim("prepare", claim):
            existing = self._get("prepare")
            if existing and existing.get("key") == key and existing.get("policy_hash") == self.policy.policy_hash and existing.get("run_id"):
                return self.store.get(existing["run_id"], owner)
            raise Conflict("This policy's preparation was already claimed; it will not be repeated automatically")
        try:
            self._claim("browse", {"status": "started"})
            snapshot = self.webiq.browse(brief)
            self._record("browse", {"status": "completed", "snapshot": snapshot.model_dump(mode="json")})
            self._claim("query-generation", {"status": "started"})
            queries, metadata = self.foundry.propose(brief, snapshot)
            self._record("query-generation", {"status": "completed", **metadata})
            inputs = RunInputs(
                brief=brief, snapshot=snapshot, queries=queries, profiles=(self.foundry.profile,),
                live_policy_hash=self.policy.policy_hash, live_policy=self.policy.model_dump(mode="json"),
                query_generation=ModelCall(**metadata),
            )
            run = self.store.create(owner, inputs)
            self._record("prepare", {**claim, "status": "completed", "run_id": run.run_id})
            return run
        except (ProviderError, ValueError):
            self._record("prepare", {**claim, "status": "failed"})
            raise

    def execute(self, run_id: str, owner: str, revision: int, key: str) -> Run:
        run = self.store.get(run_id, owner)
        if (
            owner != self.policy.owner or run.inputs.snapshot.provenance != Provenance.LIVE
            or run.inputs.live_policy_hash != self.policy.policy_hash
            or run.inputs.brief != self.policy.brief or len(run.inputs.queries) != 5
            or run.inputs.profiles != (self.foundry.profile,)
        ):
            raise Conflict("Run inputs do not match the approved live test limits")
        run = self.coordinator.start(run_id, owner, revision, key)
        if run.state != State.EVALUATING:
            return run
        if not self._claim("evaluate", {"status": "started", "run_id": run_id, "revision": revision}):
            return self.store.get(run_id, owner)
        results = []
        halted = False
        for index, query in enumerate(run.inputs.queries, start=1):
            if self.store.get(run_id, owner).state != State.EVALUATING:
                self._record("evaluate", {"status": "cancelled", "run_id": run_id})
                return self.store.get(run_id, owner)
            sources = ()
            try:
                if halted:
                    raise ProviderError("Skipped after a provider failure; no call made")
                if not self._claim(f"search-{index}", {"status": "started"}):
                    raise ProviderError("Search already claimed; no automatic replay")
                sources = self.webiq.search(query, run.inputs.brief.locale)
                self._record(f"search-{index}", {"status": "completed", "sources": [source.model_dump(mode="json") for source in sources]})
                if self.store.get(run_id, owner).state != State.EVALUATING:
                    return self.store.get(run_id, owner)
                if not self._claim(f"answer-{index}", {"status": "started"}):
                    raise ProviderError("Evaluator already claimed; no automatic replay")
                result = self.foundry.evaluate(query, run.inputs.brief.locale, sources)
                self._record(f"answer-{index}", {"status": "completed", "result": result.model_dump(mode="json")})
            except ProviderError as error:
                halted = True
                result = EvaluationResult(
                    query_id=query.query_id, profile_id=self.foundry.profile.profile_id,
                    provenance=Provenance.LIVE, status="error", error=str(error), sources=sources,
                )
            results.append(result)
        if self.store.get(run_id, owner).state != State.EVALUATING:
            return self.store.get(run_id, owner)
        finished = self.coordinator.finish(run_id, owner, revision, tuple(results))
        self._record("evaluate", {"status": "completed", "run_id": run_id})
        return finished


class BudgetedLiveWorkflow:
    def __init__(self, original: LiveWorkflow):
        self.original = original
        self.policy = original.policy
        self.store = original.store
        initialise_grants(self.store)
        with self.store.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS live_run_slots (policy_id TEXT, owner TEXT, preparation_key TEXT, grant_id TEXT NOT NULL, slot INTEGER NOT NULL, child_policy_id TEXT UNIQUE NOT NULL, policy_hash TEXT NOT NULL, PRIMARY KEY(policy_id, owner, preparation_key), UNIQUE(grant_id, slot))")

    def budget(self) -> dict:
        with self.store.connect() as connection:
            allowed = connection.execute("SELECT COALESCE(SUM(live_runs), 0) FROM budget_grants WHERE live_policy_id = ? AND owner = ?", (self.policy.policy_id, self.policy.owner)).fetchone()[0]
            used = connection.execute("SELECT COUNT(*) FROM live_run_slots WHERE policy_id = ? AND owner = ?", (self.policy.policy_id, self.policy.owner)).fetchone()[0]
        return {"additional_runs": allowed, "claimed_runs": used, "remaining_runs": allowed - used,
                "calls_per_run": {"browse": 1, "query_generation": 1, "search": 5, "evaluator": 5},
                "failed_runs_consume_slot": True, "automatic_retries": False}

    def _workflow(self, child_policy_id: str) -> LiveWorkflow:
        policy = self.policy.model_copy(update={"policy_id": child_policy_id})
        return LiveWorkflow(self.store, policy, self.original.webiq, self.original.foundry)

    def prepare(self, brief: Brief, owner: str, key: str) -> Run:
        if owner != self.policy.owner or brief != self.policy.brief:
            raise Conflict("Brief or owner does not match the approved live test policy")
        if not key.strip() or len(key) > 128:
            raise Conflict("Preparation requires a bounded idempotency key")
        previous = self.original._get("prepare")
        if previous and previous.get("key") == key:
            return self.original.prepare(brief, owner, key)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT child_policy_id, policy_hash FROM live_run_slots WHERE policy_id = ? AND owner = ? AND preparation_key = ?", (self.policy.policy_id, owner, key)).fetchone()
            if existing:
                child_policy_id, policy_hash = existing
                if policy_hash != self.policy.policy_hash:
                    raise Conflict("Run slot is bound to different policy inputs")
            else:
                grants = connection.execute("SELECT grant_id, live_runs FROM budget_grants WHERE live_policy_id = ? AND owner = ? ORDER BY rowid", (self.policy.policy_id, owner)).fetchall()
                if not grants:
                    raise Conflict("No additional live runs approved")
                child_policy_id = None
                for grant_id, limit in grants:
                    claimed = connection.execute("SELECT COUNT(*) FROM live_run_slots WHERE grant_id = ?", (grant_id,)).fetchone()[0]
                    if claimed < limit:
                        child_policy_id = f"{self.policy.policy_id}-{grant_id}-{claimed + 1}"
                        connection.execute("INSERT INTO live_run_slots VALUES (?, ?, ?, ?, ?, ?, ?)", (
                            self.policy.policy_id, owner, key, grant_id, claimed + 1, child_policy_id, self.policy.policy_hash,
                        ))
                        break
                if child_policy_id is None:
                    raise Conflict("Additional live-run budget exhausted; new human authorisation is required")
        return self._workflow(child_policy_id).prepare(brief, owner, key)

    def execute(self, run_id: str, owner: str, revision: int, key: str) -> Run:
        run = self.store.get(run_id, owner)
        if run.inputs.live_policy_hash == self.policy.policy_hash:
            return self.original.execute(run_id, owner, revision, key)
        child_policy_id = (run.inputs.live_policy or {}).get("policy_id")
        with self.store.connect() as connection:
            slot = connection.execute("SELECT policy_hash FROM live_run_slots WHERE child_policy_id = ? AND policy_id = ? AND owner = ?", (child_policy_id, self.policy.policy_id, owner)).fetchone()
        if slot is None or slot[0] != self.policy.policy_hash:
            raise Conflict("Run has no matching authorised budget slot")
        return self._workflow(child_policy_id).execute(run_id, owner, revision, key)


def configured_live_workflow(store: RunStore, policy_file: Path, environment: dict[str, str]) -> LiveWorkflow | BudgetedLiveWorkflow:
    policy = LivePolicy.model_validate_json(policy_file.read_text(encoding="utf-8"))
    endpoint = environment.get("AZURE_OPENAI_ENDPOINT") or environment.get("AZURE_AI_PROJECT_ENDPOINT", "")
    foundry = Foundry(endpoint, environment.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", ""))
    webiq = WebIQ(
        environment.get("WEBIQ_API_KEY", ""),
        browse_endpoint=environment.get("WEBIQ_BROWSE_ENDPOINT", ""),
        search_endpoint=environment.get("WEBIQ_SEARCH_ENDPOINT", ""),
    )
    original = LiveWorkflow(store, policy, webiq, foundry)
    return BudgetedLiveWorkflow(original) if environment.get("GEO_BUDGET_GRANT") else original