# GEO Optimiser: Live Agent Build Plan

10 September 2026 | Proposed implementation | Planning only

## Executive Summary

Build one GEO agent that guides marketers from a URL and approved buyer queries to a traceable visibility report and versioned Markdown tasks. Keep retrieval, scoring, approvals and file validation in deterministic services. Use LLMs for interpreting intent, answering queries, comparing passages and proposing edits. [S1]

The recommended first release is a local, authenticated live-service vertical slice: one public page, 5-10 approved queries, up to three Foundry evaluator profiles and an evidence-linked Markdown manifest plus recommendation files. A subsequent milestone proves one content-editor and review-agent hand-off. The target architecture then adds human-approved publishing into the customer's existing CMS, with Shopify and Sitecore as illustrative connectors, followed by a governed maintenance loop for content such as FAQs. [S4]

The HTML remains the shareable offline proposal. Its workflow, visual language, fixture cases and output contract are reusable design references; its scripted agent, timers and fictional results are not a backend. No live integrations, account access, model availability or service budgets have been verified in this planning task. [S1]

## Planning Framework

| Section | Description | Notes |
| --- | --- | --- |
| Startle | A search result position does not establish whether an AI answer cites the page. | Demonstrate retrieval presence and answer citation as separate observations; do not invent market statistics. [S1] |
| WIIFM | Give marketers an evidence-backed edit backlog that downstream agents can consume. | Success is a reviewable action and trustworthy evidence, not an unsupported visibility guarantee. |
| Needs and Challenges | Marketers need to understand buyer intent, missing citations and competing sources without manual query-by-query investigation. | Citation variability, limited service access and proprietary assistant behaviour constrain conclusions. |
| Define Questions | Can we retrieve the page, test approved queries fairly, verify citations and emit useful tasks? | Confirm access, runtime, allowed costs, data handling and evaluation mode before live work. |
| Cornerstone | One agent-led conversation produces inspectable evidence, reviewable content and a governed path to publication. | Keep separate human approvals for query evaluation, content acceptance and CMS publishing; distinguish agent roles. |
| Supporting Evidence 1 | The existing HTML defines the brief, query approval and dashboard flow. | Reuse as the UX specification; test live behaviour independently. [S1] |
| Supporting Evidence 2 | Web IQ is a real grounding service and Foundry supports agents, tools and traces. | Availability does not prove access or exact endpoint compatibility; phase 0 must verify both. [S2, S3] |
| Supporting Evidence 3 | The reference deck extends the flow from monitoring and content creation through human approval into Shopify or Sitecore, with Work IQ supplying brand guardrails and approved messaging. | Treat CMS targets as illustrative until connector contracts, permissions and rollback are verified. [S4] |
| Repeat Cornerstone | A useful GEO result connects an observed answer to evidence, a proposed edit and an acceptance check. | Do not confuse a model-written explanation with proof of why a source ranked. |
| Conclusion and next steps | Approve scope, confirm service access and implement the smallest live pipeline. | Proposed owners are engineering, platform and content roles; people and dates remain unassigned. |

## Architecture Decision

Recommended stack: Python with Microsoft Agent Framework for the GEO agent and role-specific LLM calls; Pydantic for typed contracts; an async HTTP client for Web IQ adapters; and a thin FastAPI interface for the dashboard, job status and downloads. Confirm compatible SDK and runtime versions during phase 0, then pin them. [S3]

Develop locally first. Use a Foundry hosted agent for deployment if access and supported runtime capabilities meet the requirements. Its code-based model suits custom adapters, provenance handling and controlled orchestration. A Foundry prompt agent with externally hosted tools is the fallback if hosted-agent access is unavailable; it must use the same backend contracts and approval controls. Do not provision both approaches by default. [S3]

The front end is a conversational GEO agent, presented through a thin chat and evidence view. Foundry Agent Inspector is the first engineering test client; a connected dashboard follows. The agent runtime, conversation, tool calls and visibility evaluator profiles are separate responsibilities. The live client needs a server and network access; the existing self-contained offline HTML stays unchanged.

| Component | Responsibility | Proposed implementation |
| --- | --- | --- |
| GEO agent | Clarify URL, audience and goal; propose queries; explain evidence; request drafts, approval and publication. | One conversational agent with narrow typed tools; no unrestricted browsing or shell access. It may request a publish transition but cannot approve content or bypass server-side guards. |
| Run coordinator | Enforce approval, execute stages, retain status and resume safely. | Application-owned state machine and bounded async jobs; agent requests transitions but cannot bypass guards. |
| Web IQ Browse adapter | Extract submitted and permitted cited competitor pages with provenance. | API contract verified against granted access; store extraction outcome, content hash and source metadata. |
| Query generator | Generate deduplicated buyer queries with intent and rationale. | Structured LLM output; marketer reviews and edits before approving. |
| Search and evaluator harness | Run Web IQ Search and up to three isolated Foundry profiles per approved query. | Bounded parallel calls with versioned prompts/models, explicit retrieval mode and no shared conversation history. |
| Presence and comparison services | Verify citations and identify observable differences across the eight-factor rubric. | Deterministic URL/citation matching; constrained LLM interpretation with evidence IDs and uncertainty. |
| Recommendation and export services | Validate proposed actions and render a manifest plus one .md task per recommendation. | Shared typed data model, schema validation and deterministic Markdown rendering. |
| Work IQ grounding adapter | Retrieve authorised brand guidance, approved messaging, workflow context and recent business changes relevant to the draft. | Read-only, least-privilege retrieval with source IDs, freshness, permissions and retention recorded. Work IQ context can propose an FAQ update; it cannot approve or publish it. [S4] |
| Run and artefact store | Persist approvals, job events, evidence, results and files. | SQLite plus a restricted local artefact directory for the local milestone. Choose durable Azure storage before hosted deployment; do not rely on container-local disk. |
| Downstream proof | Consume a task, produce a draft, then review evidence and checks. | One content-editor role and one reviewer role; manual dispatch and an isolated writable output directory. |
| CMS approval and publishing service | Create a CMS draft, bind approval to its exact revision, publish once approved and retain the receipt. | Connector interface with Shopify and Sitecore as illustrative adapters. Separate credentials, scoped permissions, idempotency, preview, audit trail and rollback are mandatory. [S4] |

Data flow: monitor signals + marketer brief -> GEO agent <-> coordinator -> Browse -> query generation -> query approval -> Search and evaluator harness -> presence matching -> competitor comparison -> recommendations -> Markdown bundle -> editor/reviewer proof grounded by authorised Work IQ context -> CMS draft -> human content and publish approval -> Shopify/Sitecore connector -> live page -> monitored retest.

## Scope and Assumptions

- Phase A delivers the GEO agent through Markdown output. Phase B demonstrates a manually triggered downstream content draft and review. Phase C adds a governed CMS connector path, and Phase D adds monitored maintenance proposals for approved content types such as FAQs. Full technical-editor execution is deferred; tasks may still describe technical recommendations.
- Service access is unknown. Read project/model selection from Foundry Toolkit at implementation start; confirm the intended existing project or new project before provisioning. The public Web IQ site currently advertises limited access. [S2]
- One accessible public page and a single explicit locale per run. Propose 5-10 queries; approve the exact set. Three profiles are a target, not an assumption that particular models or vendors are available.
- Copilot-like, ChatGPT-like and Claude-like are configurable test profiles, never claims of consumer-product replication. Prefer neutral profile labels with documented characteristics until the approximation is calibrated. Record actual model deployments separately.
- Aim for three to five useful recommendations, but output fewer or none when evidence is insufficient. Never invent a fourth task to reproduce the fixture. Unverified technical checks must remain visibly distinct from observed defects.
- Exclude login-protected crawling, whole-site audits, unapproved or model-authorised publishing, autonomous shell execution, fine-tuning, consumer-assistant scraping and unsupported ROI claims. Scheduled maintenance may be added only after approval, connector and monitoring controls are proven.
- Shopify and Sitecore are illustrative CMS targets from the reference deck, not verified integrations. Confirm API scope, draft/review semantics, rate limits, identity model, audit fields and rollback behaviour before implementation. [S4]
- "Self-maintaining" means continuously monitored and agent-maintained, with a human approving each publication. It never means autonomous unreviewed changes. Work IQ is an authorised grounding source for brand, policy and business context, not a blanket instruction channel.
- Preserve the offline HTML as a fixture/demo artefact. Explicit live, recorded and synthetic provenance travels with every run and evidence item. A failed live call must not silently substitute synthetic results.

## Build Sequence and Gates

### Phase 0: Access and feasibility

Owner: platform engineer and technical lead. Dependency: approved implementation scope.

Verify Web IQ Browse/Search contracts, authentication, citation/source fields, result ordering semantics, extraction limits, allowed retention and quota. Confirm Foundry project, region, model deployments, tool support, identity and hosted-agent availability. Run one permitted Browse call, one Search call and one tool-using model invocation only after implementation approval. Do not infer a Search endpoint or schema from marketing copy. [S2, S3]

Select local Python environment and supported SDK versions. At implementation time, follow Foundry Toolkit project/model selection, dependency checks and its current agent setup workflow. Record project and model choices without copying secrets into source or chat. Establish a run-cost ceiling and permitted test pages with the owner.

Exit gate: a capability matrix with verified sample responses and explicit gaps. If access is missing, build adapters against labelled contract fixtures; the live milestone remains blocked, not complete. Foundry preparation commands and dependency installation are intentionally deferred from this planning task.

### Phase 1: Contracts and deterministic coordinator

Owner: backend engineer. Dependency: phase 0 contracts; fixture-backed development may start in parallel with access work.

Define Brief, PageSnapshot, QuerySet, Approval, EvaluationResult, CitationMatch, ComparisonFinding, RecommendationTask and RunManifest schemas. Keep profile configuration, prompts, model versions and dataset versions in configuration. Build the coordinator and a repository interface before wiring a conversational agent.

States: draft -> fetching -> awaiting-query-approval -> evaluating -> comparing -> recommendations-ready -> exported -> drafting -> awaiting-content-approval -> cms-draft-ready -> awaiting-publish-approval -> publishing -> published -> monitoring. Failed, partial, rejected, superseded, rollback-pending and cancelled states are explicit. Persist query approval against a hash of page snapshot, approved queries, locale, profiles and retrieval settings. Persist content and publish approvals separately against the exact draft hash, CMS target, destination and connector configuration. Any changed input invalidates the relevant approval and starts a new revision. Server guards check ownership, expected revision and idempotency keys; model text cannot count as approval.

Tools: inspect_page(brief_id), propose_queries(run_id), start_evaluation(approved_run_id), get_run_status(run_id), get_evidence(run_id, evidence_id), generate_recommendations(run_id), export_tasks(run_id), create_cms_draft(approved_content_revision), publish_cms_revision(approved_publish_revision) and get_publication_receipt(publication_id). Approvals are separate authenticated UI/API actions, not agent-authorised tools. Results refer to server-owned IDs, not caller-supplied filesystem paths.

Exit gate: typed contract tests and coordinator tests prove approval gating, invalidation, duplicate-submit handling, cancellation, safe resume and complete separation of fixture/live runs. A backend contract test validates score inputs rather than hardcoding expected outputs.

### Phase 2: Safe retrieval and approved query generation

Owner: integration engineer. Dependency: phases 0-1.

Implement Web IQ adapters and URL policy: public HTTP(S) only, no embedded credentials, no local/private/link-local/metadata targets, limited redirects, size/content-type limits, deadlines and restricted outbound access. Revalidate resolved addresses and each redirect in any fetcher we control. Verify the provider's equivalent protections for provider-side fetching; local validation alone cannot prevent a remote provider following an unsafe redirect. Fail closed if the required controls cannot be established.

Extract title, headings, content, canonical/redirect evidence and available date/author metadata. Track missing fields and failed extraction. Use passage-level grounding where supported, retaining source IDs and request settings. Respect access restrictions, copyright and service retention terms; collect only necessary content.

Generate schema-valid queries and short rationales from the page and supplied brief. Treat inferred audience/goals as suggestions. Let the marketer edit, remove and approve the query set. The demo's fixed four queries remain regression fixtures, not runtime defaults.

Exit gate: approved test pages produce inspectable snapshots and query sets. Tests cover private targets, redirect chains, DNS changes, Unicode hosts, malformed URLs, unsupported content, blocked pages, extraction failures and prompt injection in page content.

### Phase 3: Fair visibility evaluation and scoring

Owner: evaluation engineer. Dependency: approved query set and retrieval adapters.

Start with one verified evaluator configuration, then add up to three. Each profile receives the approved query and declared locale, with isolated context and a pinned retrieval policy. Never provide the target URL, brand goal, target-page snapshot or optimisation instructions as additional evaluator context; doing so would bias inclusion. A brand may appear in the approved query itself, but label such prompts as branded and report them separately.

Run a standalone Web IQ Search measurement for each query. For the MVP, supply the same permitted Web IQ evidence packet to each evaluator as a controlled comparison, recording that this standardises retrieval rather than approximating proprietary assistant search. A later agentic-search mode may let each profile search independently; label and score the modes separately. Returned position is position within the recorded response, not a universal search rank.

Require citation IDs or URLs to resolve to actual sources available in the tool trace. A model-written URL is not verified evidence by itself. Use canonical and observed redirect relationships to match the submitted page; do not erase meaningful path/query distinctions or equate all pages on a domain. Track exact-page citations, domain-only citations, brand mentions and reviewed claim matches separately. Unsupported citations and ambiguous matches are flagged.

Score = round(100 * completed answers with a verified exact-page citation / completed evaluable answers). Report numerator, denominator, intended call count and coverage overall and by query/profile. Completed answers without citations count as zero; timeouts, unusable payloads and tool failures are missing/error results, not negative answers. With no evaluable answers show N/A, never zero. Retain failure reasons and avoid comparing different query sets or retrieval modes as if equivalent.

Exit gate: human-labelled citation cases validate matching and failure handling; tests include fabricated citations, tracking/canonical variations, same-domain different pages and no-answer runs. Frozen synthetic fixtures reproduce the prototype's 42/100 (5/12) and 50/50/25 profile scores. These are regression values, not targets for live pages.

### Phase 4: Comparison, recommendations and Markdown output

Owner: backend and content engineers. Dependency: phase 3 evidence.

For missing-page answers, inspect the sources actually cited. Deduplicate and fetch at most five unique competitor pages per initial run, configurable after cost review. Compare relevance, authority signals, structure, clarity, factual support, freshness, citations and machine readability. Cite exact source passages and distinguish absent data from a measured deficiency. Explanations of source selection remain hypotheses, not claims about hidden ranking logic.

Generate structured recommendations with priority, effort, target section, evidence IDs, proposed changes, rationale, uncertainty, fact-verification needs and acceptance checks. The server validates them before rendering Markdown. Do not let an LLM produce arbitrary filenames, trusted frontmatter or unrestricted downstream instructions. Technical recommendations require suitable evidence; if Browse does not expose raw HTML, flag schema/HTML checks as unverified instead of claiming inspection.

Export one manifest and one .md per accepted recommendation. Each live artefact records schema_version, run_id, revision, task_id, created_at, source page URL and snapshot hash, provenance, retrieval mode, query/profile IDs, evidence IDs, priority, suggested role, depends_on, status=draft and requires_human_approval=true. Publish permission remains false in the task contract; a later CMS service derives authority only from a separate server-side approval record bound to an immutable content revision. Keep compatibility with the demo's v1 fields or explicitly version a migration; do not silently replace fixture fields with live meanings.

The manifest links task files, query/profile coverage, score methodology, source/evidence records, limitations and the review sequence. Evidence references must be usable by an authorised downstream consumer: package permitted bounded excerpts and provenance, or provide an authenticated resolver with explicit expiry/retention rules. Keep a machine-readable internal JSON record, even though the user-facing hand-off is Markdown. Use a YAML library, escaped Markdown and schema-checked filenames; downloads represent a frozen complete run, not the selected dashboard filter.

Exit gate: all frontmatter parses and satisfies the schema; every task/file/evidence link resolves; malicious text cannot alter permissions or create paths; repeated rendering of a snapshot is deterministic. Empty recommendations still produce a manifest explaining insufficient evidence. All files include provenance and review constraints, with no fabricated evidence.

### Phase 5: Live conversation and evidence interface

Owner: agent and frontend engineers. Dependency: phase 1 tools; final acceptance requires phases 2-4. Can develop against fixtures in parallel.

Connect the GEO agent to the narrow coordinator tools. Its instructions define clarification, query review, evidence explanation, uncertainty and hand-off behaviour. Keep tool traces available to engineering and show concise stage progress to marketers; do not display purported hidden reasoning. Configure bounded tool turns and explain partial runs instead of retrying indefinitely.

Use Agent Inspector for the first local conversations; include workspace-root VS Code debug configuration when implementing. Adapt the existing conversation/evidence visual design into a served client that consumes backend run state. Stream progress via SSE or poll a run-status endpoint, with reconnect and refresh recovery. Keep the original offline file operational as the labelled presentation version.

Suggested API contracts: POST /briefs, POST /runs/{id}/query-approval, POST /runs/{id}/start, GET /runs/{id}, GET /runs/{id}/events, GET /runs/{id}/evidence/{evidence_id}, POST /runs/{id}/exports, GET /runs/{id}/artifacts/{artifact_id}, POST /runs/{id}/content-approval, POST /runs/{id}/cms-drafts, POST /runs/{id}/publish-approval, POST /runs/{id}/publications and GET /runs/{id}/publications/{publication_id}. Exact routes are design proposals, not existing endpoints. Enforce authentication, run ownership, destination allowlists and revision checks at every boundary; do not place provider or CMS credentials in browser code.

Exit gate: a user can submit a real page, clarify the brief, approve queries, see traceable live results and download valid tasks. Keyboard, mobile, dark/light, reconnect, error and partial-run states work. The agent cannot approve its own run or generate an artefact for another user's run.

### Phase 6: One downstream proof and release gate

Owner: agent engineer, content reviewer and platform engineer. Dependency: phases 4-5; a follow-on milestone after the GEO-to-Markdown core.

Manually select one content task and pass it through a schema/provenance validator to an isolated editor role. Give the editor only the required verified evidence and permission to write draft outputs. Return a proposed edit, evidence mapping and unresolved questions. A reviewer role checks acceptance criteria and records pass/fail/needs-human-review; it cannot grant publication permission. Do not execute a full autonomous multi-agent graph in this milestone.

Reject path traversal, unknown schema versions, stale page hashes, cross-run references and untrusted instruction overrides. A human must inspect the draft. Technical-agent execution, CMS connectors and scheduled processing remain follow-on work.

Before any hosted pilot, choose a durable database/job strategy and artefact storage, Entra identities, minimal RBAC, retention policy and logging controls. Use Foundry tracing/Application Insights with correlation IDs and redaction. Deploy only after local acceptance and owner approval, following current Foundry guidance; smoke-test the hosted endpoint and retain a rollback path. [S3]

Exit gate: one authorised consumer resolves real evidence, produces a reviewable draft and validation result, and cannot publish. The release checklist records quality findings, coverage, costs, access constraints and remaining risks. Cloud deployment is a separate approval, not implied by this plan.

### Phase 7: Human-approved CMS publishing

Owner: CMS integration engineer, content owner and security reviewer. Dependency: phase 6 plus a customer-approved CMS sandbox.

Define a connector-neutral CMSDraft and PublicationRequest contract. Start with one sandbox connector; Shopify and Sitecore are illustrative targets from the deck, not simultaneous MVP commitments. Map approved content only into allowlisted sites, locales, content types and fields. Read the current CMS revision before creating a draft, preserve customer workflow states and never overwrite a newer human edit.

The content owner reviews a rendered preview and evidence map. A separate publish approver confirms the immutable CMS draft revision, destination and release note. The publish service verifies both approvals server-side, uses a dedicated least-privilege identity, supplies an idempotency key and records the CMS response, resulting URL/version and correlation ID. Query approval, task generation or chat text cannot authorise publication.

Support dry-run, draft-only and publish modes. Production publish is disabled by default. Add explicit rejection, expiry, conflict and rollback paths; if the CMS cannot offer a safe rollback primitive, retain the previous content and provide a compensating-change workflow. Never silently retry an ambiguous publish result.

Exit gate: an approved draft is published to one sandbox destination exactly once, an unapproved or stale revision is rejected, a concurrent CMS edit is preserved, and the audit log identifies who approved what, where and when. [S4]

### Phase 8: Governed content maintenance

Owner: content operations, Work IQ integration engineer and evaluation engineer. Dependency: phases 3, 6 and 7.

Create maintenance policies for explicitly enrolled content blocks, starting with FAQs. Signals may include new visibility gaps, stale source dates, changed authorised Work IQ guidance, approved messaging updates or changed product facts. Each signal produces a maintenance proposal with source provenance, a diff against the live CMS revision, affected claims, confidence, expiry and retest plan.

Work IQ retrieval is read-only and permission-trimmed. Treat messages and documents as evidence that may be stale, conflicting or malicious; prefer designated authoritative sources, record freshness and require the reviewer to resolve conflicts. Do not infer approval from a document's presence or a user's role.

Every maintenance revision follows the same draft -> review -> approval -> publish path as net-new content. After publication, snapshot the new page and rerun matched held-out queries. Report observed change without attributing causation. Rate-limit proposals, deduplicate repeated signals and pause the loop after repeated failures, unresolved source conflicts or content-owner rejection.

Exit gate: a controlled source change produces one deduplicated FAQ proposal, no live change occurs before approval, publication retains a full audit trail, and the post-publish evaluation is linked to the exact CMS revision.

## CMS Publishing and Continuous Maintenance

The target operating model is **Monitor -> Act -> Approve -> Publish -> Retest**. The agent can prepare and submit a publication request, but only authenticated human decisions can release an immutable revision to a permitted CMS destination. Shopify PDPs/collection pages and Sitecore landing/campaign pages are examples from the reference deck; customer-specific content models and approval workflows remain authoritative. [S4]

Continuously maintained FAQs are a governed use case, not an autonomous editing promise. Web IQ supplies external evidence, Work IQ supplies authorised organisational context and the CMS supplies the current live revision. The system proposes a bounded diff, a human approves it, the connector publishes it and the evaluator measures the resulting page. Each loop preserves provenance, approval, publication receipt and rollback information.

## Work Breakdown and Delivery Estimate

These are engineering-effort estimates, not calendar commitments. They assume working service access and SDK compatibility; access approvals and model quota delays are excluded. Owners are proposed roles, not named assignments.

| Phase | Workstream | Effort estimate | Dependency |
| --- | --- | --- | --- |
| 0 | Access, contracts and feasibility | 0.5-1 engineer-day | Scope and account owner |
| 1 | Schemas, state machine and run store | 1-2 engineer-days | Contract shape; can begin with fixtures |
| 2 | Browse/Search adapters and query generation | 1-2 engineer-days | 0-1 |
| 3 | Evaluator harness, matching and scoring | 1.5-2.5 engineer-days | 2 and approved queries |
| 4 | Comparison, recommendations and Markdown | 1-2 engineer-days | 3 |
| 5 | Live agent and connected evidence view | 1-2 engineer-days | 1, with 2-4 required for live acceptance |
| 6 | Downstream proof and pilot release checks | 1-2 engineer-days | 4-5 |
| 7 | CMS draft, approval and one sandbox publisher | 2-4 engineer-days | 6 and customer CMS access |
| 8 | Work IQ-grounded FAQ maintenance loop | 1.5-3 engineer-days | 3, 6-7 |

Core through Markdown: approximately 6-11.5 engineer-days. Including the downstream proof: approximately 7-13.5 engineer-days. Including one sandbox CMS connector: approximately 9-17.5 engineer-days. Including the controlled FAQ maintenance loop: approximately 10.5-20.5 engineer-days, excluding access approvals and full production hardening. With a two-day hackathon, use one evaluator, five queries, a known accessible page, three bounded recommendations and a simulated approval-to-CMS experience; prove the contract before connecting a customer CMS.

Parallel work: phase 1 schemas/coordinator, phase 2 adapter preparation and phase 5 client scaffolding can overlap once contracts are agreed. Critical live path: access -> safe retrieval -> approved queries -> verified citations -> recommendations -> validated files. Downstream consumer development can use contract fixtures while that path is completed.

## Evaluation, Safety and Cost Plan

- Build a small proposed evaluation set: 6 authorised public pages across 3 content types, 5 queries per page and a human-labelled citation subset. Separate development cases from held-out queries. Final dataset and runtime configuration must be agreed before evaluation code generation; use the Foundry evaluation planning guidance then.
- Hard gates: no evaluation before valid query approval; no cross-run/user access; no unsupported exact-page citation accepted as verified; all exported files schema-valid; no CMS action before separately authenticated content and publish approvals; no stale draft publication; no secrets in browser or exported tasks. Passing tests is evidence for the tested cases, not a guarantee against all attacks.
- Quality assessment: human-review citation/mention precision and recall, recommendation specificity and factual support, task usefulness and consumer comprehension. Record disagreement and uncertainty; numeric quality thresholds follow baseline review instead of being invented now.
- Repeat selected queries three times to measure variability, using fixed configurations. Report raw counts and per-profile coverage. For pre/post comparisons use matched complete query/profile pairs, unchanged settings, held-out queries and recorded page versions. In a controlled-content comparison, distinguish evidence-packet experiments from changes in live retrieval. Do not attribute ranking changes to edits without further evidence.
- Budget the first standard run as 5-10 standalone Search calls and 15-30 evaluator answers with three profiles, plus one initial Browse, at most five competitor Browses and bounded query/comparison/recommendation calls. Controlled evidence-packet mode avoids extra evaluator search calls; independent-search mode needs a separate budget. Retries and repeated runs add cost.
- Proposed initial limits, to confirm in phase 0: at most 3 concurrent provider calls, 2 bounded retries per transient failure, per-call deadlines, cancellation and an explicit run budget approved before execution. Record actual input/output tokens and provider costs when supplied; distinguish estimates from billed cost. Never retry non-transient policy/access failures indefinitely.
- Store provider request IDs, run IDs, page hashes, prompts/versions, source provenance, partial failures, latency and measured usage. Redact sensitive text and signed links from general logs. Approve retention/deletion rules for raw content and transcripts before sharing a hosted pilot.

## Suggested Repository Structure

Keep geo-optimiser.html as the unchanged proposal. The following names are proposed future files/directories, not files created by this plan:

- geo-agent/src/geo_agent/contracts and workflow: typed schemas, approval/state transitions and orchestration.
- geo-agent/src/geo_agent/tools: Web IQ adapters, safe URL policy and evidence retrieval.
- geo-agent/src/geo_agent/evaluation: profile configuration, isolated runs, citation matching and score calculation.
- geo-agent/src/geo_agent/recommendations and artifacts: structured recommendations, YAML/Markdown rendering and consumer validation.
- geo-agent/src/geo_agent/grounding: permission-trimmed Work IQ context, authority/freshness policy and evidence normalization.
- geo-agent/src/geo_agent/publishing: connector-neutral CMS contracts, approval verification, Shopify/Sitecore adapters, publication receipts and rollback handling.
- geo-agent/src/geo_agent/agents and api: GEO agent, downstream proof roles and authenticated endpoints.
- geo-agent/tests: unit, adapter-contract, safety, fixture, integration and Markdown-consumer tests.
- geo-agent/web: connected chat and evidence client, reusing the proposal's visual language.
- geo-agent/.foundry: evaluation configuration/results as required by the selected Foundry workflow; no secrets or duplicated deployment state.
- Workspace-root .vscode: local agent debug configuration; deployment configuration and infrastructure added only after hosting choice.

## Decisions and Immediate Next Steps

The user was unavailable for scope/access clarification. Proceeding assumptions are documented, not approvals to use accounts or spend money.

1. Product owner: confirm the core GEO-to-Markdown scope and whether the downstream proof is part of the first demonstration. Default recommendation: core first, proof immediately after.
2. Account/platform owner: identify the Web IQ grant, Foundry project, permissible models/region, per-run budget and content-retention rules. No credentials in chat.
3. Technical lead: approve the Python/code-based approach and controlled retrieval mode; choose model deployments on verified capability, cost and availability, not branding.
4. Content owner: nominate one accessible page and desired buyer intent, then approve a small human-review dataset and one maintenance-eligible FAQ block.
5. CMS owner: choose either a Shopify or Sitecore sandbox, document its existing approval workflow, and define allowlisted destinations, rollback expectations and the authorised publisher identity.
6. Work IQ owner: identify the authoritative brand, policy and approved-messaging sources that the agent may read; define freshness and conflict rules.
7. Engineering: start phase 0 when implementation is authorised, preserve the offline proposal, and demonstrate one real citation trace before expanding profiles, downstream agents or CMS authority.

## Sources and Limits

[S1] Local requirements and prototype: [geo-optimiser.html](geo-optimiser.html), particularly workflow, architecture, technical approach, scope, risks and the embedded Markdown generator. Reviewed 10 September 2026. The file proves prototype behaviour and intended contracts, not live service capability.

[S2] Microsoft Web IQ public service information: https://webiq.microsoft.ai/ . Reviewed 9 September 2026. Confirms a real web-grounding API service and limited access; precise Browse/Search endpoint contracts and permissions require granted documentation and verification.

[S3] Microsoft Foundry Agent Service overview: https://learn.microsoft.com/en-us/azure/foundry/agents/overview . Reviewed 9 September 2026. Supports the agent/runtime/tool/tracing options described here; project-specific models, regions, runtime features and quotas remain unverified.

[S4] GEO Content Agent reference deck: https://microsofteur-my.sharepoint.com/:p:/g/personal/thomasjones_microsoft_com/IQBy3ediM0mmR5qK9s3sVAGbAQ0IEEhXMFk_xyUKYw4qYg0?e=R4NznB . Reviewed 10 September 2026. Supports the Monitor/Act/Publish vision, human approval boundary, Shopify and Sitecore examples, and Work IQ grounding role. The deck explicitly describes a concept and says no end-to-end reference implementation exists.

No application code or Azure resources are created by this plan. The HTML remains a labelled offline simulation, including its CMS approval and publish interactions.