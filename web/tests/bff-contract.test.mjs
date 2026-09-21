import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const read = (path) => readFileSync(join(root, path), "utf8");

test("bearer token remains in the server API module", () => {
  const serverApi = read("lib/server-api.ts");
  const browserApi = read("lib/api.ts");
  assert.match(serverApi, /FASTAPI_BEARER_TOKEN/);
  assert.doesNotMatch(browserApi, /FASTAPI_BEARER_TOKEN|Authorization:\s*`Bearer/);
});

test("project-scoped BFF routes include projectId upstream", () => {
  const routes = [
    "app/api/projects/[projectId]/chat-status/route.ts",
    "app/api/projects/[projectId]/route.ts",
    "app/api/projects/[projectId]/runs/route.ts",
    "app/api/projects/[projectId]/conversations/route.ts",
    "app/api/projects/[projectId]/conversations/[conversationId]/route.ts",
    "app/api/projects/[projectId]/conversations/[conversationId]/messages/route.ts",
    "app/api/projects/[projectId]/measurements/route.ts",
    "app/api/projects/[projectId]/runs/[runId]/route.ts",
    "app/api/projects/[projectId]/runs/[runId]/[...operation]/route.ts",
  ];
  for (const route of routes) {
    const source = read(route);
    assert.match(source, /projectId/);
    assert.match(source, /encodeURIComponent\(projectId\)/);
  }
});

test("project chat requires explicit runtime readiness and never claims organisational grounding", () => {
  const chat = read("app/projects/[projectId]/chat/page.tsx");
  assert.match(chat, /api\.chatStatus\(project\.project_id\)/);
  assert.match(chat, /!runtime\?\.can_send/);
  assert.match(chat, /disabled=\{!runtime\?\.organisational_context_available\}/);
  assert.doesNotMatch(chat, /Grounded project context/);
  assert.doesNotMatch(
    read("app/projects/[projectId]/integrations/page.tsx"),
    /Available to workflow/,
  );
});

test("switching projects remounts the project workspace", () => {
  assert.match(
    read("app/projects/[projectId]/layout.tsx"),
    /<ProjectShell key=\{projectId\}/,
  );
  const shell = read("components/project-shell.tsx");
  assert.match(shell, /safeSuffix/);
  assert.doesNotMatch(shell, /router\.push\(`\/projects\/\$\{value\}\$\{suffix\}`\)/);
});

test("chat and integrations use server-selected runtime identity without allowance labels", () => {
  const chat = read("app/projects/[projectId]/chat/page.tsx");
  assert.match(chat, /api\.chatStatus\(project\.project_id\)/);
  assert.doesNotMatch(chat, /agentLabel\(runtime\)|runtimeLabel\(runtime\)|runtime\.detail/);
  assert.doesNotMatch(chat, /budgetLabel|requests remaining|owner budget/i);
  const integrations = read("app/projects/[projectId]/integrations/page.tsx");
  assert.match(integrations, /api\.chatStatus\(project\.project_id\)/);
  assert.match(integrations, /agentLabel\(runtime\)/);
  assert.doesNotMatch(integrations, /runtime\.detail|Configuration is not remote verification/);
  assert.doesNotMatch(integrations, /budgetLabel|requests remaining|owner budget/i);
  assert.doesNotMatch(integrations, /ggs-geo-hackathon2026|project\.foundry_status/);
  assert.match(read("lib/types.ts"), /mode: "foundry" \| "mock" \| "unavailable"/);
  assert.doesNotMatch(read("lib/types.ts"), /budget\?:|remaining:\s*number/);
  assert.doesNotMatch(read("lib/chat-runtime.ts"), /budget|requests remaining/i);
});

test("measurement BFF is project scoped and preserves binary artifact headers", () => {
  const browserApi = read("lib/api.ts");
  const route = read("app/api/projects/[projectId]/runs/[runId]/[...operation]/route.ts");
  const serverApi = read("lib/server-api.ts");
  assert.match(browserApi, /createMeasurement/);
  assert.match(browserApi, /prepareRun/);
  assert.match(browserApi, /reviseQueries/);
  assert.match(browserApi, /approveQueries/);
  assert.match(browserApi, /startRun/);
  assert.match(browserApi, /cancelJob/);
  assert.match(browserApi, /requestBinary/);
  assert.doesNotMatch(browserApi, /\/api\/v2\/runs|\/api\/v2\/briefs/);
  assert.match(route, /proxyFastApiBinary/);
  assert.match(route, /encodeURIComponent\(projectId\)/);
  assert.match(route, /encodeURIComponent\(runId\)/);
  assert.match(route, /definition_version/);
  assert.match(route, /measurement_hash/);
  for (const header of ["content-type", "content-disposition", "etag"]) {
    assert.match(serverApi, new RegExp(header));
  }
});

test("dev and production servers bind to loopback", () => {
  const { scripts } = JSON.parse(read("package.json"));
  assert.match(scripts.dev, /--hostname 127\.0\.0\.1/);
  assert.match(scripts.start, /--hostname 127\.0\.0\.1/);
});
