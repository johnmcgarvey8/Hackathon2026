import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
function load(path, imports = {}) {
  const source = readFileSync(path, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports, require: (name) => imports[name] ?? require(name),
    crypto: { randomUUID: () => "new-turn-key" }, URL, Error,
  });
  return exports;
}

const labels = load("lib/chat-runtime.ts");
const evidence = load("lib/evidence-presentation.ts");
const runtime = {
  mode: "foundry", can_send: true, organisational_context_available: false,
  detail: "Configured, unverified.",
  agent: { name: "server-selected-agent", version: "42", scope: "shared-default" },
};
const conversation = { conversation_id: "chat-one", revision: 1, title: "Saved chat", turns: [], updated_at: "2026-09-17" };

test("runtime labels preserve dynamic agent identity without implying verification", () => {
  assert.equal(labels.runtimeLabel(runtime), "Live Foundry selected");
  assert.equal(labels.agentLabel(runtime), "server-selected-agent · version 42 · Shared default");
  assert.match(labels.agentLabel({ ...runtime, agent: { ...runtime.agent, scope: "project" } }), /Project override/);
  assert.equal(labels.runtimeLabel({ ...runtime, mode: "unavailable" }), "Live agent unavailable");
  assert.equal(labels.runtimeLabel(null), "Runtime status unavailable");
  assert.equal(labels.agentLabel(null), null);
});

test("chat workflow polling is limited to active backend workflow states", () => {
  assert.equal(labels.shouldPollMeasurementWorkflow({ status: "preparing" }), true);
  assert.equal(labels.shouldPollMeasurementWorkflow({ status: "evaluating" }), true);
  assert.equal(labels.shouldPollMeasurementWorkflow({ status: "collecting" }), false);
  assert.equal(labels.shouldPollMeasurementWorkflow({ status: "completed" }), false);
  assert.equal(labels.shouldPollMeasurementWorkflow({ status: "failed" }), false);
  assert.equal(labels.shouldPollMeasurementWorkflow(null), false);
  assert.equal(labels.shouldPollMeasurementWorkflows([
    { status: "completed" },
    { status: "evaluating" },
  ]), true);
  assert.equal(labels.shouldPollMeasurementWorkflows([
    { status: "collecting" },
    { status: "completed" },
  ]), false);
});

function harness(overrides = {}) {
  const calls = [];
  const states = [];
  const refs = [];
  let cursor = 0;
  let refCursor = 0;
  const saved = { ...conversation, revision: 2, turns: [{ sequence: 1, message: "Draft question", status: "completed", answer: "Saved answer", citations: [], idempotency_key: "new-turn-key" }] };
  const api = Object.fromEntries(Object.entries({
    conversations: async () => [saved],
    chatStatus: async () => runtime,
    conversation: async () => saved,
    sendMessage: async () => saved,
    createConversation: async () => conversation,
    ...overrides,
  }).map(([name, fn]) => [name, (...args) => { calls.push(name); return fn(...args); }]));
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      return [states[index], (value) => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
    },
    useRef(initial) { const index = refCursor++; return refs[index] ||= { current: initial }; },
    useEffect() {},
    useMemo: (fn) => fn(),
  };
  const { default: ChatPage } = load("app/projects/[projectId]/chat/page.tsx", {
    react, "@/lib/api": { api, ApiError: class extends Error {} },
    "@/lib/chat-runtime": labels,
    "@/lib/evidence-presentation": evidence,
    "@/lib/measurement-runtime": {
      pageUrl: () => "https://example.com/page",
      operationLabel: () => "Model evaluation",
      operationTotals: () => ({ planned: 0, resolved: 0, percentage: 0 }),
    },
    "next/link": { default: ({ children, ...props }) => ({ type: "a", props: { ...props, children } }) },
    "next/navigation": { useSearchParams: () => ({ get: () => null }) },
    "@/components/project-context": { useProject: () => ({ project: { project_id: "project-one", name: "Project", primary_domain: "example.com", active_goal: null } }) },
    "@/components/icons": { Icon: () => null },
    "@/components/assistant-markdown": { AssistantMarkdown: () => null },
    "@/components/status-state": { LoadingState: () => null, UnavailableState: () => null },
  });
  function render() { cursor = 0; refCursor = 0; return ChatPage(); }
  render();
  // Initial loaded workspace, before the first user interaction.
  Object.assign(states, { 0: [conversation], 1: runtime, 2: conversation, 4: "Draft question", 6: false });
  function find(predicate, node = render()) {
    if (!node || typeof node !== "object") return null;
    if (predicate(node)) return node;
    for (const child of [node.props?.children].flat(Infinity)) {
      const found = find(predicate, child ?? null);
      if (found) return found;
    }
    return null;
  }
  return { calls, states, render, find, submit: () => find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }) };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
const sendButton = (node) => node.props?.["aria-label"] === "Send message";
const newChat = (node) => node.props?.className === "new-chat-button";
const reloadButton = (node) => node.props?.children === "Reload saved conversation";

test("sending locks navigation and refreshes history and runtime", async () => {
  let finish;
  const h = harness({ sendMessage: () => new Promise((resolve) => { finish = resolve; }) });
  h.submit();
  await settle();
  assert.equal(h.find(newChat).props.disabled, true);
  assert.equal(h.find((node) => node.props?.className?.startsWith("chat-history-item")).props.disabled, true);
  h.find(newChat).props.onClick();
  h.submit();
  assert.equal(h.calls.filter((name) => name === "sendMessage").length, 1);
  assert.equal(h.calls.includes("createConversation"), false);
  finish({ ...conversation, revision: 2, turns: [{ status: "completed" }] });
  await settle();
  assert.deepEqual(h.calls, ["sendMessage", "conversation", "conversations", "chatStatus"]);
  assert.equal(h.states[4], "");
  assert.equal(h.find(newChat).props.disabled, false);
});

test("lost response uses GET recovery only and retains failed draft and error", async () => {
  const h = harness({
    sendMessage: async () => { throw new Error("Network lost"); },
    conversation: async () => ({ ...conversation, revision: 2, turns: [{ status: "failed", error: "Provider failed", citations: [] }] }),
  });
  h.submit();
  await settle();
  assert.deepEqual(h.calls, ["sendMessage", "conversation", "conversations", "chatStatus"]);
  assert.equal(h.states[2].revision, 2);
  assert.equal(h.states[4], "Draft question");
  assert.match(h.states[10], /Network lost.*Provider failed/);
  assert.equal(h.find(sendButton).props.disabled, false);
});

test("failed reconciliation blocks sends until explicit GET recovery succeeds", async () => {
  let unavailable = true;
  const h = harness({
    sendMessage: async () => { throw new Error("Network lost"); },
    conversation: async () => {
      if (unavailable) throw new Error("Offline");
      return conversation;
    },
  });
  h.submit();
  await settle();
  assert.equal(h.find(sendButton).props.disabled, true);
  h.submit();
  assert.equal(h.calls.filter((name) => name === "sendMessage").length, 1);
  unavailable = false;
  h.find(reloadButton).props.onClick();
  await settle();
  assert.equal(h.find(sendButton).props.disabled, false);
  assert.equal(h.states[4], "Draft question");
  assert.equal(h.calls.filter((name) => name === "sendMessage").length, 1);
});

test("a saved running turn blocks another send after recovery", async () => {
  const h = harness({
    sendMessage: async () => { throw new Error("Network lost"); },
    conversation: async () => ({ ...conversation, turns: [{ status: "running", citations: [] }] }),
  });
  h.submit();
  await settle();
  assert.equal(h.find(sendButton).props.disabled, true);
  assert.ok(h.find(reloadButton));
  assert.equal(h.states[4], "Draft question");
});

test("a persisted failed turn returned successfully preserves the draft", async () => {
  const failed = { ...conversation, revision: 2, turns: [{ status: "failed", error: "Provider unavailable", citations: [] }] };
  const h = harness({ sendMessage: async () => failed, conversation: async () => failed });
  h.submit();
  await settle();
  assert.equal(h.states[4], "Draft question");
  assert.equal(h.states[10], "Provider unavailable");
  assert.ok(h.calls.includes("chatStatus"));
});

test("recovery clears only the draft for the matching completed request", async () => {
  const recovered = harness({ sendMessage: async () => { throw new Error("Network lost"); } });
  recovered.submit();
  await settle();
  assert.equal(recovered.states[4], "");
  assert.match(recovered.states[10], /saved reply was recovered/);

  const unrelated = harness({
    sendMessage: async () => { throw new Error("Network lost"); },
    conversation: async () => ({ ...conversation, turns: [{ status: "completed", message: "Draft question", idempotency_key: "earlier-request", citations: [] }] }),
  });
  unrelated.submit();
  await settle();
  assert.equal(unrelated.states[4], "Draft question");
});
