import ipaddress
import json
import socket
from collections.abc import Callable
from enum import StrEnum
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx
from pydantic import HttpUrl, TypeAdapter, ValidationError

from geo_agent.contracts import Brief, PageSnapshot, Provenance, Query, Source
from geo_agent.technical_checks import TechnicalField, TechnicalSignals


BROWSE_ENDPOINT = "https://api.microsoft.ai/v3/browse"
SEARCH_ENDPOINT = "https://api.microsoft.ai/v3/search/web"
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "msockid",
}


class ProviderFailure(StrEnum):
    UNKNOWN = "ProviderError"
    AUTH = "provider-authentication-failed"
    RATE_LIMIT = "provider-rate-limited"
    REQUEST = "provider-request-failed"
    CONNECTION = "provider-connection-failed"
    OUTPUT_LIMIT = "model-output-limit"
    INCOMPLETE = "model-response-incomplete"
    BLOCKED = "model-content-blocked"
    REFUSED = "model-response-refused"
    SCHEMA = "model-output-invalid"
    PLAN_ORDER = "query-plan-order-invalid"
    PLAN_EVIDENCE = "query-plan-evidence-invalid"


class ProviderError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: ProviderFailure = ProviderFailure.UNKNOWN,
        safe_detail: str | None = None,
    ):
        super().__init__(message)
        self.code = ProviderFailure(code)
        self.safe_detail = safe_detail


def _provider_error(
    message: str,
    code: ProviderFailure = ProviderFailure.UNKNOWN,
) -> ProviderError:
    return ProviderError(message, code=code, safe_detail=message)


def public_url(value: str, resolve: bool = True) -> str:
    try:
        parsed = urlsplit(value)
        url = TypeAdapter(HttpUrl).validate_python(value)
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError
        if url.port not in {80, 443} or not url.host or "%" in url.host:
            raise ValueError
        host = url.host.strip("[]")
        if host == "localhost" or "." not in host and ":" not in host:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
        if resolve:
            addresses = socket.getaddrinfo(host, url.port, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
                raise ValueError
        return str(url)
    except (ValueError, OSError):
        raise _provider_error(
            "URL policy rejected the page or its resolved addresses",
            ProviderFailure.REQUEST,
        ) from None


def _same_browse_page(requested: str, returned: str) -> bool:
    requested_url = urlsplit(requested)
    returned_url = urlsplit(returned)
    requested_host = requested_url.hostname or ""
    returned_host = returned_url.hostname or ""
    hosts_match = requested_host == returned_host or (
        requested_host.removeprefix("www.") == returned_host.removeprefix("www.")
        and (requested_host.startswith("www.") or returned_host.startswith("www."))
    )
    requested_query = tuple(sorted(
        (key, value)
        for key, value in parse_qsl(requested_url.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_QUERY_KEYS
        and not key.casefold().startswith("utm_")
    ))
    returned_query = tuple(sorted(
        (key, value)
        for key, value in parse_qsl(returned_url.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_QUERY_KEYS
        and not key.casefold().startswith("utm_")
    ))
    return (
        hosts_match
        and requested_url.scheme == returned_url.scheme
        and requested_url.port == returned_url.port
        and requested_url.path == returned_url.path
        and requested_query == returned_query
    )


class WebIQ:
    def __init__(
        self, api_key: str, *, browse_endpoint: str = BROWSE_ENDPOINT,
        search_endpoint: str = SEARCH_ENDPOINT,
        transport: httpx.BaseTransport | None = None,
        url_validator: Callable[[str], str] = public_url,
    ):
        if not api_key.strip():
            raise _provider_error("Web IQ API key is missing", ProviderFailure.AUTH)
        if browse_endpoint != BROWSE_ENDPOINT or search_endpoint != SEARCH_ENDPOINT:
            raise _provider_error(
                "Web IQ endpoints must match the documented HTTPS endpoints",
                ProviderFailure.REQUEST,
            )
        self._api_key = api_key
        self._transport = transport
        self._validate_url = url_validator

    def _post(self, endpoint: str, body: dict, *, max_response_bytes: int = 250_000) -> dict:
        try:
            with httpx.Client(transport=self._transport, timeout=30, follow_redirects=False, trust_env=False) as client:
                with client.stream("POST", endpoint, json=body, headers={"x-apikey": self._api_key}) as response:
                    if response.status_code != 200:
                        message = f"Web IQ returned HTTP {response.status_code}; no automatic retry"
                        code = (
                            ProviderFailure.AUTH
                            if response.status_code in {401, 403}
                            else ProviderFailure.RATE_LIMIT
                            if response.status_code == 429
                            else ProviderFailure.REQUEST
                        )
                        raise _provider_error(message, code)
                    if response.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
                        raise _provider_error(
                            "Web IQ returned an unsupported content type",
                            ProviderFailure.REQUEST,
                        )
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > max_response_bytes:
                            raise _provider_error(
                                "Web IQ response exceeds the size limit",
                                ProviderFailure.REQUEST,
                            )
            payload = json.loads(chunks)
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except httpx.HTTPError:
            raise _provider_error(
                "Web IQ transport failure",
                ProviderFailure.CONNECTION,
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise _provider_error(
                "Web IQ JSON response failure",
                ProviderFailure.REQUEST,
            ) from None
        except ValueError as error:
            if isinstance(error, ProviderError):
                raise
            raise _provider_error(
                "Web IQ response is not an object",
                ProviderFailure.REQUEST,
            ) from None

    def browse_markup(self, brief: Brief) -> TechnicalSignals:
        target = self._validate_url(str(brief.url))
        language, region = brief.locale.split("-")
        payload = self._post(BROWSE_ENDPOINT, {
            "url": target,
            "contentFormat": "html",
            "maxLength": 200000,
            "language": language,
            "region": region,
            "liveCrawl": "none",
            "includeWebLinks": False,
            "includeImageLinks": False,
            "renderDynamicPages": False,
        }, max_response_bytes=600_000)
        try:
            returned_url = public_url(payload["url"], resolve=False)
            if not _same_browse_page(target, returned_url):
                raise _provider_error(
                    "Browse returned a different page; redirect equivalence is unverified",
                    ProviderFailure.REQUEST,
                )
            markup = payload["content"]
            if not isinstance(markup, str) or not markup.strip():
                raise ValueError
            if payload.get("isAdult") is True:
                raise _provider_error(
                    "Browse content was blocked by policy",
                    ProviderFailure.BLOCKED,
                )
            parser = _TechnicalMarkupParser(target)
            parser.feed(markup)
            parser.close()
            return parser.signals(
                crawled_at=payload.get("crawledAt"),
                trace_id=payload.get("traceId"),
                truncated=len(markup) >= 200000,
            )
        except (KeyError, TypeError, ValidationError, ValueError) as error:
            if isinstance(error, ProviderError):
                raise
            raise _provider_error(
                "Browse response lacks usable indexed HTML",
                ProviderFailure.REQUEST,
            ) from None

    def browse(self, brief: Brief) -> PageSnapshot:
        target = self._validate_url(str(brief.url))
        language, region = brief.locale.split("-")
        payload = self._post(BROWSE_ENDPOINT, {
            "url": target, "contentFormat": "markdown", "maxLength": 10000,
            "language": language, "region": region, "liveCrawl": "none",
            "includeWebLinks": False, "includeImageLinks": False,
            "renderDynamicPages": False,
        })
        try:
            returned_url = public_url(payload["url"], resolve=False)
            if not _same_browse_page(target, returned_url):
                raise _provider_error(
                    "Browse returned a different page; redirect equivalence is unverified",
                    ProviderFailure.REQUEST,
                )
            if not isinstance(payload.get("content"), str) or not payload["content"].strip():
                raise ValueError
            if payload.get("isAdult") is True:
                raise _provider_error(
                    "Browse content was blocked by policy",
                    ProviderFailure.BLOCKED,
                )
            return PageSnapshot(
                url=target, title=payload.get("title", ""), content=payload["content"][:10000],
                provenance=Provenance.LIVE, provider_trace_id=payload.get("traceId"),
                content_format="markdown", crawled_at=payload.get("crawledAt"),
                last_updated_at=payload.get("lastUpdatedAt"),
            )
        except (KeyError, TypeError, ValidationError, ValueError) as error:
            if isinstance(error, ProviderError):
                raise
            raise _provider_error(
                "Browse response lacks usable page evidence",
                ProviderFailure.REQUEST,
            ) from None

    def search(self, query: Query, locale: str) -> tuple[Source, ...]:
        language, region = locale.split("-")
        payload = self._post(SEARCH_ENDPOINT, {
            "query": query.text, "contentFormat": "passage", "maxResults": 5,
            "maxLength": 1500, "language": language, "region": region, "safeSearch": "strict",
        })
        try:
            results = payload["webResults"]
            if not isinstance(results, list) or len(results) > 5:
                raise ValueError
            sources = []
            for position, item in enumerate(results, start=1):
                if item.get("isAdult") is True:
                    raise _provider_error(
                        "Search content was blocked by policy",
                        ProviderFailure.BLOCKED,
                    )
                url = public_url(item["url"], resolve=False)
                sources.append(Source(
                    evidence_id=f"{query.query_id}-source-{position}", url=url,
                    title=item.get("title", ""), excerpt=item["content"][:1500],
                    provenance=Provenance.LIVE, returned_position=position,
                    provider_trace_id=payload.get("traceId"),
                    crawled_at=item.get("crawledAt"), last_updated_at=item.get("lastUpdatedAt"),
                ))
            return tuple(sources)
        except (KeyError, TypeError, AttributeError, ValidationError, ValueError) as error:
            if isinstance(error, ProviderError):
                raise
            raise _provider_error(
                "Search response lacks usable source evidence",
                ProviderFailure.REQUEST,
            ) from None


class _TechnicalMarkupParser(HTMLParser):
    def __init__(self, target_url: str):
        super().__init__(convert_charrefs=True)
        self.target_url = target_url
        self.target_host = urlsplit(target_url).hostname
        self.title_parts: list[str] = []
        self.in_title = False
        self.in_jsonld = False
        self.jsonld_parts: list[str] = []
        self.jsonld_types: set[str] = set()
        self.meta_description: str | None = None
        self.robots: str | None = None
        self.canonical: str | None = None
        self.hreflang: list[str] = []
        self.open_graph = False
        self.h1_count = 0
        self.h2_count = 0
        self.image_count = 0
        self.image_alt_count = 0
        self.internal_links: set[str] = set()
        self.saw_markup = False
        self.html_seen = False
        self.html_closed = False
        self.head_seen = False
        self.head_closed = False
        self.body_seen = False
        self.body_closed = False
        self.jsonld_seen = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value for key, value in attrs if value is not None}
        tag = tag.casefold()
        self.saw_markup = True
        if tag == "html":
            self.html_seen = True
        elif tag == "head":
            self.head_seen = True
        elif tag == "body":
            self.body_seen = True
        elif tag == "title":
            self.in_title = True
        elif tag == "script" and values.get("type", "").casefold() == "application/ld+json":
            self.jsonld_seen = True
            self.in_jsonld = True
            self.jsonld_parts = []
        elif tag == "meta":
            name = values.get("name", "").casefold()
            property_name = values.get("property", "").casefold()
            content = values.get("content", "")
            if name == "description":
                self.meta_description = content
            elif name == "robots":
                self.robots = content
            if property_name.startswith("og:"):
                self.open_graph = True
        elif tag == "link":
            rel = {item.casefold() for item in values.get("rel", "").split()}
            if "canonical" in rel:
                href = values.get("href")
                self.canonical = urljoin(self.target_url, href) if href else None
            if "alternate" in rel and values.get("hreflang"):
                self.hreflang.append(values["hreflang"])
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "h2":
            self.h2_count += 1
        elif tag == "img":
            self.image_count += 1
            if values.get("alt", "").strip():
                self.image_alt_count += 1
        elif tag == "a" and values.get("href"):
            try:
                href = urlsplit(urljoin(self.target_url, values["href"]))
                if (
                    not values["href"].startswith("#")
                    and href.scheme in {"http", "https"}
                    and href.hostname == self.target_host
                ):
                    self.internal_links.add(href._replace(fragment="").geturl())
            except ValueError:
                pass

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "html" and self.html_seen:
            self.html_closed = True
        elif tag == "head" and self.head_seen:
            self.head_closed = True
        elif tag == "body" and self.body_seen:
            self.body_closed = True
        elif tag == "title":
            self.in_title = False
        elif tag == "script" and self.in_jsonld:
            self.in_jsonld = False
            try:
                payload = json.loads("".join(self.jsonld_parts))
                self._collect_jsonld_types(payload)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.in_jsonld:
            self.jsonld_parts.append(data)

    def _collect_jsonld_types(self, value: object) -> None:
        if isinstance(value, dict):
            schema_type = value.get("@type")
            if isinstance(schema_type, str):
                self.jsonld_types.add(schema_type)
            elif isinstance(schema_type, list):
                self.jsonld_types.update(
                    item for item in schema_type if isinstance(item, str)
                )
            for nested in value.values():
                self._collect_jsonld_types(nested)
        elif isinstance(value, list):
            for nested in value:
                self._collect_jsonld_types(nested)

    @staticmethod
    def _field(value: object, present: bool, *, complete: bool = False) -> TechnicalField:
        return TechnicalField(
            state="present" if present else "not-found" if complete else "not-provided",
            value=value if present else None,
        )

    def signals(
        self, *, crawled_at: str | None, trace_id: str | None, truncated: bool = False,
    ) -> TechnicalSignals:
        if not self.saw_markup:
            return TechnicalSignals(
                reason="html-not-provided", crawled_at=crawled_at, browse_trace_id=trace_id,
            )
        head_complete = self.head_seen and self.head_closed
        body_complete = self.body_seen and self.body_closed and not truncated
        document_complete = (
            self.html_seen and self.html_closed and head_complete and body_complete
        )
        title = "".join(self.title_parts).strip()
        alt_coverage = self.image_alt_count / self.image_count if self.image_count else 1.0
        # Missing fields in a stripped or truncated response are unknown, not page defects.
        return TechnicalSignals(
            status="captured",
            reason=None if document_complete else "partial-html",
            crawled_at=crawled_at,
            browse_trace_id=trace_id,
            jsonld_types=self._field(
                tuple(sorted(self.jsonld_types)), bool(self.jsonld_types),
                complete=document_complete and not self.jsonld_seen,
            ),
            meta_title=self._field(title, bool(title), complete=head_complete),
            meta_description=self._field(
                self.meta_description,
                self.meta_description is not None,
                complete=head_complete,
            ),
            canonical=self._field(
                self.canonical, self.canonical is not None, complete=head_complete,
            ),
            robots=self._field(self.robots, self.robots is not None, complete=head_complete),
            hreflang=self._field(
                tuple(sorted(set(self.hreflang))), bool(self.hreflang),
                complete=head_complete,
            ),
            open_graph=self._field(self.open_graph, self.open_graph, complete=head_complete),
            h1=self._field(self.h1_count, body_complete),
            h2=self._field(self.h2_count, body_complete),
            image_alt_coverage=self._field(
                round(alt_coverage, 4), body_complete and self.image_count > 0,
            ),
            internal_link_count=self._field(
                len(self.internal_links), body_complete,
            ),
        )
