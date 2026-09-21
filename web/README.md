# Microsoft GEO Optimizer web

Frontend-only Next.js App Router implementation of the approved Microsoft GEO Optimizer mockup. The browser calls local Next.js BFF route handlers. Those handlers add the FastAPI bearer token server-side and proxy project-scoped requests to `/api/v2`.

## Start locally

1. Install dependencies:

   ```powershell
   cd web
   npm install
   ```

2. Copy the environment template:

   ```powershell
   Copy-Item .env.example .env.local
   ```

3. Set `FASTAPI_BASE_URL` and `FASTAPI_BEARER_TOKEN` in `.env.local`. Do not use a `NEXT_PUBLIC_` prefix for the token.

4. Start the FastAPI service, then start Next.js:

   ```powershell
   npm run dev
   ```

5. Open `http://127.0.0.1:3000/projects`.

Both `npm run dev` and `npm start` bind to `127.0.0.1` by default. This server-token architecture is for local, single-user use only. Do not expose the BFF on a public or shared network.

## Routes

- `/projects`: project chooser
- `/projects/[projectId]`: dashboard
- `/projects/[projectId]/chat`: primary chat-first measurement and conversation workspace
- `/projects/[projectId]/measurements/new`: secondary manual measurement workflow
- `/projects/[projectId]/measurements/[runId]`: manual workflow preparation, approval, execution, and recovery
- `/projects/[projectId]/control-plane`: read-only measurement progress and project run history
- `/projects/[projectId]/control-plane/[runId]`: read-only results, saved downloads, and audit detail
- `/projects/[projectId]/integrations`: available BFF status and explicit unavailable states
- `/projects/[projectId]/cms-updates`: disabled future state

## Validation

```powershell
npm test
npm run lint
npm run build
```

The BFF token is read only in `lib/server-api.ts`. It is never returned to browser code.

## Measurement workflow

Chat is the primary measurement surface. A message containing a measurement request, valid in-scope project URL, and objective starts the backend workflow automatically. Chat displays the saved collecting, preparing, evaluating, completed, or failed status and returns the workflow-origin insight without inventing a user message. Pasting a valid run UUID into a new chat lets the backend bind that conversation to the saved run.

While a chat workflow is preparing or evaluating, the browser performs bounded, visibility-aware GET polling of the saved conversation, run, and progress. Polling pauses while the page is hidden and never retries a mutation. The **Manual measurement** link remains available for the secondary explicit workflow.

All measurement traffic uses project-scoped BFF routes. The active Project supplies the brand name, allowed domains, locale, goal, ownership, and isolation boundary.

Chat-first measurements use the authenticated user request as the instruction to create the run, prepare it, approve the exact generated query hash, and start evaluation. The application owns those mutations; the hosted Foundry agent receives no measurement tools. The secondary manual workflow retains separate preparation and evaluation confirmations, revision-bound query editing, and exact-hash approval. The Control Plane and its run detail route are strictly read-only. They load saved progress, results, ancillary assessments, jobs, events, and existing artifacts. Existing GET-only downloads are available, but the Control Plane never creates exports or exposes prepare, approve, start, review, recovery, or cancellation actions.

Run results keep Query plan, WebIQ evidence, Model answers, Citation performance, Brand presence, Recommendations, and Limitations separate. Model answers and passages in the results views render as text. Chat assistant replies render safe GitHub-flavoured Markdown with raw HTML disabled. Inline citation actions are created only when a bracketed ID matches the turn's structured citation metadata; unknown IDs remain visible as text. Artifact downloads pass through a binary BFF that preserves media type, filename, ETag, and private no-store caching.

## Live chat status

Keep using Next.js for the project workspace. Project APIs and saved conversations remain available when `GEO_MEASUREMENT_POLICY` is unset. The chat page loads the owner-scoped `chat-status` endpoint before enabling its composer.

The backend can select a shared default hosted Foundry agent or a project override. Chat and Integrations render its name, version, scope, and readiness from `GET /api/v2/projects/{id}/chat-status` through the BFF. No agent identifiers or credentials are hardcoded in the browser. A configured runtime is not remotely verified. Unavailable and explicitly enabled mock modes remain clearly labelled.

The shared default runtime has no tools or knowledge retrieval. Organisational context and the listed grounding integrations remain unavailable. The backend caps each message at one hosted response, 2,000 output tokens, and no retries. Application-level lifetime request allowance labels and decisions are not part of the frontend. Azure service quota and consumption remain separate operational concerns.

Chat refreshes saved history and runtime after each message. Conversation selection and creation are locked during sends. Failed turns preserve the draft and show the error. If a response is lost, the UI reconciles the saved conversation with GET requests before allowing another send; it never automatically retries a message POST. Failed recovery or a running saved turn keeps sending paused, with a GET-only **Reload saved conversation** action.

**Discuss this run** always creates a new conversation with an immutable `run_id` binding. Bound and unbound conversations appear in separate history groups. Switching Project remounts the workspace and clears selected conversation, drafts, drawers, recovery keys, and cached run evidence.
