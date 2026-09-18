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
- `/projects/[projectId]/chat`: project conversation workspace
- `/projects/[projectId]/control-plane`: project run history
- `/projects/[projectId]/integrations`: available BFF status and explicit unavailable states
- `/projects/[projectId]/cms-updates`: disabled future state

## Validation

```powershell
npm test
npm run lint
npm run build
```

The BFF token is read only in `lib/server-api.ts`. It is never returned to browser code.

## Live chat status

Keep using Next.js for the project workspace. Project APIs and saved conversations remain available when `GEO_MEASUREMENT_POLICY` is unset. The chat page loads the owner-scoped `chat-status` endpoint before enabling its composer.

The backend can select a shared default hosted Foundry agent or a project override. Chat and Integrations render its name, version, scope, readiness and owner budget from `GET /api/v2/projects/{id}/chat-status` through the BFF; no agent identifiers or credentials are hardcoded in the browser. A configured runtime is not remotely verified. Unavailable and explicitly enabled mock modes remain clearly labelled.

The shared default runtime has no tools or knowledge retrieval. Organisational context and the listed grounding integrations remain unavailable. The backend caps each message at one hosted response, 2,000 output tokens and no retries. Its initial persistent owner budget is six requests shared across projects; the displayed counts come from the runtime response. Existing legacy chat budgets are unchanged.

Chat refreshes saved history and runtime/budget after each message. Conversation selection and creation are locked during sends. Failed turns preserve the draft and show the error. If a response is lost, the UI reconciles the saved conversation with GET requests before allowing another send; it never automatically retries a message POST. Failed recovery or a running saved turn keeps sending paused, with a GET-only **Reload saved conversation** action.
