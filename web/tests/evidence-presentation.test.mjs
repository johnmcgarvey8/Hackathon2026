import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);

function load(path) {
  const source = readFileSync(path, "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, require });
  return exports;
}

const {
  deduplicateCitations,
  evidencePresentation,
  formatGroundingQueryTitle,
  groupGroundingEvidence,
  inferEvidenceType,
  measurementRunEvidence,
} = load("lib/evidence-presentation.ts");

test("all GEO evidence types have explicit user-facing explanations", () => {
  assert.deepEqual(
    [
      "measurement-run",
      "grounding-query",
      "grounding-citation",
      "test-answer",
    ].map((type) => evidencePresentation(type).label),
    [
      "Measurement run",
      "Grounding query",
      "Grounding citation",
      "LLM provider response",
    ],
  );
  assert.match(evidencePresentation("grounding-query").description, /sent to WebIQ/);
  assert.match(evidencePresentation("grounding-query").description, /not itself a citation/);
  assert.match(evidencePresentation("grounding-citation").description, /WebIQ passage/);
  assert.match(evidencePresentation("test-answer").description, /LLM Provider Survey/);
});

test("legacy evidence receives a neutral fallback", () => {
  const presentation = evidencePresentation();
  assert.equal(presentation.label, "Measurement evidence");
  assert.match(presentation.description, /type was not recorded/);
});

test("legacy saved chat citations recover their evidence type", () => {
  assert.equal(
    inferEvidenceType(
      null,
      "0fb51ac2-3cc9-41f9-9305-c74db308561b-query-q-1",
      "Approved query q-1: cheapest flights",
    ),
    "grounding-query",
  );
  assert.equal(
    inferEvidenceType(null, "run-evidence-q-1-source-1", "Airline source"),
    "grounding-citation",
  );
  assert.equal(
    inferEvidenceType(null, "run-answer-q-1-chatgpt", "Saved answer"),
    "test-answer",
  );
});

test("grounding query labels use human-readable numbering", () => {
  assert.equal(
    formatGroundingQueryTitle("Grounding query q-2: cheapest flights", "q-2"),
    "Grounding query #2: cheapest flights",
  );
  assert.equal(
    formatGroundingQueryTitle("Approved query q-12: loyalty offers", "q-12"),
    "Grounding query #12: loyalty offers",
  );
  assert.equal(formatGroundingQueryTitle("", "q-3"), "Grounding query #3");
});

test("grounding citations are nested by query and unmatched evidence is retained", () => {
  const citation = (overrides) => ({
    source_class: "geo-evidence",
    geo_evidence_type: "grounding-citation",
    source_id: "run-evidence-unknown",
    title: "Citation",
    url: "https://example.com/",
    query_id: null,
    ...overrides,
  });
  const groups = groupGroundingEvidence([
    citation({ source_id: "run-evidence-q-10-result", query_id: "q-10" }),
    citation({
      geo_evidence_type: "grounding-query",
      source_id: "run-query-q-2",
      title: "Grounding query q-2: second",
      query_id: "q-2",
    }),
    citation({ source_id: "run-evidence-q-2-result", query_id: "q-2" }),
    citation({ source_id: "legacy-evidence-without-query" }),
  ]);

  assert.equal(groups.length, 3);
  assert.equal(groups[0].queryId, "q-2");
  assert.equal(groups[0].query.title, "Grounding query q-2: second");
  assert.equal(groups[0].citations.length, 1);
  assert.equal(groups[1].queryId, "q-10");
  assert.equal(groups[1].citations.length, 1);
  assert.equal(groups[2].queryId, null);
  assert.equal(groups[2].citations.length, 1);
});

test("repeated citations across conversation turns keep one latest record", () => {
  const shared = {
    source_class: "geo-evidence",
    geo_evidence_type: "measurement-run",
    source_id: "run-one",
    url: null,
  };
  const unique = deduplicateCitations([
    { ...shared, title: "Project measurement run" },
    { ...shared, title: "Easyjet measurement run run-one" },
    { ...shared, title: "Latest measurement run title" },
  ]);

  assert.equal(unique.length, 1);
  assert.equal(unique[0].title, "Latest measurement run title");
});

test("sidebar inventory includes the complete saved run instead of bounded model context", () => {
  const queries = Array.from({ length: 5 }, (_, index) => ({
    query_id: `q-${index + 1}`,
    grounding_query: `Grounding text ${index + 1}`,
  }));
  const retrievals = queries.map((query) => ({
    query_id: query.query_id,
    sources: Array.from({ length: 5 }, (_, index) => ({
      evidence_id: `${query.query_id}-source-${index + 1}`,
      title: `Source ${index + 1}`,
      url: `https://example.com/${query.query_id}/${index + 1}`,
    })),
  }));
  const citations = measurementRunEvidence({
    run_id: "run-one",
    inputs: { query_plan: { queries } },
    measurement: { retrievals, results: [] },
  }, "Easyjet");

  assert.equal(citations.filter((item) => item.geo_evidence_type === "grounding-query").length, 5);
  assert.equal(citations.filter((item) => item.geo_evidence_type === "grounding-citation").length, 25);
  assert.ok(citations.some((item) => item.query_id === "q-5"));
});
