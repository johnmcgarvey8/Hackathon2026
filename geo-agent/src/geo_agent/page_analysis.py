import json
from collections.abc import Callable
from typing import Literal

from pydantic import Field, HttpUrl

from geo_agent.contracts import Brief, Contract, ModelCall, PageSnapshot, Provenance, RunInputs, digest, identifier, utc_now
from geo_agent.foundry import Foundry
from geo_agent.live import LivePolicy, LiveWorkflow
from geo_agent.webiq import ProviderError, WebIQ, public_url
from geo_agent.workflow import Conflict, NotFound, RunStore


class AnalysisPolicy(Contract):
    policy_id: str = Field(pattern=r"^[a-z0-9-]{1,80}$")
    owner: str = Field(min_length=1)
    endpoint: str
    deployment: str
    max_analyses: int = Field(ge=1, le=100)
    max_evaluations: int = Field(ge=0, le=100)
    scope: Literal["public-http-urls"] = "public-http-urls"
    browse_mode: Literal["indexed-only"] = "indexed-only"
    retain_local_evidence: Literal[True] = True
    max_output_tokens_per_call: Literal[2000] = 2000
    monetary_ceiling: Literal["not-specified-call-cap-approved"] = "not-specified-call-cap-approved"
    approval: str = Field(min_length=1)


class AnalysisRequest(Contract):
    url: HttpUrl
    locale: str = Field(default="en-GB", pattern=r"^[a-z]{2}-[A-Z]{2}$")
    idempotency_key: str = Field(min_length=1, max_length=128)


class EvaluationBriefRequest(Contract):
    audience: str = Field(min_length=1, max_length=1000)
    goal: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=1, max_length=128)
    confirm_query_generation: Literal[True]


ANALYSIS_LIMITS = [
    "Indexed page excerpt only, at most 10,000 characters; not a live crawl or full-site audit.",
    "Observed statements describe the supplied page; their truth is not independently verified.",
    "Inferred findings and proposed improvements are hypotheses requiring review.",
    "No visual, JavaScript, technical SEO, ranking or citation-performance assessment.",
    "Citation evaluation requires a separate brief and approval of the exact generated queries.",
]


class PageAnalysisService:
    def __init__(self, store: RunStore, policy: AnalysisPolicy, webiq: WebIQ, foundry: Foundry,
                 url_validator: Callable[[str], str] = public_url):
        if foundry.base_url != policy.endpoint or foundry.deployment != policy.deployment:
            raise ProviderError("Foundry configuration does not match the page analysis policy")
        self.store, self.policy, self.webiq, self.foundry = store, policy, webiq, foundry
        self.validate_url = url_validator
        with store.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS analysis_policies (policy_id TEXT PRIMARY KEY, policy_hash TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS page_analyses (analysis_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, owner TEXT NOT NULL, request_key TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(policy_id, owner, request_key))")
            connection.execute("CREATE TABLE IF NOT EXISTS analysis_evaluations (analysis_id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, owner TEXT NOT NULL, run_id TEXT UNIQUE, payload TEXT NOT NULL)")
            policy_hash = digest(policy.model_dump(mode="json"))
            connection.execute("INSERT OR IGNORE INTO analysis_policies VALUES (?, ?)", (policy.policy_id, policy_hash))
            if connection.execute("SELECT policy_hash FROM analysis_policies WHERE policy_id = ?", (policy.policy_id,)).fetchone()[0] != policy_hash:
                raise Conflict("Page analysis policy changed; an existing allowance cannot be reset")

    def _owner(self, owner: str) -> None:
        if owner != self.policy.owner:
            raise NotFound("Page analysis policy not found")

    def budget(self) -> dict:
        with self.store.connect() as connection:
            used = connection.execute("SELECT COUNT(*) FROM page_analyses WHERE policy_id = ?", (self.policy.policy_id,)).fetchone()[0]
            evaluated = connection.execute("SELECT COUNT(*) FROM analysis_evaluations WHERE policy_id = ?", (self.policy.policy_id,)).fetchone()[0]
        return {"analyses_remaining": self.policy.max_analyses - used, "analyses_claimed": used,
                "evaluations_remaining": self.policy.max_evaluations - evaluated, "evaluations_claimed": evaluated,
                "calls_per_analysis": {"browse": 1, "analysis": 1},
                "calls_per_evaluation": {"query_generation": 1, "search": 5, "evaluator": 5},
                "automatic_retries": False, "failed_attempts_consume_slot": True}

    def list(self, owner: str) -> list[dict]:
        self._owner(owner)
        with self.store.connect() as connection:
            rows = connection.execute("SELECT payload FROM page_analyses WHERE owner = ? AND policy_id = ? ORDER BY rowid DESC", (owner, self.policy.policy_id)).fetchall()
        return [{key: record[key] for key in ("analysis_id", "url", "status", "created_at")}
                for record in (json.loads(row[0]) for row in rows)]

    def get(self, analysis_id: str, owner: str) -> dict:
        self._owner(owner)
        with self.store.connect() as connection:
            row = connection.execute("SELECT payload FROM page_analyses WHERE analysis_id = ? AND owner = ? AND policy_id = ?", (analysis_id, owner, self.policy.policy_id)).fetchone()
            evaluation = connection.execute("SELECT payload FROM analysis_evaluations WHERE analysis_id = ? AND owner = ? AND policy_id = ?", (analysis_id, owner, self.policy.policy_id)).fetchone()
        if row is None:
            raise NotFound("Page analysis not found")
        return {**json.loads(row[0]), "evaluation": json.loads(evaluation[0]) if evaluation else None}

    def _save(self, record: dict) -> None:
        with self.store.connect() as connection:
            connection.execute("UPDATE page_analyses SET payload = ? WHERE analysis_id = ?", (json.dumps(record), record["analysis_id"]))

    def analyse(self, request: AnalysisRequest, owner: str) -> dict:
        self._owner(owner)
        signature = digest(request.model_dump(mode="json"))
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM page_analyses WHERE policy_id = ? AND owner = ? AND request_key = ?", (self.policy.policy_id, owner, request.idempotency_key)).fetchone()
            if row:
                record = json.loads(row[0])
                if record["request_hash"] != signature:
                    raise Conflict("Analysis key is bound to a different request")
                return self.get(record["analysis_id"], owner)
            used = connection.execute("SELECT COUNT(*) FROM page_analyses WHERE policy_id = ?", (self.policy.policy_id,)).fetchone()[0]
            if used >= self.policy.max_analyses:
                raise Conflict("Page analysis allowance exhausted")
            record = {"analysis_id": identifier(), "url": str(request.url), "locale": request.locale,
                      "request_hash": signature, "status": "running", "created_at": utc_now().isoformat(),
                      "retrieval_status": "not-started", "analysis_status": "not-started", "snapshot": None,
                      "passages": [], "report": None, "model_call": None, "error": None, "limits": ANALYSIS_LIMITS}
            connection.execute("INSERT INTO page_analyses VALUES (?, ?, ?, ?, ?)", (record["analysis_id"], self.policy.policy_id, owner, request.idempotency_key, json.dumps(record)))
        try:
            target = self.validate_url(str(request.url))
            brief = Brief(url=target, locale=request.locale, audience="To be inferred from page evidence",
                          goal="Analyse page content, with evidence-backed observations and improvement hypotheses")
            record["retrieval_status"] = "attempted"
            self._save(record)
            snapshot = self.webiq.browse(brief)
            if str(snapshot.url) != target or snapshot.provenance != Provenance.LIVE or not snapshot.content.strip():
                raise ProviderError("Retrieval did not return usable evidence for the exact submitted page")
            record.update(snapshot=snapshot.model_dump(mode="json"), retrieval_status="completed")
            record["passages"] = [{"passage_id": f"page-{offset // 1000 + 1}", "url": target, "text": snapshot.content[offset:offset + 1000]}
                                  for offset in range(0, min(len(snapshot.content), 10000), 1000)]
            record["analysis_status"] = "attempted"
            self._save(record)
            report, metadata = self.foundry.analyse_page(snapshot, record["passages"])
            record.update(status="completed", analysis_status="completed", report=report.model_dump(mode="json"), model_call=metadata)
        except Exception as error:
            stage = "analysis_status" if record["retrieval_status"] == "completed" else "retrieval_status"
            record[stage] = "failed"
            record.update(status="failed", error=str(error) if isinstance(error, ProviderError) else "Page analysis failed; no automatic retry. Review the saved stage before a new attempt.")
        self._save(record)
        return self.get(record["analysis_id"], owner)

    def prepare_evaluation(self, analysis_id: str, request: EvaluationBriefRequest, owner: str) -> dict:
        analysis = self.get(analysis_id, owner)
        if analysis["status"] != "completed":
            raise Conflict("Complete a page analysis before requesting citation evaluation")
        signature = digest(request.model_dump(mode="json"))
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload FROM analysis_evaluations WHERE analysis_id = ?", (analysis_id,)).fetchone()
            if existing:
                record = json.loads(existing[0])
                if record["request_hash"] != signature:
                    raise Conflict("An evaluation brief was already claimed for this analysis")
                return record
            used = connection.execute("SELECT COUNT(*) FROM analysis_evaluations WHERE policy_id = ?", (self.policy.policy_id,)).fetchone()[0]
            if used >= self.policy.max_evaluations:
                raise Conflict("Page citation evaluation allowance exhausted")
            record = {"status": "running", "request_hash": signature, "run_id": None, "error": None}
            connection.execute("INSERT INTO analysis_evaluations VALUES (?, ?, ?, NULL, ?)", (analysis_id, self.policy.policy_id, owner, json.dumps(record)))
        try:
            snapshot = PageSnapshot.model_validate(analysis["snapshot"])
            brief = Brief(url=analysis["url"], locale=analysis["locale"], audience=request.audience, goal=request.goal)
            policy = LivePolicy(policy_id=f"page-eval-{analysis_id}", owner=owner, brief=brief,
                                endpoint=self.policy.endpoint, deployment=self.policy.deployment)
            queries, metadata = self.foundry.propose(brief, snapshot)
            run = self.store.create(owner, RunInputs(brief=brief, snapshot=snapshot, queries=queries, profiles=(self.foundry.profile,),
                                                    live_policy=policy.model_dump(mode="json"), live_policy_hash=policy.policy_hash,
                                                    query_generation=ModelCall(**metadata)))
            record.update(status="awaiting-query-approval", run_id=run.run_id)
        except Exception as error:
            record.update(status="failed", error=str(error) if isinstance(error, ProviderError) else "Query proposal failed; no automatic retry")
        with self.store.connect() as connection:
            connection.execute("UPDATE analysis_evaluations SET payload = ?, run_id = ? WHERE analysis_id = ?", (json.dumps(record), record["run_id"], analysis_id))
        return record

    def owns_run(self, run_id: str, owner: str) -> bool:
        with self.store.connect() as connection:
            return connection.execute("SELECT 1 FROM analysis_evaluations WHERE run_id = ? AND owner = ? AND policy_id = ?", (run_id, owner, self.policy.policy_id)).fetchone() is not None

    def execute(self, run_id: str, owner: str, revision: int, key: str):
        self._owner(owner)
        if not self.owns_run(run_id, owner):
            raise NotFound("Page evaluation not found")
        run = self.store.get(run_id, owner)
        policy = LivePolicy.model_validate(run.inputs.live_policy)
        return LiveWorkflow(self.store, policy, self.webiq, self.foundry).execute(run_id, owner, revision, key)