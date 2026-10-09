# Manual Microsoft Foundry Setup

**BLUF:** Create and configure the Foundry resources manually in the portal, then add their non-secret identifiers to the matching local GEO project. The application must never provision or modify Azure resources automatically.

**Implementation status:** Next.js project chat now has a tool-free hosted-agent runtime. A shared default can serve all projects while each request carries isolated local project context and history. Foundry IQ retrieval remains disabled pending its separate permission-aware integration. Legacy fixed-run chat remains a separate service.

## Shared agent for all projects

Fill these non-secret settings in `geo-agent\.env`, then restart FastAPI:

```dotenv
GEO_FOUNDRY_AGENT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>/agents/<agent-name>/endpoint/protocols/openai/responses
GEO_FOUNDRY_AGENT_NAME=<agent-name>
GEO_FOUNDRY_AGENT_VERSION=<numeric-version>
```

Current and future app projects inherit this default unless they have an explicit `foundry` binding. No knowledge base or model deployment name is required: the hosted agent already defines its model. Authentication uses the developer's Azure CLI sign-in; credentials are never sent to Next.js browser code.

## Recommendations specialist agent

The recommendation stage can use a separate pinned agent. Configure all three
values or leave all three blank:

```dotenv
GEO_FOUNDRY_RECOMMENDATIONS_AGENT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>/agents/<agent-name>/endpoint/protocols/openai/responses
GEO_FOUNDRY_RECOMMENDATIONS_AGENT_NAME=<agent-name>
GEO_FOUNDRY_RECOMMENDATIONS_AGENT_VERSION=<numeric-version>
```

When these settings are absent, the stage uses the existing direct model
recommendation provider. If the configured agent invocation fails, the stage is
saved as failed and requires an explicit retry. It does not make a second
provider call through the direct model.

The agent must return only JSON matching the application
`RecommendationProposal` contract. The application remains responsible for
evidence-ID validation, exact-quote validation, measurement and approval hash
binding, persistence, human review, and publishing restrictions.

Projects can override the environment default with a role-specific binding:

```json
{
  "expected_revision": 1,
  "specialist_agents": [
    {
      "role": "recommendations",
      "binding": {
        "project_endpoint": "https://<resource>.services.ai.azure.com/api/projects/<project>",
        "agent_name": "<agent-name>",
        "agent_version": "<numeric-version>"
      }
    }
  ]
}
```

The reserved role names are `grounding-query` and `llm-survey`. Their current
application implementations remain active until corresponding hosted-agent
adapters are implemented.

The runtime derives the project endpoint and calls its `/openai/v1/responses` API with an explicit `agent_reference` name and version. This avoids relying on the stable agent endpoint's potentially latest-version routing. It does not change Azure endpoint configuration.

Each explicit submission sends at most one request with at most 512,000 input bytes, 2,000 output tokens, `tool_choice: none` and `store: false`. Only the selected local conversation and deterministic `geo-context/v2` packet for its immutable project-bound run are included. The packet separates query plan, WebIQ evidence, model answers, citation performance, literal brand presence, recommendations, and limitations/provenance. No response ID or cloud conversation ID is reused.

Opening the UI makes no model call. The first sent message checks Azure access. The application does not impose a hosted-chat lifetime allowance; Azure service quota, rate limits, access, and consumption still apply. The Azure identity must have permission to invoke the existing agent. The UI surfaces authentication, quota, missing-agent and interrupted-request errors without automatic replay.

## MomentsAndMissions query-planning agent

**BLUF:** The measurement worker requires a pinned Foundry prompt agent that can call
WebIQ Browse without interactive approval and return one raw, schema-valid JSON query
plan. A pending MCP approval message, Markdown wrapper or extra assistant text causes
query planning to fail.

### Create the agent

1. In the same Microsoft Foundry project used by the local developer identity, create
   a prompt agent named `MomentsAndMissions`.
2. Select an approved chat model that supports MCP tool calls and structured JSON
   generation. Keep the response within the application's 2,000-output-token limit.
3. Copy the complete contents of
   [`moments-and-missions-agent-instructions.txt`](moments-and-missions-agent-instructions.txt)
   into the agent instructions.
4. Add the read-only WebIQ MCP server and expose the Browse tool. Search is not
   required by this agent.
5. Configure Browse so it does not require interactive user approval. Depending on
   the portal version, this setting may be labelled **Always approve**,
   **Auto-approve**, or **Do not require approval**.
6. Do not attach output citations or annotations. The overall response and assistant
   message must be completed, with exactly one `output_text` block.
7. Publish an immutable agent version and record its numeric version.

The local runtime cannot participate in Foundry's interactive MCP approval flow. If
Foundry returns `MCP tool call mcp_WebIQ.browse is pending user approval`, the
application receives that text instead of a query plan and records
`model-output-invalid`.

### WebIQ Browse configuration

The agent should make one Browse call for the supplied canonical URL with settings
that match the application's independently saved page snapshot:

| Setting | Required value |
| --- | --- |
| `contentFormat` | `markdown` |
| `maxLength` | `10000` |
| `liveCrawl` | `none` |
| `includeWebLinks` | `false` |
| `includeImageLinks` | `false`, when available |
| `renderDynamicPages` | `false` |
| `language` | Language parsed from the supplied locale |
| `region` | Region parsed from the supplied locale |

Using different crawl or rendering settings can produce quotes that do not match the
snapshot saved by the application. The backend independently anchors every returned
quote and rejects a query that has no matching evidence.

### Input and output contract

The worker sends one JSON input object:

```json
{
  "url": "https://www.example.com/page/",
  "locale": "en-GB",
  "audience": "People researching the category",
  "goal": "Check citation and content gaps across common user scenarios."
}
```

The agent must return one raw JSON object with a `queries` array of exactly five
items. Do not include Markdown fences, introductory text, trailing commentary,
annotations or multiple text blocks.

Each query requires:

| Field | Constraint |
| --- | --- |
| `query_id` | `q-1` through `q-5` |
| `priority` | Unique integer `1` through `5` |
| `mission`, `moment` | Exact pair shown below |
| `intent`, `rationale` | Non-empty, maximum 500 characters |
| `branded` | Boolean |
| `chat_query` | Unique natural-language user question, maximum 500 characters |
| `grounding_query` | Unique search query for the same intent, maximum 500 characters |
| `evidence` | One or two exact page quotes |

| Query | Mission | Moment |
| --- | --- | --- |
| `q-1` | `functional-planning` | `before-journey` |
| `q-2` | `functional-constraint` | `early-journey` |
| `q-3` | `transition-to-discovery` | `mid-journey` |
| `q-4` | `emotive-discovery` | `inspiration` |
| `q-5` | `decision-validation` | `point-of-decision` |

Each evidence item contains only `evidence_id` and `quote`. Use `page-1` through
`page-10` and copy exact text from the Browse result. The backend can relocate a
valid quote to the correct passage, but it cannot accept paraphrased or invented
evidence.

### Connect the published version

Add the stable Responses endpoint and published numeric version to
`geo-agent\.env`:

```dotenv
GEO_QUERY_AGENT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>/agents/MomentsAndMissions/endpoint/protocols/openai/responses
GEO_QUERY_AGENT_VERSION=<published-version>
```

The endpoint contains the agent name, so no separate query-agent name variable is
required. The worker derives the project `/openai/v1/responses` route and sends an
explicit `agent_reference` containing the parsed name and configured version. It
sets `store: false`, does not reuse cloud conversation state and does not add
caller-defined tools.

Restart the measurement worker after changing either variable. In local development,
ensure an inherited `WEBIQ_API_KEY` is not shadowing the value in `.env`.

### Validate before a measurement run

Test the published agent in Foundry with one representative input and confirm:

1. WebIQ Browse executes immediately, without a pending approval prompt.
2. The run completes rather than pausing after the MCP call.
3. The final output contains only one JSON object.
4. The object has five queries and the exact mission-and-moment combinations.
5. Every query has one or two exact quotes from the browsed page.
6. The published version in Foundry matches `GEO_QUERY_AGENT_VERSION`.

| Failure | Likely cause |
| --- | --- |
| `provider-request-failed` | Endpoint, agent name or pinned version does not exist or is inaccessible |
| `model-output-invalid` | Pending tool approval, prose or fences around JSON, annotations, multiple text blocks, missing fields or invalid mission/moment values |
| `query-plan-order-invalid` | Query IDs or priorities are missing, duplicated or out of order |
| `query-plan-evidence-invalid` | Returned quotes do not exactly match the application's saved WebIQ snapshot |

The live policy and immutable grant must authorise two query-plan calls. The first
claim invokes the hosted agent; the second is the separately recorded direct-model
fallback. There are no automatic retries. With one evaluator profile, maximum outer
operation ceilings are 14 for score-only and 15 with recommendations. With three
profiles, the corresponding ceilings are 24 and 25. Internal prompt-agent MCP
activity is controlled by the published agent and is not represented as separate
local operation claims.

## Required manual outcome

| Component | Manual action | Value needed by the local app |
| --- | --- | --- |
| Foundry project | Select or create the project in the Microsoft Foundry portal | Project endpoint ending in `/api/projects/<project-name>` |
| Model deployment | Select or deploy the approved chat model | Deployment name |
| GEO agent | Create a named, versioned prompt agent using `foundry-agent-instructions.txt` | Agent name and immutable version |
| Foundry IQ knowledge base | Create one knowledge base for the brand project | Knowledge-base identifier |
| SharePoint knowledge source | Connect one approved site or folder owned by the relevant brand/business team | Connection/source identifier and documented scope |
| Permissions | Grant the signed-in developer the minimum Foundry and SharePoint read permissions | No secret value; verify access manually |

Foundry IQ uses Azure AI Search as its managed retrieval layer. It does not replace the application's local SQLite database, local artifacts, or local worker.

## Portal checklist

1. Confirm the SharePoint scope and Foundry project are in the same Microsoft Entra tenant.
2. Select one narrow SharePoint site or folder containing approved, non-sensitive brand and business reference material.
3. In Microsoft Foundry, create or select the project and approved model deployment.
4. Create a versioned agent using the exact instructions in `foundry-agent-instructions.txt`.
5. Create a Foundry IQ knowledge base and add the approved SharePoint knowledge source.
6. Configure permission-aware retrieval and citations.
7. Attach the knowledge base to the agent.
8. Test one document the signed-in user can read and one they cannot read.
9. Record the project endpoint, model deployment, agent name/version, and knowledge-base identifier.
10. Add those identifiers to the local brand project through the project API. The current runtime deliberately rejects knowledge-enabled bindings until retrieval permissions are implemented.

## Project configuration payload

Update an existing project with its current revision:

```json
{
  "expected_revision": 1,
  "foundry": {
    "project_endpoint": "https://<resource>.services.ai.azure.com/api/projects/<project>",
    "agent_name": "geo-optimizer-<brand>",
    "agent_version": "<portal-created-version>",
    "model_deployment": "<approved-deployment>",
    "knowledge_base_id": "<portal-created-knowledge-base-id>"
  }
}
```

Send this payload to:

```text
PATCH /api/v2/projects/{project_id}
```

These values identify resources and must not contain access tokens, API keys, connection strings, or SharePoint document content.

For an agent-only override, omit `knowledge_base_id` and `model_deployment`. To resume using the shared default, PATCH the current `expected_revision` with `"foundry": null`.

## Runtime identity

- Next.js calls FastAPI with the existing local API token through server-side route handlers.
- FastAPI calls the already-created Foundry resources using the signed-in developer's delegated Azure identity.
- SharePoint and future Work IQ retrieval must preserve the signed-in user's permissions.
- Application-only access must not be used to bypass SharePoint or Microsoft 365 permissions.

## Prohibited automation

Without explicit approval for the specific action, do not run:

- `az`, `azd`, Bicep, ARM, Terraform, or Pulumi resource-creation commands.
- Azure SDK management clients or management-plane REST calls.
- Code that creates, deploys, updates, versions, or deletes Foundry agents or model deployments.
- Code that creates or changes Foundry IQ knowledge bases, Azure AI Search resources, SharePoint connections, roles, or permissions.

Missing or inaccessible resources produce a prerequisite error. Explicit mock mode remains available for development, but a configured shared hosted agent takes precedence for project chat and never silently falls back to mock replies.

## Validation questions

Use a small evaluation set before enabling the knowledge base:

1. Can the agent answer a brand terminology question and cite the approved SharePoint source?
2. Does the same question fail safely when the source is outside the configured scope?
3. Can a user without access avoid receiving restricted text or citations?
4. Does the answer keep organisational guidance separate from measured GEO evidence?
5. Does switching projects select the correct agent version and knowledge base?

## References

- Versioned Responses reference: https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/aiproject

- Foundry Agent Service: https://learn.microsoft.com/en-us/azure/foundry/agents/overview
- Foundry IQ: https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/what-is-foundry-iq
- Connect agents to Foundry IQ: https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/foundry-iq-connect
- SharePoint grounding: https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/sharepoint
- Document-level access control: https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview
