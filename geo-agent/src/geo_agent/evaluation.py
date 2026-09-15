import re
from urllib.parse import urlsplit

from pydantic import HttpUrl

from geo_agent.contracts import Contract, EvaluationResult, MeasurementResults


class CitationMatch(Contract):
    evidence_id: str
    kind: str


class Score(Contract):
    score: int | None
    numerator: int
    denominator: int
    intended: int
    errors: int
    missing: int
    coverage: float


def comparable_url(url: HttpUrl) -> str:
    return str(url).split("#", 1)[0]


def match_citations(result: EvaluationResult, target: HttpUrl) -> tuple[CitationMatch, ...]:
    if result.status != "completed":
        return ()
    sources = {source.evidence_id: source for source in result.sources}
    matches = []
    for evidence_id in dict.fromkeys(result.citation_ids):
        source = sources.get(evidence_id)
        if source is None:
            kind = "unsupported"
        elif comparable_url(source.url) == comparable_url(target):
            kind = "exact-page"
        elif urlsplit(str(source.url)).hostname == urlsplit(str(target)).hostname:
            kind = "same-domain-other-page"
        else:
            kind = "other-page"
        matches.append(CitationMatch(evidence_id=evidence_id, kind=kind))
    return tuple(matches)


def score_results(results: tuple[EvaluationResult, ...], target: HttpUrl, intended: int) -> Score:
    pairs = {(result.query_id, result.profile_id) for result in results}
    if len(pairs) != len(results) or len(results) > intended or intended < 0:
        raise ValueError("Invalid or duplicate score inputs")
    completed = tuple(result for result in results if result.status == "completed")
    numerator = sum(
        any(match.kind == "exact-page" for match in match_citations(result, target))
        for result in completed
    )
    denominator = len(completed)
    return Score(
        score=round(100 * numerator / denominator) if denominator else None,
        numerator=numerator,
        denominator=denominator,
        intended=intended,
        errors=len(results) - denominator,
        missing=intended - len(results),
        coverage=denominator / intended if intended else 0,
    )


def answer_mentions_target(answer: str, target: HttpUrl) -> bool:
    """Detect literal HTTP(S) URLs, not encoded URLs, aliases, or canonical pages.

    Strip prose punctuation and unmatched closing brackets from URL tokens;
    preserve queries, path case, and trailing slashes. Mentions are not citations.
    """
    if urlsplit(str(target)).username is not None:
        return False
    for token in re.findall(r'''(?<![\w:/?=&%+@.\-])https?://[^\s<>"'`]+''', answer, re.IGNORECASE):
        while token:
            trimmed = token.rstrip(".,;!:")
            if trimmed.endswith((")", "]", "}")):
                opening = {")": "(", "]": "[", "}": "{"}[trimmed[-1]]
                if trimmed.count(trimmed[-1]) > trimmed.count(opening):
                    trimmed = trimmed[:-1]
            if trimmed == token:
                break
            token = trimmed
        try:
            if urlsplit(token).username is not None:
                continue
            candidate = HttpUrl(token)
        except ValueError:
            continue
        if comparable_url(candidate) == comparable_url(target):
            return True
    return False


def measurement_scores(measurement: MeasurementResults) -> dict:
    """Score validated v2 evidence; raw mentions never contribute citation credit.

    Answer details cover every intended pair; citation state is null without a
    completed answer. Same-domain counts exclude exact-page citations. Only the
    common completed query set is comparable across the configured profiles.
    """
    measurement = MeasurementResults.model_validate(measurement.model_dump(mode="json"))
    queries = measurement.inputs.query_plan.queries
    profiles = measurement.inputs.profiles
    target = measurement.inputs.snapshot.url
    results = measurement.results

    def citation_score(outcomes: tuple[EvaluationResult, ...], intended: int) -> dict:
        score = score_results(outcomes, target, intended)
        return {**score.model_dump(mode="json"), "provisional": score.coverage < 1}

    by_profile = {
        profile.profile_id: tuple(result for result in results if result.profile_id == profile.profile_id)
        for profile in profiles
    }
    common_query_ids = [
        query.query_id for query in queries
        if all(any(result.query_id == query.query_id and result.status == "completed"
                   for result in by_profile[profile.profile_id]) for profile in profiles)
    ]
    outcomes = {(result.query_id, result.profile_id): result for result in results}
    by_answer = []
    for query in queries:
        for profile in profiles:
            outcome = outcomes.get((query.query_id, profile.profile_id))
            completed = outcome is not None and outcome.status == "completed"
            matches = match_citations(outcome, target) if outcome is not None else ()
            by_answer.append({
                "query_id": query.query_id,
                "profile_id": profile.profile_id,
                "status": outcome.status if outcome is not None else "missing",
                "exact_page_cited": any(match.kind == "exact-page" for match in matches) if completed else None,
                "same_domain_citation_count": sum(match.kind == "same-domain-other-page" for match in matches),
                "unsupported_citation_ids": [match.evidence_id for match in matches if match.kind == "unsupported"],
                "raw_url_mentioned": answer_mentions_target(outcome.answer, target) if completed else False,
            })

    packets = {packet.query_id: packet for packet in measurement.retrievals}
    retrieval_by_query = {}
    for query in queries:
        packet = packets.get(query.query_id)
        completed = packet is not None and packet.status == "completed"
        positions = [
            source.returned_position for source in packet.sources
            if source.returned_position is not None and comparable_url(source.url) == comparable_url(target)
        ] if completed else []
        retrieval_by_query[query.query_id] = {
            "status": packet.status if packet is not None else "missing",
            "target_returned": bool(positions) if completed else None,
            "returned_position": min(positions) if positions else None,
            "error": packet.error if packet is not None else None,
        }
    denominator = sum(packet.status == "completed" for packet in measurement.retrievals)
    numerator = sum(item["target_returned"] is True for item in retrieval_by_query.values())
    retrieval = {
        "score": round(100 * numerator / denominator) if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "intended": len(queries),
        "errors": len(measurement.retrievals) - denominator,
        "missing": len(queries) - len(measurement.retrievals),
        "coverage": denominator / len(queries),
        "provisional": denominator < len(queries),
        "failures": [{"query_id": packet.query_id, "error": packet.error}
                     for packet in measurement.retrievals if packet.status == "error"],
        "by_query": retrieval_by_query,
    }
    overall = citation_score(results, len(queries) * len(profiles))
    return {
        "method_version": measurement.inputs.method_version,
        "provisional": overall["provisional"],
        "overall": overall,
        "by_profile": {profile_id: citation_score(outcomes, len(queries))
                       for profile_id, outcomes in by_profile.items()},
        "by_query": {
            query.query_id: citation_score(tuple(result for result in results if result.query_id == query.query_id),
                                           len(profiles))
            for query in queries
        },
        "by_query_type": {
            label: citation_score(tuple(result for result in results
                                        if result.query_id in {query.query_id for query in queries
                                                               if query.branded == branded}),
                                  sum(query.branded == branded for query in queries) * len(profiles))
            for label, branded in (("branded", True), ("unbranded", False))
        },
        "common_completed_query_ids": common_query_ids,
        "comparable_by_profile": {
            profile_id: citation_score(tuple(result for result in outcomes if result.query_id in common_query_ids),
                                       len(common_query_ids))
            for profile_id, outcomes in by_profile.items()
        },
        "retrieval": retrieval,
        "by_answer": by_answer,
    }