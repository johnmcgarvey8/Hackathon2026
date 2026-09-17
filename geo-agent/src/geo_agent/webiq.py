import ipaddress
import json
import socket
from collections.abc import Callable
from enum import StrEnum
from urllib.parse import urlsplit

import httpx
from pydantic import HttpUrl, TypeAdapter, ValidationError

from geo_agent.contracts import Brief, PageSnapshot, Provenance, Query, Source


BROWSE_ENDPOINT = "https://api.microsoft.ai/v3/browse"
SEARCH_ENDPOINT = "https://api.microsoft.ai/v3/search/web"


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
    def __init__(self, message: str, *, code: ProviderFailure = ProviderFailure.UNKNOWN):
        super().__init__(message)
        self.code = ProviderFailure(code)


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
        raise ProviderError("URL policy rejected the page or its resolved addresses") from None


class WebIQ:
    def __init__(
        self, api_key: str, *, browse_endpoint: str = BROWSE_ENDPOINT,
        search_endpoint: str = SEARCH_ENDPOINT,
        transport: httpx.BaseTransport | None = None,
        url_validator: Callable[[str], str] = public_url,
    ):
        if not api_key.strip():
            raise ProviderError("Web IQ API key is missing")
        if browse_endpoint != BROWSE_ENDPOINT or search_endpoint != SEARCH_ENDPOINT:
            raise ProviderError("Web IQ endpoints must match the documented HTTPS endpoints")
        self._api_key = api_key
        self._transport = transport
        self._validate_url = url_validator

    def _post(self, endpoint: str, body: dict) -> dict:
        try:
            with httpx.Client(transport=self._transport, timeout=30, follow_redirects=False, trust_env=False) as client:
                with client.stream("POST", endpoint, json=body, headers={"x-apikey": self._api_key}) as response:
                    if response.status_code != 200:
                        raise ProviderError(f"Web IQ returned HTTP {response.status_code}; no automatic retry")
                    if response.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
                        raise ProviderError("Web IQ returned an unsupported content type")
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > 250_000:
                            raise ProviderError("Web IQ response exceeds the size limit")
            payload = json.loads(chunks)
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError):
            raise ProviderError("Web IQ transport or JSON response failure") from None
        except ValueError as error:
            if isinstance(error, ProviderError):
                raise
            raise ProviderError("Web IQ response is not an object") from None

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
            if public_url(payload["url"], resolve=False) != target:
                raise ProviderError("Browse returned a different page; redirect equivalence is unverified")
            if not isinstance(payload.get("content"), str) or not payload["content"].strip():
                raise ValueError
            if payload.get("isAdult") is True:
                raise ProviderError("Browse content was blocked by policy")
            return PageSnapshot(
                url=target, title=payload.get("title", ""), content=payload["content"][:10000],
                provenance=Provenance.LIVE, provider_trace_id=payload.get("traceId"),
                content_format="markdown", crawled_at=payload.get("crawledAt"),
                last_updated_at=payload.get("lastUpdatedAt"),
            )
        except (KeyError, TypeError, ValidationError, ValueError) as error:
            if isinstance(error, ProviderError):
                raise
            raise ProviderError("Browse response lacks usable page evidence") from None

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
                    raise ProviderError("Search content was blocked by policy")
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
            raise ProviderError("Search response lacks usable source evidence") from None