import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, MeasurementResults, digest, utc_now
from .evaluation import comparable_url, match_citations, measurement_scores


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


class BrandAlias(Contract):
    text: str = Field(min_length=1, max_length=120)
    ambiguous: bool = False


class BrandDefinition(Contract):
    name: str = Field(min_length=1, max_length=120)
    aliases: tuple[BrandAlias, ...] = Field(default=(), max_length=10)
    domains: tuple[str, ...] = Field(default=(), max_length=10)

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, domains: tuple[str, ...]) -> tuple[str, ...]:
        normalized = []
        for domain in domains:
            host = domain.strip().rstrip(".").encode("idna").decode("ascii").lower()
            if len(host) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                raise ValueError("Enter domain names only, without paths, ports or wildcards")
            if "." not in host or any(not label or len(label) > 63 or label.startswith("-")
                                     or label.endswith("-") for label in host.split(".")):
                raise ValueError("Invalid brand domain")
            if host not in normalized:
                normalized.append(host)
        return tuple(normalized)

    @model_validator(mode="after")
    def unique_aliases(self) -> "BrandDefinition":
        names = [_normalize(self.name), *(_normalize(alias.text) for alias in self.aliases)]
        if len(set(names)) != len(names):
            raise ValueError("Brand name and aliases must be distinct")
        return self

    @property
    def definition_hash(self) -> str:
        return digest(self.model_dump(mode="json"))


class BrandDefinitionRecord(Contract):
    run_id: str
    definition_version: int = Field(ge=1)
    definition: BrandDefinition
    source: Literal["manual", "page-analysis"] = "manual"
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def definition_hash(self) -> str:
        return self.definition.definition_hash


def owns_host(url: str, definition: BrandDefinition) -> bool:
    host = (urlsplit(url).hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
    return any(host == domain or host.endswith("." + domain) for domain in definition.domains)


def _normalized_spans(text: str) -> tuple[str, list[tuple[int, int]]]:
    pieces = []
    spans = []
    start = 0
    while start < len(text):
        end = start + 1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1
        normalized = _normalize(text[start:end])
        pieces.append(normalized)
        spans.extend([(start, end)] * len(normalized))
        start = end
    return "".join(pieces), spans


def brand_matches(fields: dict[str, str], definition: BrandDefinition | None,
                  *, brand_owned: bool = False) -> dict:
    matches = []
    if definition:
        aliases = (BrandAlias(text=definition.name), *definition.aliases)
        for field, text in fields.items():
            normalized, spans = _normalized_spans(text)
            for alias in aliases:
                pattern = r"(?<!\w)" + re.escape(_normalize(alias.text)) + r"(?!\w)"
                for match in re.finditer(pattern, normalized):
                    start, end = spans[match.start()][0], spans[match.end() - 1][1]
                    matches.append({"field": field, "alias": alias.text, "ambiguous": alias.ambiguous,
                                    "start": start, "end": end, "quote": text[start:end]})
    corroborated = brand_owned or any(not match["ambiguous"] for match in matches)
    return {"status": "unconfigured" if definition is None else
            "matched" if matches and corroborated else "ambiguous" if matches else "absent",
            "matches": matches}


class EvidenceAssessment(Contract):
    schema_version: Literal["geo-evidence-assessment/v1"] = "geo-evidence-assessment/v1"
    method_version: Literal["brand-citation-audit/v1"] = "brand-citation-audit/v1"
    run_id: str
    run_revision: int = Field(ge=1)
    input_hash: str
    measurement_hash: str
    definition_version: int | None
    definition_hash: str | None
    definition: BrandDefinition | None
    grounding: dict
    answer: dict
    queries: tuple[dict, ...]
    answers: tuple[dict, ...]
    common_completed_query_ids: tuple[str, ...]
    comparable_by_profile: dict
    limitations: tuple[str, ...] = (
        "Brand matches are literal matches under the configured ambiguity rules, not sentiment or endorsement.",
        "Grounding findings cover retained titles and passages, not full pages or the entire search index.",
        "Model-reported citations do not prove claim support. Uncited evidence may still influence an answer.",
        "Shared wording is not proof of source use. Claim-level citation alignment and selection reasons were not recorded.",
        "Citing more sources is not inherently better. Failed or missing outcomes are unknown, not zero use.",
    )

    @property
    def assessment_hash(self) -> str:
        return digest(self.model_dump(mode="json"))


def _rate(numerator: int, denominator: int, intended: int | None = None) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None,
            "intended": intended,
            "coverage": denominator / intended if intended else None}


def _text_has_brand_host(text: str, definition: BrandDefinition | None) -> bool:
    if definition is None:
        return False
    for url in re.findall(r"https?://[^\s<>]+", text):
        try:
            if owns_host(url.rstrip(".,;:!?)]}\"'"), definition):
                return True
        except (ValueError, UnicodeError):
            continue
    return False


def _shared_wording(excerpt: str, answer: str) -> list[dict]:
    excerpt_tokens = list(re.finditer(r"\w+", excerpt))
    answer_tokens = list(re.finditer(r"\w+", answer))
    matcher = SequenceMatcher(None, [_normalize(token.group()) for token in excerpt_tokens],
                              [_normalize(token.group()) for token in answer_tokens], autojunk=False)
    findings = []
    for block in matcher.get_matching_blocks():
        if block.size < 6:
            continue
        source_start = excerpt_tokens[block.a].start()
        source_end = excerpt_tokens[block.a + block.size - 1].end()
        answer_start = answer_tokens[block.b].start()
        answer_end = answer_tokens[block.b + block.size - 1].end()
        findings.append({"excerpt_start": source_start, "excerpt_end": source_end,
                         "answer_start": answer_start, "answer_end": answer_end,
                         "excerpt_quote": excerpt[source_start:source_end],
                         "answer_quote": answer[answer_start:answer_end], "non_unique": False})
    return findings


def _grounding_summary(queries: list[dict], configured: bool) -> dict:
    completed = [query for query in queries if query["status"] == "completed"]
    sources = [source for query in completed for source in query["sources"]]
    return {"coverage": _rate(len(completed), len(queries)),
            "brand_presence": _rate(sum(query["brand_status"] == "matched" for query in completed),
                                    len(completed) if configured else 0, len(queries)),
            "ambiguous_packets": sum(query["brand_status"] == "ambiguous" for query in completed),
            "brand_sources": sum(source["brand"]["status"] == "matched" for source in sources),
            "source_count": len(sources)}


def _answer_summary(answers: list[dict], configured: bool) -> dict:
    completed = [answer for answer in answers if answer["status"] == "completed"]
    brand_supplied = [answer for answer in completed if answer["brand_source_count"]]
    return {"coverage": _rate(len(completed), len(answers)),
            "brand_presence": _rate(sum(answer["brand"]["status"] == "matched" for answer in completed),
                                    len(completed) if configured else 0, len(answers)),
            "ambiguous_answers": sum(answer["brand"]["status"] == "ambiguous" for answer in completed),
            "source_citation_rate": _rate(sum(len(answer["valid_citation_ids"]) for answer in completed),
                                          sum(len(answer["sources"]) for answer in completed)),
            "brand_source_conversion": _rate(sum(answer["brand_cited_count"] > 0 for answer in brand_supplied),
                                             len(brand_supplied)),
            "brand_sources_cited": _rate(sum(answer["brand_cited_count"] for answer in completed),
                                         sum(answer["brand_source_count"] for answer in completed)),
            "unsupported_citation_count": sum(len(answer["unsupported_citation_ids"]) for answer in completed)}


def build_evidence_assessment(measurement: MeasurementResults, record: BrandDefinitionRecord | None = None,
                              *, run_id: str, run_revision: int) -> EvidenceAssessment:
    measurement = MeasurementResults.model_validate(measurement.model_dump(mode="json"))
    if record and record.run_id != run_id:
        raise ValueError("Brand definition belongs to another run")
    definition = record.definition if record else None
    retrievals = {item.query_id: item for item in measurement.retrievals}
    results = {(item.query_id, item.profile_id): item for item in measurement.results}
    target = str(measurement.inputs.snapshot.url)
    queries = []
    answers = []
    for pair in measurement.inputs.query_plan.queries:
        retrieval = retrievals.get(pair.query_id)
        sources = []
        for source in retrieval.sources if retrieval else ():
            url = str(source.url)
            owned = owns_host(url, definition) if definition else False
            brand = brand_matches({"title": source.title or "", "excerpt": source.excerpt}, definition,
                                  brand_owned=owned)
            relation = ("exact-page" if comparable_url(source.url) == comparable_url(measurement.inputs.snapshot.url)
                        else "same-domain-other-page" if urlsplit(url).hostname == urlsplit(target).hostname
                        else "other-page")
            sources.append({"query_id": pair.query_id, "evidence_id": source.evidence_id,
                            "url": url, "title": source.title, "excerpt": source.excerpt,
                            "returned_position": source.returned_position, "provenance": source.provenance.value,
                            "brand_owned_host": owned, "target_relation": relation, "brand": brand})
        status = retrieval.status if retrieval else "missing"
        brand_status = ("unconfigured" if not definition else "unknown" if status != "completed" else
                        "matched" if any(source["brand"]["status"] == "matched" for source in sources) else
                        "ambiguous" if any(source["brand"]["status"] == "ambiguous" for source in sources) else "absent")
        queries.append({"query_id": pair.query_id, "branded_query": pair.branded,
                        "grounding_query": pair.grounding_query, "chat_query": pair.chat_query,
                        "status": status, "brand_status": brand_status, "sources": sources})
        brand_ids = {source["evidence_id"] for source in sources if source["brand"]["status"] == "matched"}
        for profile in measurement.inputs.profiles:
            result = results.get((pair.query_id, profile.profile_id))
            completed = result is not None and result.status == "completed"
            answer_text = result.answer if result else ""
            citations = match_citations(result, measurement.inputs.snapshot.url) if completed and result else ()
            valid = [citation.evidence_id for citation in citations if citation.kind != "unsupported"]
            unsupported = [citation.evidence_id for citation in citations if citation.kind == "unsupported"]
            trace = [{"evidence_id": source["evidence_id"], "query_id": pair.query_id,
                      "cited": source["evidence_id"] in valid if completed else None,
                      "shared_wording": _shared_wording(source["excerpt"], answer_text) if completed else [],
                      "selection_reason": "Selection reason not recorded"} for source in sources]
            for source in trace:
                for overlap in source["shared_wording"]:
                    overlap["non_unique"] = any(
                        other["evidence_id"] != source["evidence_id"] and any(
                            len(re.findall(r"\w+", answer_text[max(item["answer_start"], overlap["answer_start"]):
                                                               min(item["answer_end"], overlap["answer_end"])])) >= 6
                            for item in other["shared_wording"]) for other in trace)
            brand = brand_matches({"answer": answer_text}, definition,
                                  brand_owned=_text_has_brand_host(answer_text, definition))
            if not completed and definition:
                brand = {"status": "unknown", "matches": []}
            answers.append({"query_id": pair.query_id, "profile_id": profile.profile_id,
                            "branded_query": pair.branded, "status": result.status if result else "missing",
                            "provenance": result.provenance.value if result else None,
                            "answer": answer_text, "brand": brand, "valid_citation_ids": valid,
                            "unsupported_citation_ids": unsupported,
                            "citation_status": "unknown" if not completed else "not-assessable" if not sources else
                            "all" if len(valid) == len(sources) else "some" if valid else "none",
                            "source_citation_rate": _rate(len(valid), len(sources) if completed else 0),
                            "brand_source_count": len(brand_ids), "brand_cited_count": len(brand_ids.intersection(valid)),
                            "exact_page_cited": any(citation.kind == "exact-page" for citation in citations) if completed else None,
                            "sources": trace})
    scores = measurement_scores(measurement)
    common = scores["common_completed_query_ids"]
    grounding = _grounding_summary(queries, definition is not None)
    answer = _answer_summary(answers, definition is not None)
    grounding["by_query_type"] = {name: _grounding_summary([query for query in queries if query["branded_query"] == branded],
                                                         definition is not None)
                                   for name, branded in (("branded", True), ("unbranded", False))}
    answer["by_query_type"] = {name: _answer_summary([item for item in answers if item["branded_query"] == branded],
                                                    definition is not None)
                              for name, branded in (("branded", True), ("unbranded", False))}
    answer["by_profile"] = {profile.profile_id: _answer_summary(
        [item for item in answers if item["profile_id"] == profile.profile_id], definition is not None)
        for profile in measurement.inputs.profiles}
    return EvidenceAssessment(
        run_id=run_id, run_revision=run_revision, input_hash=measurement.inputs.approval_hash,
        measurement_hash=digest(measurement.model_dump(mode="json")), definition=definition,
        definition_version=record.definition_version if record else None,
        definition_hash=record.definition_hash if record else None,
        grounding=grounding, answer=answer, queries=tuple(queries), answers=tuple(answers),
        common_completed_query_ids=tuple(common), comparable_by_profile={profile.profile_id: _answer_summary(
            [item for item in answers if item["profile_id"] == profile.profile_id and item["query_id"] in common],
            definition is not None) for profile in measurement.inputs.profiles})