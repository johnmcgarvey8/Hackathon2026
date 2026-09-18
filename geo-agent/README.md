# GEO Agent: Conversational Evidence Assistant

The local GEO agent analyses public page URLs and holds multi-turn conversations about saved evaluation evidence. Page analysis returns observed and inferred findings with validated verbatim quotes, plus improvement hypotheses requiring review. The Microsoft Agent Framework chat remains read-only and bound to its existing saved run; the browser's separate analysis workflow uses structured Foundry calls. Neither model can approve evaluations or publish changes. The v2 measurement console connects the durable multi-profile workflow to the local browser in explicit mock mode. No Azure resources were provisioned by this build. The [build plan](../geo-agent-build-plan.md) remains the roadmap; the [offline proposal](../geo-optimiser.html) is unchanged.

## V2 Backend Milestone

The recommended release path now supports both the human website and an MCP agent interface over the same application service. The local website exposes the durable workflow at `/measurements`; agents use stdio or loopback Streamable HTTP. Remote hosting remains gated on production identity, PostgreSQL, durable artifact storage and a separately approved live canary.

| Component | Implemented and offline-tested |
| --- | --- |
| [Versioned contracts](src/geo_agent/contracts.py) | Five priority-ranked chat/search query pairs, exact page references, three separately configurable simulation profiles, immutable input fingerprints and independently retained retrieval packets. V1 serialisation and approval hashes remain unchanged. |
| [Paired planner](src/geo_agent/foundry.py) | `Foundry.propose_pairs` reuses the saved snapshot, validates quoted passages and rejects duplicate queries or invalid priority order. |
| [Evaluator providers](src/geo_agent/providers.py) | OpenAI Responses for ChatGPT-style and Copilot-style profiles; Anthropic Messages via `anthropic==0.125.0` for Claude-backed answers. Canonical endpoint validation, approved prompt guards, bounded evidence, 2,000 output tokens and no automatic retries. |
| [Measurement scoring](src/geo_agent/evaluation.py) | Exact-page citation rate, errors and coverage; separate Web IQ presence/position metrics; same-domain, unsupported-citation and raw-mention details. Model comparisons use only the common completed query set. |
| [Recommendations](src/geo_agent/recommendations.py) | Automatic, deterministic content-strategy report separates returned excerpt patterns from cited sources and final-answer wording, with exact quotes and original-page changes to test. No provider call. Optional legacy model reports remain supported with up to three reviewed draft hypotheses. |
| [Draft exports](src/geo_agent/artifacts.py) | `render_measurement_bundle` validates reports and produces deterministic ZIPs containing measurement data, scores, recommendations and a sanitized human review record. Only accepted tasks become Markdown planning files. No provider call or publishing permission. Existing run exports are unchanged. |
| [Durable runtime](src/geo_agent/persistence.py) | SQLAlchemy-backed runs, events, approvals, jobs, operation claims, v2 budget consumption and artifact metadata. The mock and live workers share the same claim-before-call path. |
| [Live worker](src/geo_agent/measurement_worker.py) | Separate queue worker with immutable owner/policy-bound grants, proportional capacity for one or more complete runs, failed-call charging and graceful shutdown. Startup validation makes no provider call. |
| [Measurement console](src/geo_agent/measurement.html) | Owner-scoped run recovery, explicit preparation and evaluation confirmations, exact-query approval, visible provenance and limitations, one- or three-profile results, retained evidence, human recommendation decisions, cancellation and authenticated ZIP download. The bearer token remains in browser memory. |
| [MCP server](src/geo_agent/mcp_server.py) | Twenty bounded tools over stdio and Streamable HTTP. Submission returns durable job IDs; discovery and reads start no provider work. |
| [Shared application service](src/geo_agent/measurement_service.py) | REST and MCP submission paths share policy, state, authorization, result and export operations. Compact signed cursors prevent full-history model payloads. |
| [Agent authority](src/geo_agent/agent_access.py) | Agent identity is supplied outside tool arguments. Human-issued stage authorizations bind owner, principal, run revision, input hash, policy, operation ceiling and expiry. |

All profiles are controlled evidence-packet simulations, not measurements of the consumer Copilot, Claude or ChatGPT services. The same ordered search packet must be used for each profile's answer to a query. Target-page text is not added to evaluator input; only returned search evidence can earn citation credit. A Claude label requires the Anthropic adapter, not a GPT persona. Actual deployments, access and underlying model identities still require live preflight.

The Copilot-style system prompt is intentionally diagnostic: it identifies buyer criteria supported by the packet, labels material criteria that cannot be answered as evidence gaps, and supplies concrete next steps only when grounded in cited evidence. It must not infer missing product capabilities or page-content absence from an evidence gap. The common evidence-only guard remains authoritative for every profile.

The citation score is `round(100 * completed answers citing the exact page / completed answers)`. Failed calls reduce coverage; no completed answers means N/A. Priority does not weight the score. Search absence means the exact page was not in that saved top-five packet, not global invisibility or rank six. Recommendations are hypotheses: quote checks prove traceability, not semantic correctness, causal ranking factors or guaranteed gains.

The v2 ZIP contains `manifest.json`, `manifest.md`, `measurement.json`, `scores.json`, `recommendations.json` and `recommendation-review.json`. The review file excludes reviewer identity. Each accepted task also produces `recommendations/rec-N.md` as a human planning input; rejected and unreviewed drafts remain only in the evidence report. Acceptance grants no editing or publishing permission. Its manifest is `geo-measurement-manifest/v1`, distinct from the legacy run manifest `geo-manifest/v2`. With the installed package, scores can be recomputed from the retained JSON:

```python
from pathlib import Path
from geo_agent.contracts import MeasurementResults
from geo_agent.evaluation import measurement_scores

measurement = MeasurementResults.model_validate_json(Path("measurement.json").read_bytes())
scores = measurement_scores(measurement)
```

The exported input hash is an integrity fingerprint, not evidence that a human approved execution. V2 runs, approvals, jobs, operation claims and artifacts are durable and owner-scoped. Claims are committed before work is dispatched, validated outputs are checkpointed with their claims, and interrupted or ambiguous calls are not automatically replayed. Renewable fenced leases prevent late workers from committing after cancellation or lease loss. A missing recommendation report exports as JSON `null`, allowing saved scores to remain available without inventing recommendations.

Verification: **526 tests passed**, with one existing Starlette/AnyIO dependency deprecation warning. The [MCP tests](tests/test_mcp_server.py) exercise all twenty tools, real stdio and Streamable HTTP clients, separate agent identity, human-issued execution authorization, terminal replay, job-scoped progress, bounded maximal query packets and immutable export reads. The [live runtime test](tests/test_live_runtime.py) uses SDK mock transports and the durable budget ledger to exercise one Browse, one analysis, one paired plan, five searches and fifteen answer attempts: 23 charged operations with no external traffic. Focused tests also prove proportional multi-run allowances, one-active/20-global/4-owner admission, renewable fenced leases, checkpoint survival, migration recovery, owner isolation, failed-call charging, complete human recommendation decisions, sanitized accepted-task exports and the Copilot-style evidence-gap guard. The browser checks cover the six-stage UI and both agent-authorization handoffs, including accessibility, reload recovery, safe rendering and ZIP integrity.

### Grounding and Answer Assessment

Returned evidence is not the same as a citation in the answer. The [evidence assessment](src/geo_agent/evidence_assessment.py) audits saved data without Web IQ, model or judge calls. It keeps the original exact-page score and approved measurement inputs unchanged. The Measure stage separates **Grounding evidence**, **Answer and citations**, and **Source trace**; Export retains a compact summary and a link back to Measure.

New measurement asks only for URL, audience, goal and locale. After Browse, the existing [preparation analysis call](src/geo_agent/foundry.py) uses the saved page content plus model knowledge to infer the primary brand, distinctive/common-word aliases and brand domain. It returns a `PreparationAnalysis` response, preserving the legacy `PageAnalysis` API. Preparation still uses exactly one Browse, one analysis and one query-planning call, with the same 2,000-token limit and no automatic retries. No additional model or Web IQ call is added for brand setup.

The inferred name or an alias must appear in a validated verbatim passage quote. Unsupported or unanchored suggestions are discarded; unclear pages can return no brand. Model knowledge may suggest a canonical name or alias, but cannot expand domain ownership: only the exact browsed hostname is retained when the model identifies a first-party page. For example, `Microsoft Clarity` can have ambiguous alias `Clarity` and domain `clarity.microsoft.com`; `microsoft.com` is never inferred as owned. This is model-generated configuration, not independent verification of names, aliases or ownership.

The [preparation transaction](src/geo_agent/persistence.py) saves the definition with `source: page-analysis` alongside the prepared run, only if no definition already exists. Manual overrides win. The suggestion, rationale and page quotes survive reload in the saved preparation analysis. Query review shows a compact brand summary with optional **Brand details** and **Edit brand definition**; no separate brand-confirmation checkbox is required. If the brand is unidentified, query approval still works and brand metrics remain N/A. Existing runs can use **Set brand** without rerunning paid preparation. Corrections append a definition version without changing approved queries, evaluator prompts, run revisions or budgets. Names/aliases are limited to 120 characters, with up to ten aliases and ten domains. Domain matches require an exact host or dot-bounded subdomain.

| Finding | Denominator and limits |
| --- | --- |
| Brand in Web IQ evidence | Successful query packets with a nonambiguous brand text match / successful packets. Titles and retained passages are inspected; domains are a separate signal. Successful empty packets count as absent. Failed/missing searches remain unknown and lower coverage. |
| Brand in answers | Completed answers with a nonambiguous text match / completed answers. A negative mention still counts as a mention, not an endorsement. |
| Supplied sources cited | Unique valid model-reported citation IDs / supplied source records across completed answers. Each profile answer is a separate opportunity; profiles do not multiply Web IQ retrieval counts. |
| Brand-source conversion | Completed answers citing a brand-bearing source / completed answers supplied a brand-bearing source. The source-level branded cited fraction is also retained. Neither metric replaces answer brand presence or exact-page citation. |

Matching is literal, Unicode-normalized and case-insensitive, with word/phrase boundaries. Ambiguous aliases count toward strict brand presence only when the same source also contains a distinctive alias/name or has a configured brand-owned host. Answers require corroboration in the answer itself, not the question or supplied packet. The query's `branded` flag describes the input query and is never used as an output brand finding. No saved definition means N/A brand metrics; valid citation inspection remains available. Matching results depend on the saved definition, including any model-inferred assumptions.

Every source trace is scoped by query ID plus evidence ID. It shows cited, not cited or unknown; duplicate citation IDs count once and unsupported IDs remain explicit validity findings. Optional shared wording means at least six consecutive normalized words occur in both excerpt and answer; overlapping matches shared by multiple sources are marked non-unique. Original text and match spans are retained for safe highlights. No match does not establish lack of support. A citation is not proof of claim support, uncited evidence may influence output, and source-selection motives and claim-level citation alignment were not recorded. More citations are not inherently better. No hidden reasoning or semantic judge result is claimed.

Authenticated owner/Geo.Operator routes in [measurement_api.py](src/geo_agent/measurement_api.py):

- `POST /api/v2/briefs` accepts the existing flat brief plus optional manual `brand_definition`; run and manual definition creation are atomic. The UI omits this field and lets preparation generate it. Old clients remain valid.
- `POST /api/v2/runs/{id}/brand-definition` accepts `definition` and `expected_definition_version` (zero for first save). Stale changes return 409; repeating the current definition returns it without creating another version.
- `GET /api/v2/runs/{id}/evidence-assessment?definition_version=N` returns configuration, report and hash. Version is optional and defaults to latest. Pending work has no fabricated per-query results: detailed analysis waits for the saved measurement. Run lists and compact progress responses do not include assessments.
- `GET /api/v2/runs/{id}/evidence-assessment/download?definition_version=N` returns a reproducible companion ZIP; optional `measurement_hash` rejects stale data. Downloads do not mutate the run.

New export requests may pin `definition_version` and `definition_hash`. Assessed bundles use `geo-measurement-manifest/v2` and add `evidence-assessment.json` and `evidence-assessment.md`, bound to `geo-evidence-assessment/v1` / `brand-citation-audit/v1`. Unassessed exports retain v1 bytes. Already-exported runs always retain their original ZIP; the separate `geo-evidence-assessment-manifest/v1` companion contains the report, definition, saved measurement and original scores. Reports are recomputed and verified before rendering. Reproduce with `build_evidence_assessment(measurement, record, run_id=..., run_revision=...)` using the report's pinned definition/version and run metadata; no provider is needed.

The additive [0003 migration](migrations/versions/0003_brand_definitions.py) creates `run_brand_definitions`. Local SQLite initialization creates missing tables without altering existing records. Back up storage and wait for an idle API before restarting an existing installation; worker policies and grants need no change. SQLite upgrade, concurrent saves, rollback, old approval hashes and original export preservation are tested. PostgreSQL migration execution has not been verified in this environment.

Automatic setup requires both the API and measurement worker to load the updated code, because the saved preparation response now includes the inferred brand. Stop new submissions, wait for idle jobs, back up storage and restart both processes together; do not mix an older worker/API with new preparation records. Existing runs and manual definitions remain valid. No further table migration, policy change or budget grant is needed for this automation. Offline tests cover inferred and missing brands, unsupported quotes, restricted domains, manual override precedence, restart persistence and transaction rollback without provider replay. Actual live model extraction quality has not been retested.

### Evidence-Based Content Recommendations

The Recommendations step turns a saved measurement into content experiments for the original page, without another Web IQ or model call. [build_content_strategy](src/geo_agent/recommendations.py) uses nonexclusive English wording cues for definitions, how-to instructions, comparisons, quantified evidence, features/integrations and pricing/access. These are excerpt patterns, not a semantic assessment or verified full-page formats. The report also works for saved runs without a brand definition or a model-generated recommendation report.

- **Grounding recommendations:** show which types appear in returned excerpts, how many sources and queries contain them, exact quotations, source URLs and original-page matches. Each type suggests a change to test; missing capture evidence prompts a full-page check before adding content.
- **LLM recommendations:** separate model-reported citation counts from matching wording in final answers. Citation opportunities count only completed answers supplied that query's source. Evidence IDs remain query-scoped. Quoted answers and cited source passages support content experiments, not claims about hidden preferences or selection motives.
- **Content strategy:** prioritise the observed patterns, verify original facts and test one change against the same query set under fresh human approval and sufficient allowance. Returned frequency is not a causal ranking factor; a citation does not prove claim support, and uncited evidence may still influence an answer. Failed or missing results reduce coverage rather than becoming negative findings.

Authenticated owner/Geo.Operator `GET /api/v2/runs/{id}/content-strategy` returns `geo-content-strategy/v1` using method `english-excerpt-cues/v1`. The companion `/content-strategy/download` route returns a deterministic ZIP with the report in JSON and Markdown, the saved measurement and a hash-bound manifest. An optional `measurement_hash` rejects stale downloads with 409. Both routes are read-only and private/no-store. They do not alter approved inputs, run revisions, scores, existing exports, budgets or legacy model-report hashes. Browser report loading is revision-cached with stale-response guards and an explicit read-only reload after failures.

The browser no longer requests the optional recommendation model pass when starting a measurement. Existing model-generated tasks retain their accept/reject review and export requirements. Automatic content advice does not need that review record and grants no editing or publishing permission. Restart an idle API to activate the new routes; no worker restart, migration or policy/grant change is needed for this read-only report. Sources and offline checks: [report tests](tests/test_recommendations.py), [API tests](tests/test_measurement_api.py), [ZIP tests](tests/test_artifact_storage.py) and [browser regression](tests/check_measurement_browser.cjs).

### Shared Release Gates

The local implementation now has durable owner-bound v2 runs, jobs, per-operation claims and an independently launched worker; an asynchronous API; and query, model, score, recommendation and export views. Shared release still requires Entra sign-in, CSRF protection, deployment of the worker and PostgreSQL-backed storage. Local bearer authentication and SQLite are development controls, not shared-hosting controls.

Before enabling live execution, obtain explicit approval for the model roster, per-user allowance, retained data and hosting costs. A complete one-profile run is bounded at **6 Web IQ requests and 7 model requests**; a three-profile run uses **6 Web IQ requests and 17 model requests**. Each model call allows at most 2,000 output tokens. These operation limits are separate from the grant's monetary authorisation ceiling. Existing grants and usage ledgers remain immutable. Provisioning, deployment, a real three-model canary and colleague-isolation verification remain outstanding.

## Run the Mock Measurement Console

Set the checked-in mock policy, start the local server, then open http://127.0.0.1:8090/measurements and connect with the generated local API token file:

```powershell
$env:GEO_MEASUREMENT_POLICY = (Resolve-Path './geo-agent/measurement-policy.json').Path
$env:GEO_PORT = '8090'
& './geo-agent/.venv/Scripts/python.exe' -m geo_agent
```

Create a brief, confirm the three preparation operations, review and approve the exact five query pairs, then confirm five synthetic searches and fifteen synthetic evaluator calls. Evidence-based content recommendations follow automatically. Existing model-generated draft tasks still require accept/reject review before exporting; acceptance creates a planning file but does not authorise editing or publishing. Runs, jobs, evidence, reviews and exported ZIPs survive a browser refresh; only the selected run ID is retained in session storage. The token remains in memory and must be supplied again after reload. This policy makes no Web IQ or Foundry requests.

The console guides the active run through Brief, Prepare, Approve, Measure, Recommendations and Export. Completed stages remain available for review; query approval and Start measurement remain explicit human actions. Short requests display pending and recovery feedback without locking the interface for the lifetime of a worker job. Unsaved query edits require confirmation before navigation.

Authenticated `GET /api/v2/runs/{id}/progress` returns compact, owner-scoped job activity and recorded operation counts without provider metadata or operation keys. Visible, online pages poll at 1-5 second intervals, backing off on transient read failures. Unchanged run revisions do not trigger full-run downloads or panel reconstruction. Hidden/offline pages pause monitoring; returning resumes safe reads. Failed, unresolved and not-attempted work is not counted as completed, and optional recommendations are identified separately. Ambiguous responses trigger a saved-state read where possible, never an automatic mutation or provider retry. Older running APIs use saved-run/job polling until restarted to load the new endpoint.

The console matches [the original GEO Optimiser design](../geo-optimiser.html): its Microsoft-blue Clawpilot overrides, four-colour mark, cool surfaces, Segoe UI typography and compact controls. Guided stage navigation, keyboard focus management and light/dark layouts are retained. Lucide 0.468.0 icons are embedded locally with their ISC notice; the browser needs no CDN. The original reference file is unchanged.

Browser checks are split between an intercepted API contract test (no server required) and the real durable mock workflow (mock server required):

```powershell
npm install --prefix ./geo-agent/.data/browser-check --no-audit --no-fund playwright@1.58.2 @axe-core/playwright@4.10.2
node ./geo-agent/tests/check_measurement_browser.cjs
$env:GEO_BROWSER_BASE_URL = 'http://127.0.0.1:8090'
$env:GEO_BROWSER_TOKEN = (Get-Content './geo-agent/.data/local-api-token' -Raw).Trim()
try { node ./geo-agent/tests/check_measurement_browser_live.cjs }
finally { Remove-Item Env:GEO_BROWSER_TOKEN }
```

The intercepted check includes 65 real monitoring updates over more than a minute, transient status failures, dropped cancellation responses, older-API compatibility, immutable recommendation review and WCAG A/AA axe checks at 320, 390, 768 and 1440px in both themes. It asserts no full-run refetch or panel replacement during unchanged revisions. These checks do not measure live provider latency or replace manual accessibility review.

The durable browser check refuses a live policy before creating any run. It creates a local synthetic run and export; use an isolated `GEO_DATA_DIR` and its corresponding token file, and set `GEO_BROWSER_BASE_URL` to that mock server. Never aim this test at the live API on 8094. The original offline HTML and build plan remain unchanged.

## Run the Live Measurement Worker

The v2 API remains enqueue-only in live mode. A separate worker processes those jobs only when all live settings are present and an immutable grant matches the policy ID, complete policy hash and run owner. Each score-only run uses 13 operations with one profile or 23 with three profiles: one Browse, two preparation model calls, five Searches and five evaluator calls per profile. A grant can authorise 1 to 100 complete runs by multiplying every mandatory stage allowance by the same run count. Recommendations remain optional and are capped at one call per authorised run. Claims consume allowance before dispatch; failed and interrupted calls are not refunded or retried.

No live v2 policy or grant is checked in. After the model roster and direct endpoints have been discovered and the exact spend has been approved, start the API and worker with the same `GEO_DATA_DIR` and `GEO_MEASUREMENT_POLICY`. Set the worker-only grant separately:

```powershell
$env:GEO_MEASUREMENT_POLICY = 'C:\approved\clarity-live-policy.json'
$env:GEO_MEASUREMENT_BUDGET_GRANT = 'C:\approved\clarity-live-budget-grant.json'
$env:GEO_DATA_DIR = (Resolve-Path './geo-agent/.data').Path
$env:AZURE_OPENAI_ENDPOINT = 'https://<resource>.services.ai.azure.com/openai/v1/'
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = '<approved-preparation-deployment>'
& './geo-agent/.venv/Scripts/python.exe' -m geo_agent.measurement_worker
```

`WEBIQ_API_KEY` must already be available in `geo-agent/.env` or the process environment. The worker does not print credentials, probe providers or acquire a Foundry token at startup. Once running, it immediately leases queued jobs, so do not launch it before approving the policy, grant and pending workload.

## Analyse a Public Page

Open http://127.0.0.1:8090/chat?view=analysis and connect with the local API token file using its file picker. Credentials stay in browser memory, not local storage or URLs; reconnect after reloading. The server must have `GEO_ANALYSIS_POLICY` set to the absolute path of [analysis-policy.json](analysis-policy.json). The existing F5 chat configurations include it.

1. Enter a public HTTP(S) URL and locale, check the per-analysis call approval, and select **Analyse page**.
2. Inspect retrieval and analysis status, then purpose, audience, entities, answered questions and observations. Each finding identifies its basis and quotes retained evidence. Quote matching verifies traceability, not semantic correctness or independent truth.
3. Review improvement hypotheses and their verification steps. Indexed retrieval is limited to 10,000 characters: gaps in the excerpt are not proof of absence from the full page. There is no live crawling, JS rendering, technical SEO audit or inferred citation score.
4. Optionally provide an evaluation audience and goal and approve one query-generation call. This reuses the saved page and reserves one evaluation slot. Read the five exact queries, select **Approve these exact queries**, then separately select **Run 5 searches + 5 answer calls**. No search or evaluation starts during page analysis or query proposal.
5. Use saved analyses to reopen results. After a lost analysis response, **Check saved request** reuses its original idempotency key. The browser retains only that pending public-URL request in session storage, not credentials. Failed or interrupted attempts are never automatically retried; a server crash can require manual review.

The separate human-approved allowance is **10 page analyses** (up to 10 Browse + 10 Foundry analysis calls) and **10 optional citation evaluations** (up to 10 query proposals, 50 Searches and 50 evaluator calls). Each model call allows at most 2,000 output tokens. Attempts reserve slots persistently, including failures and rejected URLs; restarting does not reset usage. Each analysis permits at most one evaluation brief, which cannot be replaced after reservation. This allowance does not change the older chat or Daylesford-run budgets. Source: [analysis-policy.json](analysis-policy.json).

Authenticated API: `GET /analysis-policy`, `GET/POST /page-analyses`, `GET /page-analyses/{id}`, and `POST /page-analyses/{id}/evaluation`. Evaluation preparation requires audience, goal, idempotency key and `confirm_query_generation: true`. Query approval and execution use the existing run endpoints. APIs enforce owner isolation, persistent caps and exact input-hash approval.

The first approved live analysis, `3132a0f0-3447-4906-bcc3-b4e778caa7f2`, completed on 14 September 2026 using one Browse and one analysis call. It identified Daylesford's visitor proposition and proposed review of the excerpt's missing opening hours and ambiguous B Corp wording. All returned quotes passed exact passage matching. No citation evaluation was requested. [Open saved analysis](http://127.0.0.1:8090/chat?view=analysis&analysis=3132a0f0-3447-4906-bcc3-b4e778caa7f2); reading it makes no provider calls.

Browser regression check (isolated headless Edge, dummy credentials, intercepted APIs, server running on 8090):

```powershell
npm install --prefix ./geo-agent/.data/browser-check --no-audit --no-fund playwright@1.58.2
node ./geo-agent/tests/check_page_browser.cjs
```

This checks query approval gating, rendering untrusted text, failed retrieval, idempotent recovery and desktop/mobile layout without provider calls. Existing intermittent conversational SDK failures remain unresolved; this structured page-analysis path does not use that tool loop.

## Talk to the Agent

The chat server runs on http://127.0.0.1:8090/docs. From the workspace root, start a terminal conversation:

```powershell
& './geo-agent/.venv/Scripts/python.exe' -m geo_agent.chat
```

The client reads the local API token without displaying it. It prints the conversation ID and remaining model-request budget. Enter `/exit` to stop. To resume the verified conversation:

```powershell
& './geo-agent/.venv/Scripts/python.exe' -m geo_agent.chat --conversation 0d393b56-065a-4162-ae8f-01e027fd83f0
```

Examples: "Explain the exact-page score", "Which URL did q-2 cite?", and "Show the passage for q-2-source-1". Replies are model-generated interpretations; verify the linked evidence. Conversation history and tool traces are retained locally in the ignored SQLite database. They are not shared with the isolated visibility evaluators.

**Budget:** the original six-request chat allowance now has an approved **30-request top-up**, giving 36 chat model requests in total minus all recorded usage. Separately, **10 additional full evaluation runs** are authorised, each with one Browse, one query-generation call, five Searches and five evaluator calls: at most 60 Web IQ and 60 Foundry requests across those runs. The [approval record](budget-grant.json) is applied once; restarting does not add it again. Past usage and conversations are preserved. There is no monetary ceiling, and failed attempts consume budget. Query approval is still required before each evaluation.

Check current usage through authenticated `GET /chat-policy` and `GET /live-budget`. The chat assistant remains read-only; start new evaluations through `POST /briefs` with the approved Daylesford brief and a new idempotency key for each intended run, then inspect and approve its exact queries before starting it. Reusing a key returns the existing run rather than spending another slot. The allowance does not authorise other pages or unrestricted web tools. Failed or cancelled runs do not refund a slot.

To start the local server after stopping the current instance:

```powershell
$env:GEO_CHAT_POLICY = (Resolve-Path './geo-agent/chat-policy.json').Path
$env:GEO_LIVE_POLICY = (Resolve-Path './geo-agent/live-policy.json').Path
$env:GEO_BUDGET_GRANT = (Resolve-Path './geo-agent/budget-grant.json').Path
$env:GEO_ANALYSIS_POLICY = (Resolve-Path './geo-agent/analysis-policy.json').Path
$env:GEO_MEASUREMENT_POLICY = (Resolve-Path './geo-agent/measurement-policy.json').Path
$env:GEO_PORT = '8090'
& './geo-agent/.venv/Scripts/python.exe' -m geo_agent
```

For F5, select **GEO: Conversational Agent + Inspector** or **GEO: Conversational Agent Server**. Configurations live at the workspace root and use the agent-local virtual environment. Stop the current chat/debug server before F5, as ports 8090 and 5679 must be free. The Inspector task runs a background server; stop that task when finished debugging. No hosted deployment has been configured; `Deploy Agent to Foundry` is a separate next workflow requiring deployment approval and project configuration.

### API and Inspector

| Route | Behaviour |
| --- | --- |
| `GET /chat-policy` | Authorised run, retained-chat policy and remaining request budget |
| `POST /conversations` | Create a local conversation for the policy's run, with no model call |
| `GET /conversations/{id}` | Retrieve history, turn status, tool results and model usage |
| `POST /conversations/{id}/messages` | Send `message`, `expected_revision` and `idempotency_key` |
| `POST /v1/responses` or `/responses` | Constrained text Responses interface for engineering clients |

All these routes require the local bearer token. Client ownership is assigned server-side. The Responses adapter accepts one new user message, optional `previous_response_id`, and an optional `Idempotency-Key` header. Without that header, identical input and previous-response ID reuse the recorded turn. It rejects caller-supplied system roles, instructions, tools, files and `store=false` because this development workflow explicitly retains chat locally. Upstream model requests always use `store=False`; local retention is separate.

Inspector has been opened on port 8090. Its interactive authentication setup has not been verified; it must supply the local bearer header. The terminal client handles that automatically. The OpenAI Python client successfully consumed the adapter's text and SSE protocol in tests. SSE is buffered until the turn completes, not live token or tool-progress streaming. Inspect actual tool arguments/results through the conversation endpoint, not purported hidden reasoning.

### Verified Chat Behaviour

The live conversation `0d393b56-065a-4162-ae8f-01e027fd83f0` used `get_run_status`, `get_answer(q-2)` and then `get_evidence(q-2-source-1)` across two turns. It distinguished the uncited submitted URL from the cited `/us/` variant, resolved the follow-up reference from history, and refused the request to approve another evaluation or publish changes. Source: the owner-authenticated conversation endpoint and its retained tool trace.

The transport counts every attempted model HTTP request before sending it. SQLite enforces the original allowance plus immutable approved top-ups across restarts and concurrent conversations; each turn allows at most three model requests and six tool calls. Failed turns are retained, not silently replayed. A crash can leave a turn running and requires manual investigation. No automatic recovery or retries are enabled. Twelve turns per conversation and 4,000 characters per user message bound local history; start a new conversation when the turn limit is reached. This milestone explains existing evidence; it does not yet create briefs through chat, edit queries, generate validated recommendation tasks or offer a connected marketer dashboard.

## MCP Agent Flow

The MCP interface covers the six human stages without turning a provider workflow into one long tool call:

The completed local implementation and the remaining remote/live release work are documented in the [MCP next-gates developer plan](../geo-agent-mcp-next-gates-plan.md). A [Word companion](../geo-agent-mcp-next-gates-plan.docx) is included for stakeholder review.

1. The agent calls `geo_create_run`.
2. A human opens the returned `/measurements?run=...` link and selects **Authorise agent preparation**.
3. The agent refreshes `geo_get_run`, reads the issued authorization ID, and calls `geo_prepare_run`.
4. A separately running worker prepares the page and five query pairs. The agent inspects them with `geo_get_preparation` and `geo_get_query_plan`.
5. The human approves the exact query hash and selects **Authorise agent measurement**.
6. The agent calls `geo_start_measurement`, polls `geo_get_progress`, and reads bounded results, evidence, assessment and deterministic recommendations.
7. The agent creates immutable main or companion exports and reads allowlisted text entries. ZIP bytes and storage paths never enter model context.

Exact-query approval and legacy recommendation acceptance remain human-only REST/UI actions. An MCP confirmation, boolean argument or model message cannot create either decision. Agent stage authorizations expire, are consumed atomically, and are bound to one owner, principal, run revision, policy and operation ceiling.

The catalogue contains 20 tools across discovery, run creation, preparation, queries, brand configuration, measurement, progress/results, evidence/assessment, recommendations, cancellation and exports. Default responses are capped at 16 KiB, progress at 8 KiB and explicit details at 64 KiB. Lists use signed owner-bound continuations. Admission permits one active job, 20 globally queued jobs and four queued jobs per owner.

### Local stdio

Copy [examples/vscode-mcp.json](examples/vscode-mcp.json) into your chosen VS Code MCP configuration location or merge its `geo-agent` server entry. Do not place bearer tokens in the JSON. Start the separate synthetic worker against the same data directory:

```powershell
$env:GEO_DATA_DIR = (Resolve-Path '.\geo-agent\.data').Path
$env:GEO_MEASUREMENT_POLICY = (Resolve-Path '.\geo-agent\measurement-policy.json').Path
& '.\geo-agent\.venv\Scripts\python.exe' -m geo_agent.mock_measurement_worker
```

The stdio server command is `python -m geo_agent.mcp_server`. Import, initialization and tool discovery do not start a worker or acquire provider credentials.

### Loopback Streamable HTTP

Set a dedicated `GEO_MCP_AGENT_TOKEN` of at least 32 characters, distinct from `GEO_API_TOKEN`, before starting `python -m geo_agent`. The MCP endpoint is `http://127.0.0.1:8088/mcp`. Agent bearer tokens work only on the MCP mount and are rejected by human-only query approval, recommendation review and execution-authorization routes.

This is a local transport proof, not an internet deployment template. Foundry Agent Service requires a remote HTTPS endpoint. Do not expose this loopback bearer configuration publicly. MCP refuses a live execution policy unless `GEO_MCP_ALLOW_LIVE=true` is also set deliberately. That switch is not remote approval: a remote release still requires OAuth/Entra resource validation, HTTPS/Origin policy, PostgreSQL migrations, durable shared artifacts, hosted credentials and remote performance/isolation validation.

### Performance proof

Run the reproducible local Gate C harness:

```powershell
& '.\geo-agent\.venv\Scripts\python.exe' '.\geo-agent\tests\measure_mcp_performance.py'
```

It creates temporary data only, loads 1,000 full three-profile saved runs, holds one job active, opens five clients per transport and measures 200 calls over real stdio and loopback Streamable HTTP. It fails if warm p95 exceeds two seconds, a default response exceeds 16 KiB, or the tool catalogue exceeds 32 KiB. Local results do not certify a remote topology or live provider completion time.

## Run Locally

From the workspace root, using Python 3.11 or later:

```powershell
python -m venv geo-agent/.venv
& './geo-agent/.venv/Scripts/python.exe' -m pip install -e "./geo-agent[test]"
& './geo-agent/.venv/Scripts/python.exe' -m pytest geo-agent/tests -q
& './geo-agent/.venv/Scripts/python.exe' -m geo_agent
```

Use the configured Python interpreter if `python` resolves to a different installation. Direct dependencies are pinned to versions exercised on Windows with Python 3.13, including MCP 2.2.0, Agent Framework core 1.17.0 and OpenAI integration 1.14.2. The agent-local virtual environment is configured; a transitive dependency lock remains outstanding. [Agent Framework source](https://github.com/microsoft/agent-framework/tree/main/python).

Open http://127.0.0.1:8088/docs. The server binds only to loopback. On first launch it generates a local human API token in `.data/local-api-token`, excluded from Git. Open that file locally and enter its value in the documentation's **Authorize** control. It is a local development credential, not a Foundry or Web IQ key. Do not share it or reuse it as `GEO_MCP_AGENT_TOKEN`. The local data directory relies on your user account's filesystem permissions; remote Entra authentication and deployment hardening remain a separate release gate.

Press F5 with **GEO: Local Synthetic Backend** selected to debug the API. This is not yet an Agent Inspector endpoint. Stop the running server first, or set `GEO_PORT` to another free port. The launcher automatically loads the agent's [.env](.env) file, independent of the working directory; existing environment variables take precedence. Restart the server after changing settings. See [.env.example](.env.example) for the configuration template.

## Web IQ Configuration

The configured endpoints are verified against the supplied API references:

| API | Method and endpoint | Authentication |
| --- | --- | --- |
| [Browse reference](https://webiq.microsoft.ai/documentation/api-reference/browse/) | `POST https://api.microsoft.ai/v3/browse` | `x-apikey` header |
| [Web Search reference](https://webiq.microsoft.ai/documentation/api-reference/web/) | `POST https://api.microsoft.ai/v3/search/web` | `x-apikey` header |

Enter your Web IQ key after `WEBIQ_API_KEY=` in [.env](.env), not in the example file or the local API token file. This single setting is intended for a Web IQ key with access to both APIs. If separate grants require separate keys, the adapter configuration will need separate settings. The service also supports Entra tokens via `Authorization`; this key-based configuration does not implement that flow.

The file is excluded from Git. Keep it local; do not paste its contents into chat or commit it. The launcher loads it into the server environment without printing values or expanding `${...}` inside keys. Foundry uses your existing Azure CLI Entra sign-in, not a Foundry API key.

Loading a key alone does not enable live execution: the launcher requires an explicit `GEO_LIVE_POLICY` for `/briefs`, or the separately approved `GEO_ANALYSIS_POLICY` for page analysis. Without the respective policy, those routes return 503. Search uses bounded passages; Browse uses indexed markdown with `liveCrawl=none`, no dynamic rendering and no live-crawl fallback. Sources: the API references above, reviewed 14 September 2026.

## Foundry and Live Mode

Set `AZURE_OPENAI_ENDPOINT` to the resource's HTTPS `/openai/v1/` base URL and `AZURE_AI_MODEL_DEPLOYMENT_NAME` to your deployment. A direct `/openai/v1/responses` endpoint is also accepted and normalised. The existing `AZURE_AI_PROJECT_ENDPOINT` setting is accepted as a compatibility fallback **only when it contains one of those direct OpenAI URLs**; an actual `/api/projects/...` endpoint is not accepted by this adapter.

The runtime endpoint always comes from the environment so each developer can use an Azure resource they can access. The endpoint retained in existing policy files is audit metadata and is not enforced at startup. The deployment name must still match the policy so the governed model, owner, scope and persistent request budgets remain unchanged.

Sign in locally with `az login` if needed, selecting the tenant/account with inference access. The adapter obtains an in-memory token for `https://ai.azure.com/.default`. Structured query generation and isolated evidence-only evaluation use the OpenAI Responses API, with `store=False`, a 2,000 output-token cap per call and no automatic retries. No model receives the local approval credential. See the [Azure OpenAI Responses documentation](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/responses).

From the workspace root, opt in explicitly on a free port:

```powershell
$env:GEO_LIVE_POLICY = (Resolve-Path './geo-agent/live-policy.json').Path
$env:GEO_PORT = '8089'
python -m geo_agent
```

Open http://127.0.0.1:8089/docs and authenticate using the same local token. `GET /live-policy` shows the owner-scoped policy. `POST /briefs` requires that exact brief plus an `idempotency_key`; it performs preparation only. Review the returned five queries, then use the approval and start endpoints below. `/health` reports configuration, not a provider readiness probe; its `live_ready` flag remains false.

The checked-in [test policy](live-policy.json) defines the per-run limits: one indexed Browse, one query-generation call, five Searches and five evaluator calls for the specified Daylesford page, with local evidence retention. Its original single-run budget has been consumed. The separately approved [budget grant](budget-grant.json), enabled through `GEO_BUDGET_GRANT`, provides ten additional run slots with the same limits. Each slot retains its own call ledger and approval hash, and is allocated transactionally before preparation. Reading and exporting existing runs makes no new provider calls. Never delete the ledger or edit a grant to obtain unapproved capacity.

## Exercise the Workflow

1. `POST /fixture-runs` creates one fictional page, five editable buyer queries and one synthetic profile. It fetches nothing.
2. Inspect the returned inputs, `revision` and `approval_hash`. Optionally edit queries through `PUT /runs/{run_id}/queries`; revisions invalidate prior approval.
3. `POST /runs/{run_id}/query-approval` with `expected_revision` and `input_hash` records your approval of those exact inputs.
4. `POST /runs/{run_id}/start` with `expected_revision` and an `idempotency_key` executes the synthetic evaluator. The fixture yields 2/5 exact-page answers (40/100), labelled synthetic throughout.
5. Inspect status, events, source excerpts and citation matches through the run/evidence endpoints.
6. `POST /runs/{run_id}/exports` with `expected_revision` freezes the run. Download its `download_url` with the same bearer token.

Downloads from the existing API contain `manifest.md`, `run.json` and `scores.json`. They include approved inputs, answers and retained source excerpts, with branded/unbranded scores reported separately. Live records include provider trace IDs, response IDs and token usage. This legacy workflow does not generate recommendation tasks; the internal `recommendations-ready` state means evaluation finished. Its manifest uses `geo-manifest/v2`; the v2 measurement exporter is separately exposed through authenticated `/api/v2/runs/{run_id}/exports` and artifact routes. All exports require human review and grant no publishing permission.

## First Live Result

Run `979d9402-d7b0-47e1-aeaa-f25174f114f0` evaluated five human-approved queries in `en-GB` using deployment `gpt-5.6-sol`. All five completed, with no failed or missing outcomes and no retries.

| Measure | Result |
| --- | --- |
| Exact submitted page citations | 0/5, score 0/100 |
| Unbranded exact-page citations | 0/4 |
| Branded exact-page citations | 0/1 |
| Other Daylesford page cited | 1 answer cited `/us/our-locations/daylesford-farm` |
| Brand mentions | Daylesford appears in all five answers, observed by inspection, not an automated brand score |

This is a controlled evidence-packet baseline, not a measurement of native consumer assistants or a general visibility rating. The `/us/` URL was not treated as the submitted page: redirects and canonical equivalence have not been verified. Source-backed IDs establish citation traceability, not factual correctness of every generated claim.

Local evidence: [validated ZIP](.data/daylesford-first-live.zip) and [matching policy](.data/daylesford-first-live-policy.json), both ignored by Git. The first run retains the policy hash rather than an embedded policy object; the accompanying policy was validated against that hash. Future prepared runs embed the policy. The ZIP was checked for approval integrity, five completed responses, provider traces and resolvable citation IDs.

## Implemented Boundaries

- Pydantic contracts reject unknown fields and duplicate queries/profiles.
- SQLite transactions enforce ownership, expected revision, approval hash and idempotent start. Persistent live call claims prevent replay after failure or restart. This is not a durable resumable provider job queue: a crash can leave a run evaluating, requiring manual investigation and renewed authorisation before any additional spend.
- Approval covers the brief, snapshot, exact queries, locale, profiles, prompt versions and retrieval mode. Changing inputs clears approval.
- Source IDs must resolve within the answer's evidence packet. URL matching preserves path, query and trailing-slash distinctions; only normal URL parsing and fragments are normalised. Redirect/canonical relationships are not implemented.
- Completed answers without verified exact-page citations count as zero. Errors reduce coverage; no completed answers produces N/A. Regression tests reproduce the proposal's 5/12 = 42 and 50/50/25 profile scores.
- Live submissions require the exact owner, page, brief and deployment in the policy. A provider failure stops remaining calls. Failed live calls never turn into synthetic successes. The fixture evaluator refuses live and recorded runs.
- API authentication is local bearer-token authentication with server-assigned ownership. Approval is an authenticated endpoint, not an agent tool. Models receive only their bounded task inputs; the evaluator receives the query, locale and source packet, not the target-page brief.

## Remaining Setup and Limits

The direct inference connection works; the following broader setup remains separate:

| Item | Current position |
| --- | --- |
| Foundry Toolkit | No project is selected in the extension. The existing direct Azure AI connection is canonical and targets the dedicated GEO resource; hosted project integration is deferred. |
| Model roster | Confirmed: `gpt-5.6-sol` on `hackathon-2026-geo-optimiser` for preparation and ChatGPT-style evaluation; existing `gpt-5-mini` on `GPT5-Editor` for Copilot-style evaluation. Recommended Claude evaluator: `claude-sonnet-4-6` version `1`, `GlobalStandard`, MaaS capacity `1`, deployment name `claude-sonnet-4-6-geo` on `hackathon-2026-geo-optimiser` in `eastus`. The model is generally available with inference deprecation dated 10 February 2027. The subscription reports 80 available quota units, but this deployment has not been created because Anthropic's required industry declaration is still pending. |
| Foundry identity | Azure CLI is authenticated and the portable `azd` plus `microsoft.foundry` extension pass their dependency check when the portable directory is added to process-local `PATH`. Azure Identity was not added because its dependency lacked a usable Windows ARM64 wheel in this environment. |
| Web IQ | A non-empty key and the documented v3 Browse/Search endpoints are configured; earlier bounded Browse and Search calls succeeded. Live crawling remains disabled; expanding retrieval requires a separate safety review. |
| Local tooling | Portable ARM64 `azd` is under `%LOCALAPPDATA%/Programs/AzureDevCLI-local`; its path is not persisted. Azure CLI and azd sign-ins passed the preflight. The preflight resets PATH, so the portable executable must be resolved explicitly. |
| Runtime | Local durable jobs and mock worker are implemented; separate worker hosting, hardened deployment, automated cost accounting and a transitive dependency lock remain outstanding. |

Place service secrets in the local [.env](.env), environment variables or an approved secret store. The existing workspace `Credentials` file was not read or modified; adding it to `.gitignore` does not untrack it if it was already committed.

## Next Build Increment

The first one-profile `https://clarity.microsoft.com/` canary completed under its immutable 13-operation grant. New browser-testing grants may now authorise several complete URL runs for the same owner and allowed domains. The API token continues to identify the owner; the separate grant remains the spending boundary and records exact operation allowances, policy hash, monetary authorisation ceiling and approval. Every run still requires its own exact query approval before evaluation.

After the local canary, prepare the connected v2 workflow for shared hosting: add Entra authentication and CSRF protection, move persistence to PostgreSQL, deploy the worker separately and complete colleague-isolation tests. Keep the current saved-page analysis and single-profile citation workflow under their existing allowances. Chat remains bound to the original run. All live calls require sufficient approved capacity and exact query approval. Canonical equivalence, CMS publishing and Work IQ remain separate later work, with their own review and approvals.