import type { ChatCitation } from "./types";

export type CitationTextSegment =
  | { kind: "text"; value: string }
  | { kind: "citation"; value: string; citation: ChatCitation };

export function splitCitationReferences(
  text: string,
  citations: ChatCitation[],
): CitationTextSegment[] {
  if (!text || citations.length === 0) {
    return text ? [{ kind: "text", value: text }] : [];
  }

  const citationTokens = new Map<string, ChatCitation>();
  for (const citation of citations) {
    const token = `[${citation.source_id}]`;
    if (!citationTokens.has(token)) citationTokens.set(token, citation);
  }

  const segments: CitationTextSegment[] = [];
  let cursor = 0;

  while (cursor < text.length) {
    let nextIndex = -1;
    let nextToken = "";

    for (const token of citationTokens.keys()) {
      const index = text.indexOf(token, cursor);
      if (
        index >= 0
        && (nextIndex === -1 || index < nextIndex || (index === nextIndex && token.length > nextToken.length))
      ) {
        nextIndex = index;
        nextToken = token;
      }
    }

    if (nextIndex === -1) {
      segments.push({ kind: "text", value: text.slice(cursor) });
      break;
    }

    if (nextIndex > cursor) {
      segments.push({ kind: "text", value: text.slice(cursor, nextIndex) });
    }

    segments.push({
      kind: "citation",
      value: nextToken,
      citation: citationTokens.get(nextToken)!,
    });
    cursor = nextIndex + nextToken.length;
  }

  return segments;
}
