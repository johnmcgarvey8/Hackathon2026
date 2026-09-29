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
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports,
    require: (name) => imports[name] ?? require(name),
  });
  return exports;
}

const { splitCitationReferences } = load("lib/chat-markdown.ts");
const citation = (source_id, title = source_id) => ({
  source_class: "geo-evidence",
  source_id,
  title,
  url: null,
});

test("structured citation IDs are separated from surrounding answer text", () => {
  const sources = [citation("source-1"), citation("source.2+(test)")];
  const result = splitCitationReferences(
    "First [source-1], then [source.2+(test)]. Repeat [source-1].",
    sources,
  );

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.map((segment) => [segment.kind, segment.value]))),
    [
      ["text", "First "],
      ["citation", "[source-1]"],
      ["text", ", then "],
      ["citation", "[source.2+(test)]"],
      ["text", ". Repeat "],
      ["citation", "[source-1]"],
      ["text", "."],
    ],
  );
  assert.equal(result[1].citation, sources[0]);
  assert.equal(result[3].citation, sources[1]);
});

test("unknown bracketed IDs remain readable plain text", () => {
  const result = splitCitationReferences(
    "Supported [known-id], unsupported [unknown-id].",
    [citation("known-id")],
  );

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.map((segment) => [segment.kind, segment.value]))),
    [
      ["text", "Supported "],
      ["citation", "[known-id]"],
      ["text", ", unsupported [unknown-id]."],
    ],
  );
});

test("empty text and answers without structured citations stay plain", () => {
  assert.deepEqual(JSON.parse(JSON.stringify(splitCitationReferences("", [citation("source-1")]))), []);
  assert.deepEqual(
    JSON.parse(JSON.stringify(splitCitationReferences("No citations here.", []))),
    [{ kind: "text", value: "No citations here." }],
  );
});

test("chat renders assistant answers through safe Markdown only", () => {
  const chat = readFileSync("app/projects/[projectId]/chat/page.tsx", "utf8");
  const renderer = readFileSync("components/assistant-markdown.tsx", "utf8");

  assert.match(chat, /<AssistantMarkdown/);
  assert.match(chat, /content=\{turn\.answer \|\| ""\}/);
  assert.match(chat, /<p>\{turn\.message\}<\/p>/);
  assert.match(renderer, /remarkPlugins=\{\[remarkGfm\]\}/);
  assert.match(renderer, /skipHtml/);
  assert.doesNotMatch(renderer, /rehypeRaw|dangerouslySetInnerHTML/);
});
