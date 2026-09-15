import json
import socket

import httpx
import pytest

from geo_agent.contracts import Brief, Query
from geo_agent.webiq import BROWSE_ENDPOINT, SEARCH_ENDPOINT, ProviderError, WebIQ, public_url


def brief():
    return Brief(url="https://example.org/page", audience="Visitors", goal="Plan a visit", locale="en-GB")


def test_browse_contract_and_provenance():
    def handler(request):
        assert str(request.url) == BROWSE_ENDPOINT
        assert request.method == "POST"
        assert request.headers["x-apikey"] == "dummy-key"
        body = json.loads(request.content)
        assert body["liveCrawl"] == "none"
        assert (body["region"], body["language"], body["contentFormat"]) == ("GB", "en", "markdown")
        return httpx.Response(200, json={"url": str(brief().url), "content": "A public page", "title": "Page", "traceId": "trace-browse"})

    provider = WebIQ("dummy-key", transport=httpx.MockTransport(handler), url_validator=lambda value: value)
    snapshot = provider.browse(brief())
    assert snapshot.provenance == "live"
    assert snapshot.provider_trace_id == "trace-browse"


def test_search_uses_bounded_passages_and_trace():
    def handler(request):
        assert str(request.url) == SEARCH_ENDPOINT
        body = json.loads(request.content)
        assert body["contentFormat"] == "passage"
        assert (body["maxResults"], body["maxLength"], body["safeSearch"]) == (5, 1500, "strict")
        assert "target" not in body
        return httpx.Response(200, json={"traceId": "trace-search", "webResults": [{"url": "https://example.org/page", "title": "Page", "content": "Relevant passage"}]})

    sources = WebIQ("dummy-key", transport=httpx.MockTransport(handler)).search(Query(query_id="q-1", text="Where to visit?", intent="Plan"), "en-GB")
    assert sources[0].evidence_id == "q-1-source-1"
    assert sources[0].returned_position == 1
    assert sources[0].provider_trace_id == "trace-search"


@pytest.mark.parametrize("status", [202, 301, 401, 403, 404, 429, 430, 500])
def test_errors_are_not_retried_or_exposed(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://other.example"}, text="dummy-secret-body")
    provider = WebIQ("dummy-key", transport=httpx.MockTransport(handler), url_validator=lambda value: value)
    with pytest.raises(ProviderError, match=f"HTTP {status}") as caught:
        provider.browse(brief())
    assert len(calls) == 1
    assert "dummy-secret" not in str(caught.value)


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://169.254.169.254/latest", "http://[::1]", "http://10.0.0.1", "http://localhost", "https://user:password@example.org", "file:///etc/passwd", "https://example.org:8443", "http://2130706433", "https://example.org/#fragment"])
def test_private_and_malformed_urls_are_rejected(url):
    with pytest.raises(ProviderError):
        public_url(url)


def test_dns_private_and_mixed_answers_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("10.0.0.1", 443))])
    with pytest.raises(ProviderError):
        public_url("https://example.org")


def test_unicode_public_host_is_normalised(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))])
    assert "xn--" in public_url("https://b\u00fccher.example/page")


def test_endpoint_override_cannot_leak_key():
    with pytest.raises(ProviderError, match="documented"):
        WebIQ("dummy-key", browse_endpoint="https://other.example/browse")


@pytest.mark.parametrize("payload", [{}, {"url": "https://example.org/page", "content": ""}, {"url": "https://example.org/different", "content": "Page"}])
def test_bad_browse_payload_fails_closed(payload):
    provider = WebIQ("dummy-key", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)), url_validator=lambda value: value)
    with pytest.raises(ProviderError):
        provider.browse(brief())