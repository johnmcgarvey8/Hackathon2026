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
});

test("chat and integrations render server-selected runtime identity and budget", () => {
  for (const path of [
    "app/projects/[projectId]/chat/page.tsx",
    "app/projects/[projectId]/integrations/page.tsx",
  ]) {
    const source = read(path);
    assert.match(source, /api\.chatStatus\(project\.project_id\)/);
    assert.match(source, /agentLabel\(runtime\)/);
    assert.match(source, /budgetLabel\(runtime\)/);
    assert.match(source, /Configuration is not remote verification/);
    assert.doesNotMatch(source, /ggs-geo-hackathon2026|project\.foundry_status/);
  }
  assert.match(read("lib/types.ts"), /mode: "foundry" \| "mock" \| "unavailable"/);
});

test("dev and production servers bind to loopback", () => {
  const { scripts } = JSON.parse(read("package.json"));
  assert.match(scripts.dev, /--hostname 127\.0\.0\.1/);
  assert.match(scripts.start, /--hostname 127\.0\.0\.1/);
});
