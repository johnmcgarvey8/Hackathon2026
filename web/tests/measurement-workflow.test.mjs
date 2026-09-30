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
const surveyModels = load("lib/survey-models.ts");

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

test("measurement pages start one automatic live workflow", () => {
  const create = read("app/projects/[projectId]/measurements/new/page.tsx");
  const detail = read("app/projects/[projectId]/measurements/[runId]/page.tsx");
  assert.match(create, /project\.name/);
  assert.match(create, /project\.domains/);
  assert.match(create, /project\.default_locale/);
  assert.match(create, /project\.active_goal/);
  assert.match(create, /Exact in-scope page/);
  assert.match(create, /What do you want to learn/);
  assert.match(create, /api\.summarizeMeasurementGoal/);
  assert.match(create, /Confirm measurement goal/);
  assert.match(create, /fallback_used/);
  assert.match(create, /api\.createMeasurement/);
  assert.match(create, /Confirm and start live measurement/);
  assert.match(create, /No synthetic provider or fixture fallback/);
  assert.match(detail, /running automatically/);
  assert.match(detail, /Live WebIQ \+ Foundry/);
  assert.doesNotMatch(detail, /confirmPreparation|confirmEvaluation/);
  assert.doesNotMatch(detail, /Approve queries|Save query revision|Start approved run/);
  assert.doesNotMatch(detail, /api\.prepareRun|api\.approveQueries|api\.startRun|api\.recoverEvaluators/);
  assert.match(detail, /status === 409/);
  assert.match(detail, /Latest server state was reloaded/);
});

test("polling is bounded, visibility aware, and GET only", () => {
  const polling = read("components/measurement/use-run-polling.ts");
  assert.match(polling, /MAX_POLLS = 120/);
  assert.match(polling, /document\.visibilityState/);
  assert.doesNotMatch(polling, /prepareRun|startRun|cancelJob|method:\s*"POST"/);
});

test("results are grouped by query and provider answers use safe markdown", () => {
  const results = read("components/measurement/results/measurement-results.tsx");
  for (const heading of [
    "Grounding queries and brand presence",
    "Inference inputs",
    "Grounding supplied to the LLM",
    "LLM Provider Survey",
    "Citation performance",
    "Recommendations",
    "Limitations and provenance",
  ]) assert.match(results, new RegExp(heading));
  assert.match(results, /Exact page cited/);
  assert.match(results, /Same-domain page cited/);
  assert.match(results, /Other-domain page cited/);
  assert.match(results, /Raw URL mention, not a citation/);
  assert.match(results, /Unsupported citation ID/);
  assert.match(results, /No citation/);
  assert.match(results, /grounding citations supplied to the LLM during inference/);
  assert.match(results, /Post-inference/);
  assert.match(results, /post-inference provider responses/);
  assert.match(results, /measurement-query-group/);
  assert.match(results, /provider-survey-response/);
  assert.match(results, /answerBrandFindings\.find/);
  assert.match(results, /answerBrandPresentation\(brandFinding\.brand\.status, brandName\)/);
  assert.match(results, /\$\{brandName\} mentioned/);
  assert.doesNotMatch(results, />\{answer\.status\}<\/span>/);
  assert.ok(results.indexOf('id="model-answers"') > results.indexOf('id="query-plan"'));
  assert.match(results, /query\?\.chat_query \|\| "Prompt unavailable"/);
  assert.doesNotMatch(results, /providerSurveyLabel|Survey response #/);
  assert.match(results, /surveyModelRoster/);
  assert.match(results, /configured deployment/);
  assert.match(results, /modelByProfile/);
  assert.doesNotMatch(results, />\{answer\.profile_id\}/);
  assert.match(results, /formatGroundingQueryTitle/);
  assert.match(results, /Grounding citation/);
  assert.match(results, /providerAnswerCitations/);
  assert.match(results, /<AssistantMarkdown/);
  assert.match(results, /content=\{answer\.answer\}/);
  assert.match(results, /onCitationClick/);
  assert.match(results, /Grounding query/);
  assert.match(results, /groundingMatchPresentation/);
  assert.match(results, /brandSources\.get\(source\.evidence_id\) === "matched"/);
  assert.match(results, /Brand result unknown/);
  assert.doesNotMatch(results, /Brand presence summary/);
  assert.doesNotMatch(results, /id="brand-presence"/);
  assert.match(results, /findingLabels\(finding\)\.join/);
  assert.match(results, /Results blocked/);
  assert.match(results, /non-live data/);
  assert.doesNotMatch(results, /dangerouslySetInnerHTML/);
});

test("measurement detail provides accessible responsive section navigation", () => {
  const controlPlaneDetail = read("app/projects/[projectId]/control-plane/[runId]/page.tsx");
  const sectionNav = read("components/measurement/measurement-section-nav.tsx");
  const styles = read("app/globals.css");
  for (const [id, label] of [
    ["brief", "Brief"],
    ["query-plan", "Grounding & Brand Presence"],
    ["model-answers", "LLM Provider Survey"],
    ["citation-performance", "Citation Performance"],
    ["recommendations", "Recommendations"],
  ]) {
    assert.match(sectionNav, new RegExp(`id: "${id}", label: "${label}"`));
  }
  assert.match(controlPlaneDetail, /id="brief"/);
  assert.match(controlPlaneDetail, /<MeasurementSectionNav \/>/);
  assert.match(sectionNav, /new IntersectionObserver/);
  assert.match(sectionNav, /root: scrollRoot/);
  assert.match(sectionNav, /aria-current=\{activeSection === section\.id \? "location"/);
  assert.match(sectionNav, /aria-label="Measurement run sections"/);
  assert.match(styles, /\.measurement-run-layout/);
  assert.match(styles, /\.measurement-section-nav/);
  assert.match(styles, /@media \(max-width: 1100px\)/);
  assert.match(styles, /scroll-behavior: smooth/);
  assert.match(styles, /prefers-reduced-motion: reduce/);
});

test("chat renders query-centric workflow evidence without exposing internal IDs", () => {
  const chat = read("app/projects/[projectId]/chat/page.tsx");
  const types = read("lib/types.ts");
  assert.match(chat, /measurement_workflows/);
  assert.match(types, /linked_run_ids/);
  assert.match(chat, /turn\.origin !== "workflow"/);
  assert.match(chat, /Measurement insight/);
  assert.match(chat, /evidencePresentation/);
  assert.match(chat, /inferEvidenceType/);
  assert.match(chat, /groupGroundingEvidence/);
  assert.match(chat, /formatGroundingQueryTitle/);
  assert.match(chat, /measurementRunEvidence/);
  assert.match(chat, /api\.run\(project\.project_id, workflowRunId\)/);
  assert.match(chat, /Loading complete measurement evidence/);
  assert.match(chat, /Measurement run/);
  assert.match(chat, /Grounding queries/);
  assert.match(chat, /LLM Provider Survey/);
  assert.match(chat, /<details className="grounding-query-group"/);
  assert.match(chat, /source-card source-card-link/);
  assert.match(chat, /Open in Control Plane/);
  assert.match(chat, /control-plane\/\$\{encodeURIComponent\(workflowRunId\)\}/);
  assert.match(chat, /\[\.\.\.runSources, \.\.\.drawerSources\]/);
  assert.match(chat, /No saved WebIQ citations are available for this grounding query/);
  assert.doesNotMatch(chat, /No returned citations are included in this response/);
  assert.match(chat, /query\?\.chat_query \|\| "Survey question unavailable"/);
  assert.match(chat, /answer\.sources\.filter\(\(source\) => answer\.citation_ids\.includes\(source\.evidence_id\)\)/);
  assert.match(chat, /<AssistantMarkdown content=\{answer\.answer\}/);
  assert.match(chat, /<h5>Cited sources<\/h5>/);
  assert.match(chat, /This response did not cite any supplied grounding sources/);
  assert.match(chat, /className="source-url"/);
  assert.match(chat, /title=\{sourceUrl\}/);
  assert.match(chat, /source-subgroup-header/);
  assert.match(chat, /✓ \{source\.brand_name\} found/);
  assert.match(chat, /evidence.*record/);
  assert.match(chat, /Saved evidence from the measurement run bound to this conversation/);
  assert.doesNotMatch(chat, /<small>\{source\.source_id\}<\/small>/);
  assert.match(chat, /hideLink source=\{source\}/);
  assert.doesNotMatch(chat, /grounded \{turn\.citations\.length === 1/);
  assert.doesNotMatch(chat, /agent-surfaces|chat-run-notice|Measurement run:/);
});

test("chat-first workflow polls saved conversation while control plane remains read only", () => {
  const control = read("app/projects/[projectId]/control-plane/page.tsx");
  const detail = read("app/projects/[projectId]/control-plane/[runId]/page.tsx");
  const chat = read("app/projects/[projectId]/chat/page.tsx");
  const styles = read("app/globals.css");
  assert.doesNotMatch(control, /width:\s*"55%"/);
  assert.match(control, /operationTotals/);
  assert.doesNotMatch(control, /cancelJob|New measurement|Create measurement/);
  assert.doesNotMatch(control, />Automation</);
  assert.match(control, /control-plane\/\$\{run\.run_id\}/);
  assert.match(chat, /shouldPollMeasurementWorkflows/);
  assert.match(chat, /document\.visibilityState/);
  assert.match(chat, /attempts < 120/);
  assert.match(chat, /api\.conversation\(project\.project_id, conversationId\)/);
  assert.doesNotMatch(chat, /agent-surfaces|chat-run-notice|View in Control Plane|Manual measurement/);
  assert.match(chat, /\{filtered\.map\(\(conversation\) =>/);
  assert.doesNotMatch(chat, /Run-bound conversations|Project conversations/);
  assert.doesNotMatch(chat, /Use run \$\{runId\}|selected\?\.linked_run_ids \|\| \[\]/);
  assert.match(chat, />Sources<\/span>/);
  assert.match(chat, /aria-label=\{`Delete chat \$\{conversation\.title\}`\}/);
  assert.match(styles, /\.chat-history-item \{[^}]*min-width: 0;[^}]*flex: 1;[^}]*width: auto;/);
  assert.match(styles, /\.chat-delete \{[^}]*width: 28px;[^}]*color: var\(--text-tertiary\);/);
  assert.match(styles, /\.chat-delete \.ui-icon \{[^}]*width: 15px;/);
  assert.match(styles, /\.chat-delete:hover, \.chat-delete:focus-visible \{[^}]*color: var\(--red\);/);
  assert.match(chat, /Measure a page/);
  assert.match(detail, /MeasurementResultsView/);
  assert.match(detail, /showLimitations=\{false\}/);
  assert.match(detail, /troubleshooting-panel/);
  assert.match(detail, /Troubleshooting details/);
  assert.ok(detail.indexOf("troubleshooting-panel") > detail.indexOf("<MeasurementResultsView"));
  assert.doesNotMatch(detail, /Query plan bound automatically|<dt>Automation<\/dt>/);
  assert.match(detail, /api\.runProgress/);
  assert.match(detail, /api\.evidenceAssessment/);
  assert.match(detail, /api\.artifacts/);
  assert.doesNotMatch(detail, /prepareRun|startRun|cancelJob|exportRun|reviewRecommendations|createConversation/);
});

test("survey model labels combine friendly and exact model identities", () => {
  const roster = surveyModels.surveyModelRoster({
    profiles: [{
      profile_id: "chatgpt-style",
      provider: "openai-responses",
      deployment: "gpt-5.6",
    }],
  }, [{
    profile_id: "chatgpt-style",
    model: "gpt-5.6-2026-09-01",
  }]);
  assert.equal(roster[0].label, "ChatGPT · gpt-5.6-2026-09-01");
  assert.equal(roster[0].provider, "openai-responses");
  assert.equal(roster[0].deployment, "gpt-5.6");
});
