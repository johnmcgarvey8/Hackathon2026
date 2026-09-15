# Microsoft GEO Optimizer workflow mockup

**BLUF:** Open `index.html` to explore a standalone, synthetic click-through of the proposed project, Chat, Control Plane, Microsoft IQ, recommendation, and governed CMS workflow.

## Run the mockup

No installation or development server is required:

1. Open `mockups\index.html` in a modern browser.
2. Open a project from its card to view its Dashboard. Choose **All projects** in the top picker to return to the project cards.
3. Use **Chat** to enter a GEO goal and a domain or page URL. Use the chat-history sidebar to resume a previous discussion or start a new chat.
4. Start the analysis and follow the simulated agents in **Control Plane**.
5. Skip to or wait for the prioritized results, then select recommendations.
6. Prepare and review the proposed changes in **CMS Updates**.
7. Approve or reject each item, approve the final bundle, create a validated CMS draft, and separately approve publication.

## Demonstrated product concepts

The visual styling follows the light theme in the repository's `geo-optimiser.html`: Segoe UI typography, neutral grey surfaces, `#0067b8` accents, 5px controls, 8px panels, and Microsoft-colour section rules. These shared tokens and the responsive workspace styling live in `workspace.css`; the reference HTML itself is unchanged.

| Area | Mock behavior |
| --- | --- |
| Projects | Simple brand and URL cards with an Open project action, accessible through All projects in the top picker |
| Dashboard | Full-width, smooth 30-day GEO score chart with calculated percentage growth, followed by opportunity/review cards and project context; no schedule cards or duplicate navigation buttons |
| Chat | Focused conversation canvas, searchable project history, source drawer, anchored composer, progress checkpoints, and result handoff |
| Integrations | Analytics, Content destinations, and Microsoft IQ accordions. Content destinations always lists Sitecore, Shopify, and Azure Static Web Apps with their logos and per-project connection state |
| Control Plane | Current execution card and Project History only |
| WebIQ | Always-on external grounding and evidence layer, not a project integration |
| Microsoft Clarity | Optional, project-specific analytics source |
| Microsoft IQ | Conceptual project knowledge layer referencing approved Word and SharePoint brand sources |
| Results | Prioritized actions with impact, effort, evidence, brand alignment, confidence, ownership, and verification |
| CMS Updates | Item sidebar with a status filter, per-item destination labels, exact field-level before/after values, item and bundle review, drafting, checks, and publication approval |

## Demo scenarios and navigation

- **All projects** uses a full-width chooser without a sidebar. Project-specific routes require selecting a project first.
- **Microsoft IQ** lists customer documents with Word and SharePoint icons. Select a document for its version, owner, and referenced sections. **Add Files** selects Word, PDF, or text filenames for that project's demo list. Contents are never read, uploaded, or used by agents; selections clear on refresh.
- **Chat** supports Enter to send and Shift+Enter for a new line. Sources are available on demand; the conversation-history panel collapses behind a button on phones.
- The Chat shell remains fixed while its transcript and history scroll independently. Suggested prompts use rounded cards. The CMS handoff is inline and appears only after selecting recommendations; there is no floating selection overlay.
- The compact **Agent surfaces** strip identifies Teams, Custom Web Apps, and MCP as planned channels, not working connections.
- **Dashboard** uses 31 synthetic daily score points, including the baseline 30 days earlier. Percentage growth is calculated from the first and last values; an expandable data table provides the daily scores.
- Expand **Demo source controls** to simulate changed or unavailable brand guidance. Refresh sources before re-reviewing affected proposals.
- Daylesford carries a third source, `Daylesford Landing Page Guidelines.docx`, as a placeholder. Its section outline is shown, the guidance content is not written yet.
- Start runs from **Chat** and follow their live checkpoints in **Control Plane**. Project History retains completed and failed executions. Demo-scenario controls and maintenance panels are not shown.
- Agent executions use their displayed per-stage durations: about **9m 37s** with Clarity, or **8m 19s** without it. Elapsed time and stage progress update while you navigate elsewhere. **Skip to results (demo)** explicitly fast-forwards a run; cancellation stops it. No real provider work occurs.
- CMS changes retain their audit trail and prior bundles in the bundle selector. Stale brand versions invalidate approvals for unpublished items.
- Routine updates can publish after human approval. Landing pages and blog concepts additionally require simulated copywriter, designer, and brand reviews.
- **CMS Updates** has a left sidebar listing every proposed item. Choose **All items** for the full bundle or a single row to focus one proposal; **Review bundle** and **Status** selectors sit above the list, and statuses with no items are disabled.
- In **CMS Updates**, **Review** shows the destination, content item, changed fields, proposed copy or JSON, supporting evidence, and checks before publishing. Arbitrary risk ratings and the explanatory governance card are omitted.
- Landing-page proposals target the project's **Azure Static Web Apps** static-hosting endpoint; all other items target the project CMS. Each item shows its destination logo and environment.
- Refresh the page to restore the initial fixtures.

Chat histories, approvals, connector states, and executions are held in memory. Switching projects preserves them during the session; refreshing the page resets them. Only the selected project identifier is stored in session storage. All entered values should be fictitious.

## Governance boundaries

- All projects, metrics, source excerpts, brand documents, recommendations, and update results are synthetic.
- The mockup makes no WebIQ, Clarity, Microsoft 365, Sitecore, Shopify, Azure, Foundry, or backend API calls.
- Connector logos in `assets` are simplified local SVG renditions used for product identification in this demo. Sitecore, Shopify, and Azure are trademarks of their respective owners.
- WebIQ grounding is shown as a standard platform capability for every eligible run.
- Clarity is included only when configured for the selected project.
- Microsoft IQ is represented as access to explicitly connected Word and SharePoint sources, not unrestricted tenant search.
- Recommendation generation waits for the simulated brand-context gate.
- Preparing CMS updates does not change a website.
- Site Content Agent activity creates a simulated non-live draft.
- Publication always requires a separate human approval.
- Landing pages and blog concepts remain in copywriter, designer, brand, and final publication workflows.
- Final bundle approval locks item decisions and reviewer notes through drafting and publication.
- Switching projects preserves each project's conversation and running analysis separately.

## Relationship to the current application

This folder is a future-state design artifact and does not replace or modify:

- `geo-agent\src\geo_agent\chat.html`
- `geo-agent\src\geo_agent\api.py`
- the local SQLite run store
- the current evidence, approval, budget, or recommendation contracts

The prototype intentionally borrows the current application's evidence provenance, explicit approval, retained failure, bounded execution, recommendation confidence, and no-automatic-publishing principles.
