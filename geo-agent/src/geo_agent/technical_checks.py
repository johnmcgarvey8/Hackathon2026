from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from geo_agent.contracts import Brief, Contract, PageSnapshot, digest
from geo_agent.evaluation import comparable_url


CHECKS_VERSION = "geo-technical-checks/v1"


class TechnicalField(Contract):
    state: Literal["present", "not-found", "not-provided"]
    value: str | int | float | bool | tuple[str, ...] | None = None


class TechnicalSignals(Contract):
    schema_version: Literal["geo-technical-signals/v1"] = "geo-technical-signals/v1"
    status: Literal["captured", "not-captured"] = "not-captured"
    reason: str | None = Field(default=None, max_length=200)
    crawled_at: str | None = Field(default=None, max_length=100)
    browse_trace_id: str | None = Field(default=None, max_length=500)
    jsonld_types: TechnicalField = TechnicalField(state="not-provided")
    meta_title: TechnicalField = TechnicalField(state="not-provided")
    meta_description: TechnicalField = TechnicalField(state="not-provided")
    canonical: TechnicalField = TechnicalField(state="not-provided")
    robots: TechnicalField = TechnicalField(state="not-provided")
    hreflang: TechnicalField = TechnicalField(state="not-provided")
    open_graph: TechnicalField = TechnicalField(state="not-provided")
    h1: TechnicalField = TechnicalField(state="not-provided")
    h2: TechnicalField = TechnicalField(state="not-provided")
    image_alt_coverage: TechnicalField = TechnicalField(state="not-provided")
    internal_link_count: TechnicalField = TechnicalField(state="not-provided")

    @property
    def signal_hash(self) -> str:
        return digest(self.model_dump(mode="json"))


class TechnicalFinding(Contract):
    check_id: str = Field(pattern=r"^T-(?:0[1-9]|1[01])$")
    field: str = Field(min_length=1, max_length=100)
    state: Literal["present", "not-found"]
    observed_value: str = Field(max_length=200)
    severity: Literal["high", "medium", "low", "info"]
    evidence_id: str = Field(pattern=r"^tech\.[a-z0-9_]+$")


class TechnicalFindings(Contract):
    schema_version: Literal["geo-technical-findings/v1"] = "geo-technical-findings/v1"
    checks_version: Literal["geo-technical-checks/v1"] = CHECKS_VERSION
    findings: tuple[TechnicalFinding, ...] = ()


TECHNICAL_THRESHOLDS = {
    "meta_title_min": 20,
    "meta_title_max": 60,
    "meta_description_min": 70,
    "meta_description_max": 160,
    "image_alt_coverage_min": 0.8,
    "internal_link_count_min": 3,
    "freshness_days": 365,
}


def _text(value: object) -> str:
    if isinstance(value, tuple):
        rendered = ", ".join(str(item) for item in value)
    elif value is None:
        rendered = ""
    else:
        rendered = str(value)
    return rendered[:200]


def _finding(
    check_id: str,
    field: str,
    signal: TechnicalField,
    severity: Literal["high", "medium", "low", "info"],
) -> TechnicalFinding:
    return TechnicalFinding(
        check_id=check_id,
        field=field,
        state=signal.state,
        observed_value=_text(signal.value),
        severity=severity,
        evidence_id=f"tech.{field}",
    )


def run_technical_checks(
    signals: TechnicalSignals,
    brief: Brief,
    snapshot: PageSnapshot,
) -> TechnicalFindings:
    if signals.status != "captured":
        return TechnicalFindings()

    findings: list[TechnicalFinding] = []

    robots = signals.robots
    if robots.state == "present" and any(
        token in _text(robots.value).casefold() for token in ("noindex", "nofollow")
    ):
        findings.append(_finding("T-01", "robots", robots, "high"))

    canonical = signals.canonical
    if canonical.state == "not-found":
        findings.append(_finding("T-02", "canonical", canonical, "medium"))
    elif canonical.state == "present":
        try:
            differs = comparable_url(str(canonical.value)) != comparable_url(str(brief.url))
        except (TypeError, ValueError):
            differs = True
        if differs:
            findings.append(_finding("T-02", "canonical", canonical, "high"))

    title = signals.meta_title
    if title.state == "not-found":
        findings.append(_finding("T-03", "meta_title", title, "medium"))
    elif title.state == "present":
        length = len(_text(title.value))
        if not TECHNICAL_THRESHOLDS["meta_title_min"] <= length <= TECHNICAL_THRESHOLDS["meta_title_max"]:
            findings.append(_finding("T-03", "meta_title", title, "low"))

    description = signals.meta_description
    if description.state == "not-found":
        findings.append(_finding("T-04", "meta_description", description, "medium"))
    elif description.state == "present":
        length = len(_text(description.value))
        if not (
            TECHNICAL_THRESHOLDS["meta_description_min"]
            <= length
            <= TECHNICAL_THRESHOLDS["meta_description_max"]
        ):
            findings.append(_finding("T-04", "meta_description", description, "low"))

    h1 = signals.h1
    if h1.state in {"present", "not-found"}:
        count = int(h1.value or 0)
        if count == 0:
            findings.append(_finding("T-05", "h1", h1, "medium"))
        elif count > 1:
            findings.append(_finding("T-05", "h1", h1, "low"))

    h2 = signals.h2
    if h2.state in {"present", "not-found"} and int(h2.value or 0) == 0:
        findings.append(_finding("T-06", "h2", h2, "low"))

    jsonld = signals.jsonld_types
    if jsonld.state == "not-found":
        findings.append(_finding("T-07", "jsonld_types", jsonld, "medium"))
    elif jsonld.state == "present":
        findings.append(_finding("T-07", "jsonld_types", jsonld, "info"))

    alt = signals.image_alt_coverage
    if alt.state == "present" and float(alt.value or 0) < TECHNICAL_THRESHOLDS["image_alt_coverage_min"]:
        findings.append(_finding("T-08", "image_alt_coverage", alt, "low"))

    open_graph = signals.open_graph
    if open_graph.state == "not-found":
        findings.append(_finding("T-09", "open_graph", open_graph, "low"))

    if snapshot.last_updated_at:
        try:
            updated = datetime.fromisoformat(snapshot.last_updated_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).days
            if age > TECHNICAL_THRESHOLDS["freshness_days"]:
                findings.append(TechnicalFinding(
                    check_id="T-10",
                    field="last_updated_at",
                    state="present",
                    observed_value=snapshot.last_updated_at[:200],
                    severity="low",
                    evidence_id="tech.last_updated_at",
                ))
        except ValueError:
            pass

    internal_links = signals.internal_link_count
    if (
        internal_links.state == "present"
        and int(internal_links.value or 0) < TECHNICAL_THRESHOLDS["internal_link_count_min"]
    ):
        findings.append(_finding("T-11", "internal_link_count", internal_links, "low"))

    return TechnicalFindings(findings=tuple(findings))
