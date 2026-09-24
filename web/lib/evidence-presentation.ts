import type { ChatCitation, GeoEvidenceType, MeasurementRun } from "./types";

export interface EvidencePresentation {
  label: string;
  description: string;
  pillClass: string;
}

const presentations: Record<GeoEvidenceType, EvidencePresentation> = {
  "measurement-run": {
    label: "Measurement run",
    description: "Run-level context describing the measured page, scope, and saved workflow state.",
    pillClass: "blue",
  },
  "grounding-query": {
    label: "Grounding query",
    description: "The search query sent to WebIQ to retrieve external passages for the LLM Provider Survey. It is not itself a citation.",
    pillClass: "amber",
  },
  "grounding-citation": {
    label: "Grounding citation",
    description: "A WebIQ passage returned for a grounding query and made available to the LLM Provider Survey.",
    pillClass: "green",
  },
  "test-answer": {
    label: "LLM provider response",
    description: "A saved response from the LLM Provider Survey. It may cite one or more grounding passages.",
    pillClass: "purple",
  },
};

const fallback: EvidencePresentation = {
  label: "Measurement evidence",
  description: "A saved record from the bound measurement run. Its more specific evidence type was not recorded.",
  pillClass: "",
};

export function evidencePresentation(type?: GeoEvidenceType | null): EvidencePresentation {
  return type ? presentations[type] : fallback;
}

export function inferEvidenceType(
  type: GeoEvidenceType | null | undefined,
  sourceId: string,
  title: string,
): GeoEvidenceType | null {
  if (type) return type;
  if (sourceId.includes("-query-") || /^(Approved|Grounding) query /.test(title)) return "grounding-query";
  if (sourceId.includes("-evidence-")) return "grounding-citation";
  if (sourceId.includes("-answer-")) return "test-answer";
  return sourceId ? "measurement-run" : null;
}

export interface GroundingQueryGroup {
  key: string;
  queryId: string | null;
  query: ChatCitation | null;
  citations: ChatCitation[];
}

export function deduplicateCitations(sources: ChatCitation[]): ChatCitation[] {
  const unique = new Map<string, ChatCitation>();
  for (const source of sources) {
    unique.set(`${source.source_class}:${source.source_id}`, source);
  }
  return Array.from(unique.values());
}

export function measurementRunEvidence(run: MeasurementRun, projectName: string): ChatCitation[] {
  const citations: ChatCitation[] = [{
    source_class: "geo-evidence",
    geo_evidence_type: "measurement-run",
    source_id: run.run_id,
    title: `${projectName} measurement run`,
    url: null,
  }];

  for (const query of run.inputs?.query_plan.queries || []) {
    citations.push({
      source_class: "geo-evidence",
      geo_evidence_type: "grounding-query",
      source_id: `${run.run_id}-query-${query.query_id}`,
      title: `Grounding query ${query.query_id}: ${query.grounding_query}`,
      url: null,
      query_id: query.query_id,
    });
  }

  for (const retrieval of run.measurement?.retrievals || []) {
    for (const source of retrieval.sources) {
      citations.push({
        source_class: "geo-evidence",
        geo_evidence_type: "grounding-citation",
        source_id: `${run.run_id}-evidence-${source.evidence_id}`,
        title: source.title || `Saved evidence ${source.evidence_id}`,
        url: source.url,
        query_id: retrieval.query_id,
      });
    }
  }

  for (const result of run.measurement?.results || []) {
    citations.push({
      source_class: "geo-evidence",
      geo_evidence_type: "test-answer",
      source_id: `${run.run_id}-answer-${result.query_id}-${result.profile_id}`,
      title: `Saved answer ${result.query_id} / ${result.profile_id}`,
      url: null,
      query_id: result.query_id,
    });
  }

  return citations;
}

function inferredQueryId(source: ChatCitation): string | null {
  if (source.query_id) return source.query_id;
  const sourceIdMatch = source.source_id.match(/-(?:query|evidence)-(q-\d+)(?:-|$)/i);
  if (sourceIdMatch) return sourceIdMatch[1].toLowerCase();
  const titleMatch = source.title.match(/(?:Approved|Grounding) query (q-\d+)/i);
  return titleMatch?.[1].toLowerCase() || null;
}

function querySortValue(queryId: string | null): number {
  const match = queryId?.match(/^q-(\d+)$/i);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

export function formatGroundingQueryTitle(title: string, queryId?: string | null): string {
  const normalized = title.replace(
    /^(?:Approved|Grounding) query q-(\d+)(?=:|\s|$)/i,
    "Grounding query #$1",
  );
  if (normalized !== title) return normalized;

  const match = queryId?.match(/^q-(\d+)$/i);
  return match ? `Grounding query #${match[1]}` : title;
}

export function groupGroundingEvidence(sources: ChatCitation[]): GroundingQueryGroup[] {
  const groups = new Map<string, GroundingQueryGroup>();
  const unmatchedKey = "unmatched-grounding-citations";

  const groupFor = (queryId: string | null) => {
    const key = queryId || unmatchedKey;
    const existing = groups.get(key);
    if (existing) return existing;
    const group: GroundingQueryGroup = {
      key,
      queryId,
      query: null,
      citations: [],
    };
    groups.set(key, group);
    return group;
  };

  for (const source of sources) {
    const type = inferEvidenceType(source.geo_evidence_type, source.source_id, source.title);
    if (type !== "grounding-query" && type !== "grounding-citation") continue;
    const group = groupFor(inferredQueryId(source));
    if (type === "grounding-query") group.query ||= source;
    else group.citations.push(source);
  }

  return Array.from(groups.values()).sort((left, right) => {
    const numericDifference = querySortValue(left.queryId) - querySortValue(right.queryId);
    if (numericDifference !== 0) return numericDifference;
    return (left.queryId || left.key).localeCompare(right.queryId || right.key);
  });
}
