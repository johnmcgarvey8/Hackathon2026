# GEO Measurement Run Methodology

**BLUF:** A GEO measurement run is a controlled, evidence-bound experiment. The application converts a user-confirmed measurement goal into five page-grounded buyer intents, retrieves one bounded WebIQ evidence packet for each intent, asks each configured LLM profile the same buyer question using the same packet, measures observable retrieval and citation outcomes, and then generates evidence-linked recommendations for human review.

This document describes the methodology implemented in the application as of **2 October 2026**. It is intended as a foundation for a more detailed product, technical, or methodology specification.

## 1. What the methodology is designed to measure

The run answers four related questions:

1. **Grounding visibility:** Does WebIQ return the exact target page in the saved top-five result packet for each approved grounding query?
2. **Answer visibility:** When a configured LLM profile receives that packet, does its answer cite the exact target page?
3. **Brand visibility:** Is the configured brand literally present in retrieved evidence and generated answers?
4. **Content opportunity:** What repeatable content patterns appear in retrieved or cited sources, and what evidence-bound changes could be tested on the target page?

The principal citation metric is:

```text
Exact-page citation score =
  completed answers citing the exact target page
  ------------------------------------------------ x 100
                  completed answers
```

Failed or missing answers lower coverage but are not treated as zero-quality answers. Search presence, raw URL mentions, same-domain citations, and exact-page citations are retained as separate observations. Priority does not weight the score. See [`measurement_scores`](../geo-agent/src/geo_agent/evaluation.py) and the measurement contracts in [`contracts.py`](../geo-agent/src/geo_agent/contracts.py).

## 2. End-to-end process

```mermaid
flowchart TD
    A[User selects project and exact page] --> B[User enters raw measurement objective]
    B --> C[Goal Summary call]
    C --> D[User reviews or edits concise goal]
    D --> E[Measurement run created]

    E --> F[WebIQ Browse exact page]
    F --> G[Bounded page snapshot<br/>up to 10,000 characters]
    G --> H[Foundry preparation analysis]
    H --> I[Purpose, audience, entities,<br/>questions, observations, brand inference]
    G --> J[Foundry paired query planner]
    D --> J
    I --> J

    J --> K[Five priority-ranked query pairs]
    K --> K1[Chat query for LLM survey]
    K --> K2[Grounding query for WebIQ Search]
    K --> K3[Intent, rationale, branded flag,<br/>exact page-evidence quotes]

    K --> L[Schema, uniqueness, ordering,<br/>and quote validation]
    L --> M[Immutable approval hash]
    M --> N[Approval or automatic project binding]

    N --> O{For each of 5 queries}
    O --> P[WebIQ Search<br/>maximum 5 results]
    P --> Q[Saved grounding packet<br/>IDs, URLs, excerpts, positions, provenance]
    Q --> R{For each configured LLM profile}
    R --> S[Ask paired chat query using<br/>the exact same grounding packet]
    S --> T[Saved answer and reported citation IDs]

    T --> U[Deterministic scoring and evidence assessment]
    U --> U1[Exact-page citation]
    U --> U2[Same-domain and other-page citations]
    U --> U3[WebIQ target presence and position]
    U --> U4[Brand presence and source conversion]
    U --> U5[Coverage, failures, unsupported IDs]

    U --> V[Recommendations specialist stage]
    V --> W[Select bounded comparison sources]
    W --> X[Generate up to 3 draft hypotheses]
    X --> Y[Validate exact quotes and hashes]
    Y --> Z[Human accepts or rejects each recommendation]

    U --> AA[Deterministic content strategy]
    AA --> AB[Grounding patterns, answer patterns,<br/>test proposal and verification method]

    Z --> AC[Export measurement bundle]
    AB --> AC
```

## 3. Stage-by-stage methodology

### Stage 1: Measurement goal creation and confirmation

The user supplies:

- An exact public HTTP(S) page within the selected project's allowed domains.
- A raw measurement objective, such as what the user wants to learn about the page's visibility or treatment.
- The project's locale and brand scope.

Before the run begins, the application can make a bounded Foundry call to rewrite the raw objective as one concise sentence. The summariser is explicitly instructed to preserve the user's intent and avoid adding facts, recommendations, labels, markdown, or quotations. The user can edit this proposed summary before confirming it. If the provider is unavailable or returns an invalid response, the original objective is used as a visible fallback.

This step improves consistency of the downstream brief without silently changing the requested objective. The original goal, summary operation, and confirmed goal remain distinguishable. See [`goal_summary.py`](../geo-agent/src/geo_agent/goal_summary.py) and the measurement creation page in [`new/page.tsx`](../web/app/projects/%5BprojectId%5D/measurements/new/page.tsx).

### Stage 2: Run creation and scope validation

The confirmed inputs become a `Brief` containing:

- `url`
- `audience`
- `goal`
- `locale`

For a project-bound run, the page must belong to the project's configured domains and the locale must match the project. The live policy also validates the brief before any provider work is queued. Project measurements do not fall back to synthetic fixtures.

The run starts as a durable, owner-scoped record. State transitions, revisions, jobs, provider operation claims, and later artifacts are persisted so the workflow can recover after reloads or process restarts. See [`project_measurements.py`](../geo-agent/src/geo_agent/project_measurements.py), [`measurement_workflow.py`](../geo-agent/src/geo_agent/measurement_workflow.py), and [`persistence.py`](../geo-agent/src/geo_agent/persistence.py).

### Stage 3: Exact-page retrieval with WebIQ Browse

The preparation worker calls the WebIQ Browse endpoint for the exact submitted page. The request asks for:

- Markdown content.
- A maximum of 10,000 characters.
- The approved language and region.
- No live crawl.
- No web or image links.
- No dynamic-page rendering.

The returned page must still match the requested URL after allowing only limited, known tracking-parameter differences. Redirect equivalence is not assumed. Private, local, credential-bearing, fragmented, or otherwise disallowed URLs are rejected by policy.

The application saves the title, content, URL, provider trace ID, crawl metadata, timestamp, content format, and live provenance as a `PageSnapshot`. This snapshot becomes the immutable page evidence used by preparation and query planning. See [`webiq.py`](../geo-agent/src/geo_agent/webiq.py).

### Stage 4: Preparation analysis and page understanding

The captured page is divided into deterministic 1,000-character passages named `page-1` through `page-10`. A Foundry model receives those bounded passages and produces a structured preparation analysis covering:

- Page purpose.
- Likely audience.
- Key entities.
- Questions the page appears to answer.
- Evidence-backed observations.
- Improvement hypotheses and suggested verification.
- An optional inferred primary brand definition.

Every finding must reference at least one supplied passage ID and an exact verbatim quote from that passage. The application verifies those references mechanically before saving the analysis.

The model must distinguish:

- **Observed:** directly stated in the captured passage.
- **Inferred:** an interpretation based on the supplied page evidence.

Brand inference is similarly constrained. The model may use model knowledge to suggest a canonical brand name or literal aliases, but a name or alias must be anchored in the page evidence. Domain ownership cannot be expanded using model knowledge: only the exact browsed hostname can be retained, and only when the page represents that brand.

This is page understanding, not a full technical or visual audit. The stage does not inspect runtime JavaScript, full HTML, schema markup, canonical tags, rankings, or citation performance. See [`PREPARATION_ANALYSIS_PROMPT`](../geo-agent/src/geo_agent/foundry.py) and [`PreparationHandler`](../geo-agent/src/geo_agent/preparation.py).

### Stage 5: Intent derivation and paired query generation

There is **no standalone intent-classification service** in the current implementation. Intent is derived as part of the paired query-planning call.

The planner receives:

- The confirmed URL, audience, goal, and locale.
- The captured page title.
- The bounded `page-1` through `page-10` passages.

It must generate exactly five distinct buyer discovery query pairs, ordered from priority 1 to 5. Each pair contains:

| Field | Purpose |
| --- | --- |
| `query_id` | Stable ID from `q-1` to `q-5`. |
| `chat_query` | Natural-language buyer question asked during the LLM survey. |
| `grounding_query` | Concise search query sent to WebIQ. |
| `intent` | Short description of the buyer need represented by the pair. |
| `rationale` | Why this intent is relevant to the supplied goal and page. |
| `branded` | Whether either query explicitly names the brand. |
| `priority` | Qualitative relevance hypothesis from 1 to 5. |
| `evidence` | One or two exact page quotes supporting why the query belongs in the plan. |

The chat and grounding queries must express the same intent but are optimized for different interfaces:

- The **chat query** is phrased as a realistic buyer question for an LLM.
- The **grounding query** is a concise retrieval expression for search.

The planner prefers unbranded discovery questions so the run can observe whether the target or brand emerges without being forced into the prompt. Branded queries are allowed but explicitly labelled.

Priority is a relevance hypothesis only. The application does not claim that priority reflects search volume, demand, ranking opportunity, or commercial value.

#### Query-plan validation

The application rejects or repairs invalid output before it becomes measurable input:

- Exactly five query IDs and five unique priorities are required.
- Chat queries must be unique.
- Grounding queries must be unique.
- Page evidence IDs must resolve to the captured passages.
- Quotes must occur verbatim in the referenced passage.
- Minor quote formatting artifacts may be normalized only when the underlying words can still be anchored exactly.
- Unsupported evidence references are discarded, and the final plan is validated again.

The methodology is implemented by [`PAIRED_QUERY_PROMPT` and `Foundry.propose_pairs`](../geo-agent/src/geo_agent/foundry.py), with structural rules in [`QueryPair` and `QueryPlan`](../geo-agent/src/geo_agent/contracts.py).

### Stage 6: Approval binding

The prepared inputs include the brief, snapshot, five query pairs, evaluator roster, policy hash, method version, retrieval mode, and query-generation metadata. They are serialized and hashed to create an immutable `approval_hash`.

That hash prevents the application from approving one query plan and executing another. Any revision to the inputs clears prior approval and downstream results.

There are two operating patterns:

- **Automatic project flow:** after successful preparation, the project orchestrator binds approval to the exact prepared input hash and queues evaluation automatically.
- **Manual or agent flow:** a human reviews the query plan and grants the required approval or execution authorization before evaluation can start.

In both cases, approval is tied to the exact inputs, owner, revision, and policy rather than being a generic permission to make provider calls. See [`MeasurementApproval`](../geo-agent/src/geo_agent/measurement_workflow.py), [`MeasurementInputs.approval_hash`](../geo-agent/src/geo_agent/contracts.py), and [`ProjectMeasurementOrchestrator.reconcile_job`](../geo-agent/src/geo_agent/project_measurements.py).

### Stage 7: WebIQ grounding retrieval

For each approved query pair, the worker sends only the `grounding_query` to WebIQ Search. The search request uses:

- The approved locale.
- Passage-formatted content.
- A maximum of five results.
- A maximum excerpt length of 1,500 characters.
- Strict SafeSearch.

Each returned result is normalized into a `Source` containing:

- A query-scoped evidence ID such as `q-1-source-1`.
- URL and title.
- Retained passage.
- Returned position from 1 to 5.
- Provider trace and crawl metadata.
- Live provenance.

The complete ordered result set is saved as the query's `RetrievalResult`. A failed search is recorded as an error packet and is not silently retried.

The saved packet is the experimental grounding environment for that query. Returned position describes only the order in this specific top-five packet. It is not a global search ranking and does not establish a causal ranking factor. See [`WebIQ.search`](../geo-agent/src/geo_agent/webiq.py) and [`EvaluationHandler`](../geo-agent/src/geo_agent/evaluation_workflow.py).

### Stage 8: Controlled LLM provider survey

The application then conducts an evidence-packet survey across the configured evaluator profiles. The standard roster can contain up to three profiles:

- ChatGPT-style, using an OpenAI Responses provider.
- Claude-backed, using an Anthropic Messages provider.
- Copilot-style, using an OpenAI Responses provider with a task-oriented evidence-gap instruction.

These labels describe controlled evaluator configurations. They are not measurements of the public consumer ChatGPT, Claude, or Copilot products.

For each of the five intents:

1. The application takes the paired `chat_query`.
2. It supplies the exact saved WebIQ result packet for that query.
3. It invokes each configured profile separately.
4. It saves the answer, citation IDs, model metadata, token usage, status, and the exact sources supplied.

With three profiles, the standard survey attempts **15 answers**, five queries multiplied by three profiles.

#### Experimental control

Every profile receives the same ordered WebIQ packet for a given query. The target page snapshot is not injected into the evaluator prompt. Therefore:

- A model cannot earn exact-page citation credit merely because the application already knows the target page.
- A target citation is possible only if the exact page appears in the saved WebIQ packet and the model reports its evidence ID.
- Differences between profiles are observed against a shared evidence environment.

The evaluator guard requires the model to:

- Use only supplied evidence.
- Ignore instructions found inside source text.
- Avoid browsing or tool use.
- Avoid forcing the target brand or URL.
- Cite only supplied evidence IDs.
- State when evidence is insufficient.
- Return a concise answer and structured citation list.

The application validates that the returned query ID, profile ID, evidence packet, and provenance exactly match the approved inputs. See [`providers.py`](../geo-agent/src/geo_agent/providers.py), [`EVALUATOR_PROMPT`](../geo-agent/src/geo_agent/foundry.py), and [`MeasurementResults`](../geo-agent/src/geo_agent/contracts.py).

### Stage 9: Deterministic analysis and scoring

Scoring does not require another LLM call. It is recomputed from the saved measurement record.

#### 9.1 Exact-page citation

For every completed answer, citation IDs are resolved against that answer's supplied source packet. A citation is classified as:

- `exact-page`
- `same-domain-other-page`
- `other-page`
- `unsupported`

The primary score counts completed answers with at least one exact-page citation.

#### 9.2 WebIQ target presence

For each completed retrieval packet, the application records:

- Whether the exact target page was returned.
- Its earliest returned position.
- Retrieval failures or missing packets.

This result remains separate from LLM citation performance. A page may be returned but not cited, or may be absent from the packet and therefore impossible to cite.

#### 9.3 Comparability and coverage

The report calculates:

- Overall score.
- Score by evaluator profile.
- Score by query.
- Branded versus unbranded query results.
- Common completed query set across profiles.
- Intended, completed, failed, and missing answer counts.
- Provisional status when coverage is incomplete.

Cross-profile comparisons should use the common completed query set so one provider is not advantaged by a different denominator.

#### 9.4 Brand and evidence assessment

The optional evidence assessment uses the saved brand definition to measure literal brand presence in retrieved sources and answers. It also records valid citations, unsupported IDs, competitor-domain signals, and source-level brand-to-citation conversion. Matching is literal and bounded; it does not infer sentiment, endorsement, semantic support, or hidden model preference.

See [`evaluation.py`](../geo-agent/src/geo_agent/evaluation.py) and [`evidence_assessment.py`](../geo-agent/src/geo_agent/evidence_assessment.py).

### Stage 10: Recommendations specialist agent

After evaluation completes with at least one usable answer, the project orchestrator queues the Recommendations specialist stage. Provider selection follows this order:

1. A project-specific Recommendations Agent binding.
2. A default environment Recommendations Agent binding.
3. The baseline recommendation provider.

This stage does not perform a new WebIQ search. It builds a bounded context from the saved measurement.

#### Comparison-source selection

For each query, the application considers non-target sources from the saved WebIQ packet and selects up to two distinct comparison sources. A source is eligible when it provides at least one observable comparison signal:

- It appeared at a higher returned position than the exact target page.
- It was cited by a completed evaluator answer.
- The exact target page was not returned in that query's packet.

The context also states whether the comparison is based on retrieval plus completed answers or retrieval only.

#### Recommendation generation

The specialist can propose up to three draft tasks. Each task must include:

- Priority.
- Related approved query ID.
- Target page section.
- Proposed change.
- Evidence-bound rationale.
- Low or medium confidence.
- A verification method.
- One or two exact quotes from the target-page snapshot.
- One or two exact quotes from eligible comparison sources for the same query.

The application then rebuilds the canonical recommendation context and validates every ID, quote, hash, source selection, and measurement binding. Invalid or unsupported output fails closed.

Recommendations are hypotheses, not measured causes or guaranteed gains. A source's returned position is not proof that its content caused the position. A gap means the content was not observed in the retained excerpt, not that it is absent from the complete page or site.

Every recommendation is saved as `draft`, requires human approval, and carries `publish_permission: false`. See [`recommendations.py`](../geo-agent/src/geo_agent/recommendations.py), [`specialist_workflow.py`](../geo-agent/src/geo_agent/specialist_workflow.py), and [`specialist_agents.py`](../geo-agent/src/geo_agent/specialist_agents.py).

### Stage 11: Deterministic content strategy

In addition to the model-backed recommendation report, the application can generate a deterministic content-strategy report from the saved evidence with no new provider call.

It looks for non-exclusive English wording cues associated with:

- Definitions and explanations.
- How-to and implementation content.
- Comparisons and alternatives.
- Research and quantified evidence.
- Features, capabilities, and integrations.
- Pricing and access.

For each observed pattern, the report links:

- Grounding-source quotes.
- Queries where the pattern appeared.
- Returned positions.
- Which evaluator profiles cited those sources.
- Similar wording in final answers.
- Existing target-page evidence, if found.
- A proposed content experiment and verification approach.

This is a literal excerpt-pattern method, not a semantic content-quality judge. See [`build_content_strategy`](../geo-agent/src/geo_agent/recommendations.py).

### Stage 12: Human review and export

The owner can accept or reject each saved recommendation. The review is cryptographically bound to:

- The approved inputs.
- The saved measurement.
- The recommendation report.
- The complete set of recommendation task IDs.

Acceptance records a planning decision only. It does not grant editing or publishing permission.

The export process can produce a deterministic measurement bundle containing the retained inputs, evidence, answers, scores, recommendations, review status, and accepted recommendation planning files. Existing artifacts remain immutable. See [`artifacts.py`](../geo-agent/src/geo_agent/artifacts.py) and [`MeasurementCoordinator.review_recommendations`](../geo-agent/src/geo_agent/measurement_workflow.py).

## 4. Run states and operational sequence

The durable workflow uses the following principal states:

| State | Meaning |
| --- | --- |
| `draft` | Run exists but preparation has not started. |
| `preparing` | Page retrieval, page analysis, or paired query planning is active. |
| `awaiting-query-approval` | Prepared inputs exist and are waiting to be bound to approval. |
| `queued` | Evaluation work has been accepted by the job system. |
| `evaluating` | WebIQ searches and LLM profile evaluations are running. |
| `recommending` | Specialist recommendation work is running. |
| `ready` | All intended evaluator results completed. |
| `partial` | At least one evaluator result completed, but coverage is incomplete. |
| `failed` | No usable evaluator result was produced or a required stage failed. |
| `needs-review` | Human attention is required. |
| `cancelled` | Queued or running work was cancelled. |
| `exported` | An immutable export was created. |

Project orchestration normally advances:

```text
Goal confirmation
  > preparing
  > awaiting-query-approval
  > automatic exact-hash approval
  > queued
  > evaluating
  > ready or partial
  > recommendations specialist stage
  > human review
  > optional export
```

Provider calls are durably claimed before execution. There are no automatic retries for ambiguous provider operations. Failed attempts consume their authorised operation allowance, reducing the risk of accidental duplicate calls or hidden cherry-picking. See [`jobs.py`](../geo-agent/src/geo_agent/jobs.py), [`measurement_worker.py`](../geo-agent/src/geo_agent/measurement_worker.py), and [`measurement_budget.py`](../geo-agent/src/geo_agent/measurement_budget.py).

## 5. Typical call shape

The standard three-profile measurement core consists of:

| Stage | Logical calls |
| --- | ---: |
| Goal summary before run | 1 Foundry call |
| Exact-page browse | 1 WebIQ call |
| Preparation analysis | 1 Foundry call |
| Paired query plan | 1 Foundry call |
| Grounding retrieval | 5 WebIQ searches |
| LLM provider survey | 15 evaluator calls |
| Recommendations specialist | Up to 1 provider or hosted-agent call |

The core prepared measurement is therefore 23 provider operations after goal confirmation: 1 browse, 1 analysis, 1 query plan, 5 searches, and 15 evaluator attempts. Goal summarisation and the recommendation stage are separate operations. Actual totals depend on the configured number of profiles, provider availability, evidence sufficiency, and whether recommendations are requested.

## 6. Methodological controls

### Evidence controls

- Page findings and query rationales are anchored to exact captured quotes.
- Search evidence is query-scoped and assigned stable IDs.
- Every evaluator sees the same packet for a given query.
- The page snapshot is not secretly added to evaluator evidence.
- Recommendation quotes are mechanically checked against saved passages.
- Provenance is retained as live, recorded, or synthetic, and the project UI displays only live measurement evidence.

### Integrity controls

- Prepared inputs have an immutable approval hash.
- Saved measurements must exactly match approved queries, profiles, packets, and provenance.
- Recommendation reports are bound to both approval and measurement hashes.
- Human reviews are bound to the recommendation hash and all task IDs.
- Durable revisions and idempotency keys prevent stale or duplicate mutations.

### Safety and authority controls

- Page and source text are treated as untrusted data.
- Model prompts prohibit browsing, tool use, credential requests, approval, editing, and publishing.
- The hosted chat agent is not the workflow authority.
- Provider work is started only through application-owned jobs and authorised execution paths.
- Recommendations require human review and never confer publish permission.

## 7. Interpretation guidance

### What the run can support

- Whether the exact target page appeared in each saved WebIQ packet.
- Whether each completed evaluator answer cited the exact target page.
- Differences among configured profiles on a common completed query set.
- Literal brand presence in saved sources and answers.
- Observable content patterns in returned and cited excerpts.
- Evidence-bound hypotheses for content experiments.

### What the run cannot support by itself

- Global search rank or total web visibility.
- Search volume or market demand.
- Hidden LLM reasoning, preferences, or source-selection motives.
- Claim-level proof that a citation supports every generated statement.
- Causal proof that a content pattern produced a returned position or citation.
- Proof that content missing from an excerpt is absent from the full page or website.
- Guaranteed uplift from a recommendation.
- Permission to edit or publish content.

## 8. Recommended repeat-measurement method

To test whether a content change may have improved GEO outcomes:

1. Preserve the original run and export its evidence.
2. Verify target facts and implement one clearly defined, original content change.
3. Create a new authorised run using the same measurement goal, locale, query methodology, and evaluator roster.
4. Review whether the regenerated query plan remains materially comparable. If it changes, report that as a methodology change rather than a clean before-and-after test.
5. Compare retrieval coverage, exact-page presence, returned positions, brand presence, exact-page citation, profile coverage, and answer wording.
6. Repeat the observation before treating a change as durable.
7. Record external factors such as page recrawl timing, provider/model changes, policy changes, and material changes in the wider search result set.

This produces directional experimental evidence. It does not isolate all external variables or establish causation.

## 9. Source map

| Area | Primary implementation |
| --- | --- |
| Goal summary | [`goal_summary.py`](../geo-agent/src/geo_agent/goal_summary.py) |
| Page retrieval and WebIQ search | [`webiq.py`](../geo-agent/src/geo_agent/webiq.py) |
| Page analysis and query prompts | [`foundry.py`](../geo-agent/src/geo_agent/foundry.py) |
| Preparation orchestration | [`preparation.py`](../geo-agent/src/geo_agent/preparation.py) |
| Measurement contracts | [`contracts.py`](../geo-agent/src/geo_agent/contracts.py) |
| Evaluation orchestration | [`evaluation_workflow.py`](../geo-agent/src/geo_agent/evaluation_workflow.py) |
| Evaluator adapters and guards | [`providers.py`](../geo-agent/src/geo_agent/providers.py) |
| Scoring | [`evaluation.py`](../geo-agent/src/geo_agent/evaluation.py) |
| Brand and evidence assessment | [`evidence_assessment.py`](../geo-agent/src/geo_agent/evidence_assessment.py) |
| Recommendations and content strategy | [`recommendations.py`](../geo-agent/src/geo_agent/recommendations.py) |
| Specialist-agent execution | [`specialist_workflow.py`](../geo-agent/src/geo_agent/specialist_workflow.py) |
| Project automation | [`project_measurements.py`](../geo-agent/src/geo_agent/project_measurements.py) |
| Durable run state and human review | [`measurement_workflow.py`](../geo-agent/src/geo_agent/measurement_workflow.py) |
| UI results presentation | [`measurement-results.tsx`](../web/components/measurement/results/measurement-results.tsx) |

