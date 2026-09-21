import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const read = (path) => readFileSync(path, "utf8");

function load(path) {
  const { outputText } = ts.transpileModule(read(path), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, require });
  return exports;
}

const runtime = load("lib/measurement-runtime.ts");

test("measurement runtime uses explicit actions and saved operation totals", () => {
  assert.equal(runtime.actionAllowed(true), true);
  assert.equal(runtime.actionAllowed({ allowed: true }), true);
  assert.equal(runtime.actionAllowed({ allowed: false }), false);
  assert.equal(runtime.shouldPollRun({ state: "evaluating" }), true);
  assert.equal(runtime.shouldPollRun({ state: "awaiting-query-approval" }), false);
  const totals = runtime.operationTotals({
    operations: [
      { planned: 5, completed: 2, failed: 1 },
      { planned: 1, completed: 1, failed: 0 },
    ],
  });
  assert.equal(totals.planned, 6);
  assert.equal(totals.resolved, 4);
  assert.equal(totals.percentage, 67);
});

test("measurement pages preserve project authority and explicit confirmations", () => {
  const create = read("app/projects/[projectId]/measurements/new/page.tsx");
  const detail = read("app/projects/[projectId]/measurements/[runId]/page.tsx");
  assert.match(create, /project\.name/);
  assert.match(create, /project\.domains/);
  assert.match(create, /project\.default_locale/);
  assert.match(create, /project\.active_goal/);
  assert.match(create, /Exact in-scope page/);
  assert.match(detail, /confirmPreparation/);
  assert.match(detail, /confirmEvaluation/);
  assert.match(detail, /approval_hash/);
  assert.match(detail, /Queries approved\. Confirm the evaluation provider calls/);
  assert.match(detail, /Approve queries and unlock evaluation/);
  assert.match(detail, /Retry failed evaluators using saved searches/);
  assert.match(detail, /api\.recoverEvaluators/);
  assert.match(detail, /status === 409/);
  assert.match(detail, /query draft was preserved/i);
});

test("polling is bounded, visibility aware, and GET only", () => {
  const polling = read("components/measurement/use-run-polling.ts");
  assert.match(polling, /MAX_POLLS = 120/);
  assert.match(polling, /document\.visibilityState/);
  assert.doesNotMatch(polling, /prepareRun|startRun|cancelJob|method:\s*"POST"/);
});

test("results are separated and unsafe evidence remains React text", () => {
  const results = read("components/measurement/results/measurement-results.tsx");
  for (const heading of [
    "Query plan",
    "WebIQ evidence",
    "Model answers",
    "Citation performance",
    "Brand presence",
    "Recommendations",
    "Limitations and provenance",
  ]) assert.match(results, new RegExp(heading));
  assert.match(results, /Exact page cited/);
  assert.match(results, /Same-domain page cited/);
  assert.match(results, /Other-domain page cited/);
  assert.match(results, /Raw URL mention, not a citation/);
  assert.match(results, /Unsupported citation ID/);
  assert.match(results, /No citation/);
  assert.match(results, /findingLabels\(finding\)\.join/);
  assert.doesNotMatch(results, /dangerouslySetInnerHTML/);
});

test("chat loads workflow run metadata and renders workflow insights without a user bubble", () => {
  const chat = read("app/projects/[projectId]/chat/page.tsx");
  assert.match(chat, /measurement_workflow\?\.run_id/);
  assert.match(chat, /api\.run\(project\.project_id, workflowRunId\)/);
  assert.match(chat, /turn\.origin !== "workflow"/);
  assert.match(chat, /Measurement insight/);
});

test("chat-first workflow polls saved state and links to a read-only control plane", () => {
  const control = read("app/projects/[projectId]/control-plane/page.tsx");
  const detail = read("app/projects/[projectId]/control-plane/[runId]/page.tsx");
  const chat = read("app/projects/[projectId]/chat/page.tsx");
  assert.doesNotMatch(control, /width:\s*"55%"/);
  assert.match(control, /operationTotals/);
  assert.doesNotMatch(control, /cancelJob|New measurement|Create measurement/);
  assert.match(control, /control-plane\/\$\{run\.run_id\}/);
  assert.match(chat, /shouldPollMeasurementWorkflow/);
  assert.match(chat, /document\.visibilityState/);
  assert.match(chat, /attempts < 120/);
  assert.match(chat, /api\.conversation\(project\.project_id, conversationId\)/);
  assert.match(chat, /api\.run\(project\.project_id, runId\)/);
  assert.match(chat, /Manual measurement/);
  assert.match(chat, /Run-bound conversations/);
  assert.match(chat, /Project conversations/);
  assert.match(chat, /Measure a page/);
  assert.match(detail, /MeasurementResultsView/);
  assert.match(detail, /api\.runProgress/);
  assert.match(detail, /api\.evidenceAssessment/);
  assert.match(detail, /api\.artifacts/);
  assert.doesNotMatch(detail, /prepareRun|startRun|cancelJob|exportRun|reviewRecommendations|createConversation/);
});
