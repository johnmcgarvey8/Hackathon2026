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

## Hosted query planner for v2 measurement

Configure the separate query-planning agent for the live measurement worker:

```dotenv
GEO_QUERY_AGENT_ENDPOINT=https://hackathon-2026-geo-optimiser.services.ai.azure.com/api/projects/proj-default/agents/MissionsAndMoments/endpoint/protocols/openai/responses
GEO_QUERY_AGENT_VERSION=7
```

The worker derives the project `/openai/v1/responses` route and pins `MissionsAndMoments` version 7 through `agent_reference`. It sends the canonical URL, locale, audience and goal from the approved measurement brief, sets `store: false`, does not reuse cloud response or conversation state, and does not inject caller-defined tools. The pinned agent may use only the tools attached to that version.

The final assistant output must be one raw JSON object matching the existing five-pair `QueryPlan`, including the five required mission-and-moment combinations. The backend rejects duplicate or out-of-order pairs and anchors every returned exact quote to the correct passage in the page snapshot independently saved through Web IQ Browse. A provider, schema or evidence-validation failure is recorded before the direct Foundry planner is claimed as a separate fallback. There are no retries.

The live policy and immutable grant must authorise two query-plan calls. With one evaluator profile, the maximum outer-operation ceiling is 14 for score-only and 15 with recommendations. With three profiles, the corresponding ceilings are 24 and 25. Internal prompt-agent tool activity is controlled by the pinned agent and is not represented as separate local operation claims, so retain a conservative monetary ceiling and review one canary before broader use. Historical one-call grants must not be mutated or reused.

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
