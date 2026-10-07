# GEO MCP Server: Next Gates Plan

30 September 2026 | Developer handoff | Gate C.5 interactive check, Gate D and Gate E remain gated

## Executive Summary

The local MCP implementation is complete, the committed VS Code workspace path has passed its automated Gate C.5 proof, and the integrated Python baseline is green at 615 tests. One interactive step remains: reload the workspace, approve MCP trust and complete a Copilot-driven synthetic run.

The three priorities are **close local acceptance**, **confirm production decisions** and **then harden the governed service**. Preserve the existing human checkpoints, scientific semantics, provider-operation counts and immutable exports. Do not start Gate D or enable live MCP merely because the local mock workflow is successful.

Current source and operating guidance:

- [GEO agent README](geo-agent/README.md)
- [MCP server](geo-agent/src/geo_agent/mcp_server.py)
- [Shared application service](geo-agent/src/geo_agent/measurement_service.py)
- [Agent identity and execution authorisation](geo-agent/src/geo_agent/agent_access.py)
- [Persistence and job lifecycle](geo-agent/src/geo_agent/persistence.py)
- [Local performance baseline](geo-agent/docs/mcp-local-performance-baseline.json)
- [VS Code integration baseline](geo-agent/docs/mcp-vscode-integration-baseline.json)

## Planning Framework

| Section | Description | Notes |
| --- | --- | --- |
| Startle | The automated Copilot-equivalent journey completes all 23 synthetic operations, reconnects after restart and now sits on a 615-test green baseline. | Only the interactive VS Code trust and Copilot check remains in Gate C.5. |
| WIIFM | A committed workspace path gives Copilot the governed GEO workflow without copied configuration or live-provider spend. | Agents gain preparation, measurement, evidence and export access without gaining human approval rights. |
| Needs and Challenges | Close the interactive trust check before adding remote identity, storage, worker isolation, observability and controlled live spend. | The stdio MCP path and repository-wide baseline are proven locally. |
| Define Questions | Can the workspace pass a real Copilot run, then preserve owner isolation and responsiveness through a hosted topology? | Gate C.5 closes local usability. Gate D answers remote readiness. Gate E answers live acceptance. |
| Cornerstone | Finish and stabilise the existing bounded asynchronous workflow before productionising it. | REST, UI and MCP must continue to share one application service and one durable job model. |
| Supporting Evidence 1 | All 20 tools work over real stdio and Streamable HTTP clients. | Tool discovery, structured results and early-return submissions are tested. |
| Supporting Evidence 2 | Lifecycle blockers found during assessment have regression coverage. | Renewable fenced leases, stable replay, checkpointed output, queue limits and migration recovery are implemented. |
| Supporting Evidence 3 | The current local performance and payload gates pass with margin. | Stdio p95 19 ms; Streamable HTTP p95 117 ms; catalogue 17,970 bytes; maximum measured response 4,982 bytes. |
| Repeat Cornerstone | Close the interactive local check, then harden the same workflow for shared use. | Keep the human approval and authorisation boundaries intact. |
| Conclusion and next steps | Complete the interactive Gate C.5 check before an approved Gate D deployment. | Stop if identity isolation, migration safety, remote p95 or live completion experience fails. |

## 1. Current Baseline

### Completed gates

| Gate | Status | Evidence |
| --- | --- | --- |
| A. SDK and contract proof | Complete | Official `mcp==2.2.0`; 20 tools; real stdio and Streamable HTTP clients; structured results; no provider activity during import or discovery. |
| B. Workflow and governance parity | Complete locally | Six-stage mock journey; exact human query approval; separate agent execution authorisation; owner-scoped reads; 23-operation three-profile path; deterministic recommendations add no provider call. |
| C. Local performance and payloads | Complete | Five clients per transport, 1,000 representative saved runs, one active job and 200 requests per transport. See [baseline](geo-agent/docs/mcp-local-performance-baseline.json). |
| C.5. VS Code and Copilot integration | Automated proof complete; interactive check pending | Committed `.vscode/mcp.json`; 20-tool discovery; synthetic human/API/worker flow; 23 operations; export read; restart/reconnect. See [evidence](geo-agent/docs/mcp-vscode-integration-baseline.json). |

### Verified local measurements

| Measure | stdio | Streamable HTTP | Gate |
| --- | ---: | ---: | ---: |
| Warm p95 | 19 ms | 117 ms | At most 2 seconds |
| Maximum request duration | 136 ms | 169 ms | Reported separately from p95 |
| Maximum response | 4,972 bytes | 4,982 bytes | Default at most 16 KiB; progress at most 8 KiB |
| Tool catalogue | 17,970 bytes | 17,970 bytes | At most 32 KiB |

The MCP, human handoff, configuration, evaluation and worker suites passed **58 tests**. The browser and accessibility harness passed with no external calls. The complete repository suite passed **615 tests** with 19 existing Pydantic serialization warnings.

### Implemented safety properties

- Agent identity is supplied outside tool arguments.
- Exact-query approval and legacy recommendation decisions remain human-only.
- Paid stages require a human-issued, principal/run/revision/policy-bound execution authorisation.
- One job may be active globally; at most 20 jobs may be queued globally and four per owner.
- Submission returns a durable job ID. Provider work never runs inside an MCP tool handler.
- Job leases renew with fencing tokens. Stale workers cannot commit over cancellation or a newer lease.
- Validated provider outputs are checkpointed with operation claims.
- Idempotent replay survives terminal run revisions without repeating provider calls.
- Lists and detail reads are bounded by signed owner-scoped continuations.
- Main and companion exports have explicit kinds. ZIP bytes and storage keys do not enter model context.
- Streamable HTTP is stateless, preventing one authenticated agent from controlling another agent's protocol session.
- Live MCP fails closed unless `GEO_MCP_ALLOW_LIVE=true` is set deliberately.

## 2. Deliberately Incomplete

The following are not defects in the local milestone. They are release gates:

- No public or internet-facing MCP deployment.
- No production Entra/OAuth token verifier or protected-resource metadata.
- No PostgreSQL deployment or multi-instance transaction proof.
- No Azure Blob Storage or equivalent durable shared artifact implementation.
- No production browser session, secure-cookie or CSRF design.
- No hosted worker identity or cached managed credential strategy.
- No remote topology performance result.
- No live MCP canary or new provider allowance.
- No proof that the three-profile live roster and every configured deployment are currently accessible.
- No recorded interactive VS Code trust and Copilot run against the committed `geoAgent` entry.

Do not describe the current loopback bearer middleware as production authentication. Do not expose it publicly.

## 3. Gate D: Remote Deployment Readiness

Gate D requires a separately approved non-production environment. Complete the work in the order below because identity, persistence and worker dispatch share transaction boundaries.

### D0. Confirm deployment decisions

Before provisioning or editing production configuration, obtain and record:

| Decision | Required input |
| --- | --- |
| Hosting | Approved Azure service and region. Azure Container Apps is the recommended starting point for one API app plus one separately scaled worker, but the platform owner must confirm it. |
| Foundry consumer | Target project, agent identity type, allowed tools and public HTTPS reachability requirements. |
| Identity | Entra tenant, API audience, delegated scopes, application scopes, owner mapping and operator group/role. |
| Data | PostgreSQL server/database, retention, encryption, backup and restore owner. |
| Artifacts | Storage account/container, retention, malware/content policy and download-authorisation pattern. |
| Network | Public ingress policy, WAF/reverse proxy, Host/Origin allowlist, DNS and certificate owner. |
| Providers | Approved Web IQ and model endpoints, managed identities/connections and egress policy. |
| Spend | Non-production hosting budget only. Live provider allowance remains a separate Gate E decision. |

**Stop condition:** do not provision a default architecture while the interactive Gate C.5 check, ownership, tenant, region, data retention or ingress is unresolved.

### D1. Generalise persistence and artifacts

1. Replace path-only bootstrap assumptions with injected database and artifact configurations.
2. Keep SQLite for local development; use PostgreSQL for shared hosting.
3. Run Alembic to `head`, including:
   - [0004 MCP foundation](geo-agent/migrations/versions/0004_mcp_execution_foundation.py)
   - [0004 projects](geo-agent/migrations/versions/0004_projects.py)
   - [0005 project agent budgets](geo-agent/migrations/versions/0005_project_agent_budgets.py)
   - [0006 project conversation runs](geo-agent/migrations/versions/0006_project_conversation_runs.py)
   - [0007 project LLM operations](geo-agent/migrations/versions/0007_project_llm_operations.py)
   - [0005 nullable export reservations](geo-agent/migrations/versions/0005_nullable_export_reservations.py)
   - [0008 export request run scope](geo-agent/migrations/versions/0008_export_request_run_scope.py)
4. Do not use `metadata.create_all()` as the shared-deployment migration mechanism.
5. Rehearse upgrade and rollback/recovery on copied or synthetic databases.
6. Exercise real PostgreSQL transactions for:
   - one-active-job admission;
   - 20-global and four-owner queue races;
   - execution-authorisation consumption;
   - idempotent run/job/export creation;
   - `FOR UPDATE SKIP LOCKED` leasing;
   - lease renewal versus recovery;
   - artifact reservation and finalisation.
7. Implement a durable `ArtifactStorage` adapter, preferably Azure Blob Storage:
   - deterministic owner/run/kind/content-hash keys;
   - create-if-absent semantics;
   - content length and SHA-256 verification on download;
   - no raw storage key, credential or unrestricted SAS in tool results;
   - immutable main measurement ZIP;
   - separately versioned assessment and content-strategy companions.
8. Keep protected downloads behind application owner checks.

**Completion evidence:** PostgreSQL integration tests, migration rehearsal log, artifact integrity tests, backup/restore exercise and unchanged approval/export hashes for representative existing runs.

### D2. Implement production MCP identity

Use the MCP SDK's resource-server support rather than extending the loopback bearer middleware.

1. Configure `TokenVerifier` and `AuthSettings` for the approved Entra resource.
2. Publish protected-resource metadata required by target MCP clients.
3. Validate issuer, audience, expiry, scopes and tenant on every request.
4. Map verified delegated users to their owner identity.
5. Require explicit server-side delegation for application or managed-identity agents.
6. Keep incoming MCP tokens separate from provider credentials. Never forward them to Web IQ or model endpoints.
7. Enforce least-privilege scopes:
   - `geo.read`;
   - `geo.write`;
   - `geo.execute`;
   - `geo.cancel`;
   - `geo.export`.
8. Apply owner checks to tools, resources, continuations, caches, artifacts and error behaviour.
9. Reject agent credentials at:
   - exact-query approval;
   - recommendation review;
   - human execution-authorisation creation.
10. Test a matrix covering same owner, different owner, delegated user, application identity, expired token, wrong tenant, wrong audience and missing scope.

**Completion evidence:** automated identity matrix with no cross-owner enumeration, reads, downloads, cancellation, authorisation use or session control.

### D3. Harden the human web path

If the browser UI is deployed remotely:

1. Replace local bearer entry with the approved human authentication flow.
2. Use secure, HTTP-only, same-site cookies when using sessions.
3. Add CSRF protection to every state-changing browser route.
4. Use an explicit Host/Origin allowlist. Do not enable blanket CORS.
5. Retain the current content-security, no-store and no-referrer protections.
6. Keep human and agent credentials distinguishable at the authentication boundary.
7. Re-run the complete measurement browser and accessibility harness.

**Completion evidence:** CSRF negative tests, cookie/header inspection, human/agent route separation and browser flow parity.

### D4. Deploy API and worker separately

Recommended topology:

```text
Hosted/local agent
      |
      | HTTPS + Entra token
      v
Ingress / Host and Origin policy
      |
      v
FastAPI + /mcp + human REST/UI
      |
      +---- PostgreSQL
      |
      +---- durable artifact storage
      |
      v
Separately deployed worker
      |
      +---- Web IQ
      +---- approved model endpoints
```

Requirements:

- API startup must not apply grants, acquire provider credentials or start the worker.
- The worker must resolve the persisted owner, policy and grant before dispatch.
- Use a managed identity or approved workload identity. Interactive Azure CLI login is not a hosted dependency.
- Cache credentials/connections safely; keep existing provider timeout and no-automatic-retry semantics.
- Add liveness and readiness separately:
  - API readiness checks database/schema and artifact configuration without a provider call;
  - worker heartbeat reports last seen/current job;
  - provider readiness remains an operator diagnostic, not a public health probe.
- Emit structured logs and traces for request ID, principal type, run/job IDs, queue time, handler time, provider time, operation outcome and sanitised error code.
- Never log tokens, prompts containing secrets, full model answers or retained passages by default.

**Completion evidence:** restart/reconnect tests, worker interruption recovery, graceful shutdown, no discovery side effects and observable job progress after API/worker replacement.

### D5. Repeat Gate C remotely

Run the same logical workload through the deployed HTTPS endpoint:

- five simultaneous authenticated clients;
- one intentionally slow active worker job;
- at least 1,000 representative saved runs in PostgreSQL;
- at least 200 control/read requests;
- authentication, serialisation and network round trip included.

Acceptance targets:

| Target | Required result |
| --- | --- |
| Warm p95 | At most 2 seconds for capabilities, run list/summary, progress, admission, cancellation and bounded result/report reads |
| Default response | At most 16 KiB |
| Progress response | At most 8 KiB |
| Explicit detail | At most 64 KiB with continuation |
| Catalogue | At most 32 KiB, or the target client's stricter limit |
| Queue | One active, 20 global queued, four queued per owner under concurrent API and MCP submissions |
| Provider calls from reads | Zero |

Also record cold start, DNS/TLS, token acquisition, database, serialisation and network components separately.

**Gate D decision:** pass only when migrations, identity isolation, artifacts, browser security, worker operation and remote performance all have saved evidence. A successful local test is not sufficient.

## 4. Gate E: Separately Authorised Live Canary

Gate E starts only after Gate D passes and John explicitly approves provider calls and spend.

### E0. Record live authority

The approval must name:

- target URL/domain and locale;
- one-profile or three-profile roster;
- exact provider/model deployments;
- maximum operation allowances;
- maximum authorised cost;
- retention and artifact location;
- operator and rollback owner;
- date/time window;
- whether recommendation-model calls are allowed. Default is no.

Never edit an existing grant, reset consumption, rename a policy or reuse a consumed allowance to obtain capacity.

### E1. One-profile canary

Expected score-only operation ceiling:

`1 Browse + 1 analysis + 1 query plan + 5 searches + 5 evaluations = 13 operations`

Procedure:

1. Verify the approved provider endpoints and deployment access without changing the roster.
2. Create the run through MCP.
3. Have the human authorise preparation in the UI.
4. Prepare and inspect the retained snapshot, analysis and five query pairs.
5. Have the human approve the exact query hash and authorise evaluation.
6. Start through MCP and monitor durable progress.
7. Verify:
   - exactly 13 attempted operations;
   - no automatic retry;
   - no hidden recommendation-model call;
   - five shared retrieval packets;
   - five answers for the single profile, subject to explicit failures;
   - failures lower coverage rather than becoming zero scores;
   - deterministic content strategy adds zero provider calls;
   - immutable export matches the saved measurement and approval hashes.
8. Record queue wait, handler/serialisation time, each provider duration, token usage, failure code and charged count.

**Stop condition:** do not continue to three profiles if access, accounting, evidence semantics, human handoff or completion time is unsatisfactory.

### E2. Three-profile canary

Expected score-only operation ceiling:

`1 Browse + 1 analysis + 1 query plan + 5 searches + 15 evaluations = 23 operations`

Additional checks:

- verify actual access to every configured provider/deployment, especially the Claude-backed adapter;
- give every profile the same ordered source packet for each query;
- keep profile histories isolated;
- compare profiles only on the common completed query set;
- preserve exact-page, same-domain, raw mention and configured brand distinctions;
- verify MCP introduces no extra paid operation compared with REST using the same backend.

### E3. Release decision

Release choices:

| Outcome | Decision |
| --- | --- |
| Gate D passes; live completion and results are satisfactory | Approve limited shared live use with the tested roster and limits. |
| Control path passes; live completion is too slow | Keep asynchronous saved-evidence access, narrow the roster, or stop. Do not call fast submission a successful full experience. |
| Identity, isolation, accounting or evidence semantics fail | Stop shared live use and retain local/mock or read-only operation. |
| One profile passes; three profiles fail | Release only the explicitly tested one-profile mode, if separately approved. |

## 5. Work Breakdown and Dependencies

| ID | Work item | Completion evidence | Depends on |
| --- | --- | --- | --- |
| `interactive-vscode-proof` | Reload VS Code, approve `geoAgent` trust and complete one Copilot-driven synthetic run | Saved interactive acceptance note | Automated Gate C.5 |
| `remote-decisions` | Confirm hosting, identity, data, network, provider and budget owners | Approved decision record | `interactive-vscode-proof` |
| `postgres-artifacts` | Add PostgreSQL and durable artifact configuration/adapters | Integration tests and migration rehearsal | `remote-decisions` |
| `entra-mcp-auth` | Implement MCP SDK OAuth/Entra resource validation and delegation | Identity matrix | `remote-decisions` |
| `human-web-auth` | Implement production human sessions and CSRF | Browser security tests | `remote-decisions` |
| `hosted-worker` | Deploy separately managed policy/owner-aware worker | Restart/recovery and heartbeat evidence | `postgres-artifacts` |
| `remote-observability` | Add safe logs, traces and dashboards | Saved telemetry for test workload | `hosted-worker`, `entra-mcp-auth` |
| `remote-gate-c` | Repeat five-client/1,000-run performance and admission proof | Gate D performance report | All Gate D implementation |
| `live-authority` | Record explicit canary URL, roster, allowance, cost and retention | Approved policy and immutable grant | Gate D pass |
| `live-one-profile` | Run 13-operation canary | Exact accounting and latency report | `live-authority` |
| `live-three-profile` | Run 23-operation canary | Roster access, parity and latency report | `live-one-profile`; separate approval if required |
| `release-decision` | Select shared, narrowed, read-only or stopped mode | Owner-reviewed decision | Completed canaries |

Do not parallelise edits to persistence, migration, execution authorisation and worker leasing. Those surfaces share invariants and should be integrated sequentially.

## 6. First-Day Checklist for the Next Developer

1. Read this plan and the [GEO agent instructions](geo-agent/AGENTS.md).
2. Read [README: MCP Agent Flow](geo-agent/README.md#mcp-agent-flow).
3. Confirm `.vscode/mcp.json` contains the secret-free `geoAgent` entry and the worktree is clean.
4. Run **GEO: Bootstrap Python Environment**, or create the environment directly:

   ```powershell
   python -m venv geo-agent\.venv
   & '.\geo-agent\.venv\Scripts\python.exe' -m pip install -e '.\geo-agent[test]'
   ```

5. Re-run the MCP and VS Code acceptance baseline before changing infrastructure:

   ```powershell
   & '.\geo-agent\.venv\Scripts\python.exe' '.\geo-agent\tests\verify_vscode_mcp.py'
   & '.\geo-agent\.venv\Scripts\python.exe' '.\geo-agent\tests\verify_vscode_mcp_e2e.py'
   & '.\geo-agent\.venv\Scripts\python.exe' -m pytest `
       '.\geo-agent\tests\test_api.py' `
       '.\geo-agent\tests\test_configuration.py' `
       '.\geo-agent\tests\test_evaluation_workflow.py' `
       '.\geo-agent\tests\test_mcp_server.py' `
       '.\geo-agent\tests\test_mcp_workspace.py' `
       '.\geo-agent\tests\test_measurement_api.py' `
       '.\geo-agent\tests\test_measurement_worker.py' -q -p no:cacheprovider
   node '.\geo-agent\tests\check_measurement_browser.cjs'
   & '.\geo-agent\.venv\Scripts\python.exe' geo-agent\tests\measure_mcp_performance.py
   ```

6. Run the full Python suite and confirm the 615-test baseline before Gate D.
7. Inspect these code surfaces before changing them:
   - [MCP registration and transports](geo-agent/src/geo_agent/mcp_server.py)
   - [Application service and bounded views](geo-agent/src/geo_agent/measurement_service.py)
   - [Principal and execution-authorisation contracts](geo-agent/src/geo_agent/agent_access.py)
   - [Run/job/artifact persistence](geo-agent/src/geo_agent/persistence.py)
   - [Lease heartbeat and failure finalisation](geo-agent/src/geo_agent/worker.py)
   - [Web/UI bootstrap](geo-agent/src/geo_agent/api.py)
   - [Live worker composition](geo-agent/src/geo_agent/live_runtime.py)
8. Complete the interactive VS Code trust and Copilot run.
9. Obtain the D0 decisions before provisioning anything.
10. Keep `GEO_MCP_ALLOW_LIVE` unset or false until Gate E authority is recorded.

## 7. Invariants the Next Developer Must Preserve

- Five query pairs, IDs `q-1` through `q-5`, with priorities one through five.
- Separate chat and grounding queries.
- Page quotes validated against the retained snapshot.
- Live roster contains one or three profiles, never two.
- The same ordered retrieval packet reaches every profile for a query.
- The target page is not injected into answers merely to earn citation credit.
- No automatic provider retry.
- Claim and charge occur before dispatch.
- Failed and ambiguous attempts remain charged.
- Zero completed answers remains N/A.
- Common completed query set controls profile comparison.
- Human query approval binds the exact current input hash.
- Agent execution authorisation is not human query approval.
- Main measurement exports remain frozen after later brand-definition changes.
- No arbitrary HTTP, provider execution, grant modification, CMS editing or publishing tool is added.

## 8. Required Handoff Evidence

The next developer should leave:

- architecture decision record for hosting and identity;
- migration and rollback/recovery log;
- PostgreSQL concurrency test report;
- identity/isolation matrix;
- remote Gate C performance JSON;
- deployment and worker runbook;
- retained trace/dashboard links;
- explicit Gate E approval record, if granted;
- one-profile and three-profile live accounting reports, if run;
- final release decision with stop/narrow rationale.

## Recommendation

Proceed with Gate D only after the interactive Gate C.5 check passes and the platform owner approves the target environment and identity model. Keep the current synthetic MCP path available for development and demos. Do not provision live provider capacity or set `GEO_MCP_ALLOW_LIVE=true` until Gate D has passed and Gate E has separate written authorisation.
