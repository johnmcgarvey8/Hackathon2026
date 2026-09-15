# GEO Agent: Conversational Evidence Assistant

The local GEO agent analyses public page URLs and holds multi-turn conversations about saved evaluation evidence. Page analysis returns observed and inferred findings with validated verbatim quotes, plus improvement hypotheses requiring review. The Microsoft Agent Framework chat remains read-only and bound to its existing saved run; the browser's separate analysis workflow uses structured Foundry calls. Neither model can approve evaluations or publish changes. New multi-model measurement and validated recommendation libraries are offline-tested but are not connected to the browser or live runner. No Azure resources were provisioned by this build. The [build plan](../geo-agent-build-plan.md) remains the roadmap; the [offline proposal](../geo-optimiser.html) is unchanged.

## V2 Backend Milestone

The recommended release is a shared website for hackathon colleagues, backed by this FastAPI service and Foundry model inference. MCP remains a possible later wrapper. The current local website still uses its original single-profile, human-approved workflow; it is not a shared multi-model application yet.

| Component | Implemented and offline-tested |
| --- | --- |
| [Versioned contracts](src/geo_agent/contracts.py) | Five priority-ranked chat/search query pairs, exact page references, three separately configurable simulation profiles, immutable input fingerprints and independently retained retrieval packets. V1 serialisation and approval hashes remain unchanged. |
| [Paired planner](src/geo_agent/foundry.py) | `Foundry.propose_pairs` reuses the saved snapshot, validates quoted passages and rejects duplicate queries or invalid priority order. |
| [Evaluator providers](src/geo_agent/providers.py) | OpenAI Responses for ChatGPT-style and Copilot-style profiles; Anthropic Messages via `anthropic==0.125.0` for Claude-backed answers. Canonical endpoint validation, approved prompt guards, bounded evidence, 2,000 output tokens and no automatic retries. |
| [Measurement scoring](src/geo_agent/evaluation.py) | Exact-page citation rate, errors and coverage; separate Web IQ presence/position metrics; same-domain, unsupported-citation and raw-mention details. Model comparisons use only the common completed query set. |
| [Recommendations](src/geo_agent/recommendations.py) | Up to two saved alternatives per query and three draft hypotheses. Exact quotes resolve within the correct query. Reports bind to both inputs and complete measurement data. No candidates means no recommendation model call. |
| [Draft exports](src/geo_agent/artifacts.py) | `render_measurement_bundle` validates reports and produces deterministic ZIPs containing measurement data, scores and recommendations. No provider call or publishing permission. Existing run exports are unchanged. |

All profiles are controlled evidence-packet simulations, not measurements of the consumer Copilot, Claude or ChatGPT services. The same ordered search packet must be used for each profile's answer to a query. Target-page text is not added to evaluator input; only returned search evidence can earn citation credit. A Claude label requires the Anthropic adapter, not a GPT persona. Actual deployments, access and underlying model identities still require live preflight.

The citation score is `round(100 * completed answers citing the exact page / completed answers)`. Failed calls reduce coverage; no completed answers means N/A. Priority does not weight the score. Search absence means the exact page was not in that saved top-five packet, not global invisibility or rank six. Recommendations are hypotheses: quote checks prove traceability, not semantic correctness, causal ranking factors or guaranteed gains.

The v2 ZIP contains `manifest.json`, `manifest.md`, `measurement.json`, `scores.json` and `recommendations.json`. Its manifest is `geo-measurement-manifest/v1`, distinct from the legacy run manifest `geo-manifest/v2`. With the installed package, scores can be recomputed from the retained JSON:

```python
from pathlib import Path
from geo_agent.contracts import MeasurementResults
from geo_agent.evaluation import measurement_scores

measurement = MeasurementResults.model_validate_json(Path("measurement.json").read_bytes())
scores = measurement_scores(measurement)
```

The exported input hash is an integrity fingerprint, not evidence that a human approved execution. These library contracts do not yet persist a v2 run, approval, job or attempt ledger. The caller must enforce ownership, approval and budget before any provider invocation. A missing recommendation report exports as JSON `null`, allowing saved scores to remain available without inventing recommendations.

Verification on 15 September 2026: **395 tests passed**, with two existing Starlette/AnyIO dependency deprecation warnings. The [component integration test](tests/test_artifacts.py) uses SDK mock transports to exercise one Browse, one analysis, one paired plan, five searches, fifteen answer attempts and one recommendation call. It tests full completion and an isolated Claude failure, shared evidence packets, redacted errors and score recomputation from the ZIP. This is a test composition, not a production orchestrator. No real provider calls were made during this milestone.

### Shared Release Gates

The next implementation work is durable owner-bound v2 runs, jobs and per-operation call claims; an asynchronous API; Entra sign-in and CSRF protection; and the query, model, score and recommendation views in the existing website. Shared hosting needs durable PostgreSQL storage, not App Service local SQLite. Claims must commit before network dispatch; ambiguous interrupted attempts must never be replayed automatically.

Before enabling live multi-model execution, obtain explicit approval for the model roster, new team/per-user allowance, retained data and hosting costs. A complete run is bounded at **6 Web IQ requests and 18 model requests**, each model call allowing at most 2,000 output tokens. This is not a monetary cap. No new allowance is enabled: existing local policies, grants and usage ledgers are unchanged and do not fund this new workflow. Provisioning, deployment, a real three-model canary and colleague-isolation verification remain outstanding.

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

## Run Locally

From the workspace root, using Python 3.11 or later:

```powershell
python -m venv geo-agent/.venv
& './geo-agent/.venv/Scripts/python.exe' -m pip install -e "./geo-agent[test]"
& './geo-agent/.venv/Scripts/python.exe' -m pytest geo-agent/tests -q
& './geo-agent/.venv/Scripts/python.exe' -m geo_agent
```

Use the configured Python interpreter if `python` resolves to a different installation. Direct dependencies are pinned to versions exercised on Windows ARM64 with Python 3.13, including Agent Framework core 1.17.0 and OpenAI integration 1.14.2. The agent-local virtual environment is configured; a transitive dependency lock remains outstanding. [Agent Framework source](https://github.com/microsoft/agent-framework/tree/main/python).

Open http://127.0.0.1:8088/docs. The server binds only to loopback. On first launch it generates a local API token in `.data/local-api-token`, excluded from Git. Open that file locally and enter its value in the documentation's **Authorize** control. It is a local development credential, not a Foundry or Web IQ key. Do not share the token or expose this server publicly. The local data directory relies on your user account's filesystem permissions; Entra authentication and deployment hardening are not implemented.

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

Downloads from the existing API contain `manifest.md`, `run.json` and `scores.json`. They include approved inputs, answers and retained source excerpts, with branded/unbranded scores reported separately. Live records include provider trace IDs, response IDs and token usage. This legacy workflow does not generate recommendation tasks; the internal `recommendations-ready` state means evaluation finished. Its manifest uses `geo-manifest/v2`; the offline prototype's v1 task contract is not silently reused. The new measurement exporter described above is separate and is not exposed through these routes. All exports require human review and grant no publishing permission.

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
| Foundry Toolkit | No selected project; direct model inference works. Local Inspector configuration exists; hosted project integration is deferred. |
| Model identity | Deployment name and provider-returned model string are retained. Underlying model version is not independently established. |
| Foundry identity | Azure CLI Entra inference succeeded. Azure Identity was not added because its dependency lacked a usable Windows ARM64 wheel in this environment. |
| Web IQ | Browse and Search succeeded. Live crawling remains disabled; expanding retrieval requires a separate safety review. |
| Local tooling | Portable ARM64 `azd` is under `%LOCALAPPDATA%/Programs/AzureDevCLI-local`; its path is not persisted. Azure CLI and azd sign-ins passed the preflight. The preflight resets PATH, so the portable executable must be resolved explicitly. |
| Runtime | Local synchronous API, no durable worker, hardened deployment, automated cost accounting or transitive dependency lock. |

Place service secrets in the local [.env](.env), environment variables or an approved secret store. The existing workspace `Credentials` file was not read or modified; adding it to `.gitignore` does not untrack it if it was already committed.

## Next Build Increment

Connect the tested v2 components through durable owner-bound execution and the shared website, following the release gates above. Keep the current saved-page analysis and single-profile citation workflow under their existing allowances. Chat remains bound to the original run. All further calls require sufficient approved capacity; citation searches additionally require exact query approval. Canonical equivalence, CMS publishing and Work IQ remain separate later work, with their own review and approvals.