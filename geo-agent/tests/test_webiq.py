import json
import socket

import httpx
import pytest

from geo_agent.contracts import Brief, PageSnapshot, Query
from geo_agent.technical_checks import run_technical_checks
from geo_agent.webiq import (
    BROWSE_ENDPOINT,
    SEARCH_ENDPOINT,
    ProviderError,
    ProviderFailure,
    WebIQ,
    public_url,
)


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


def markup_provider(markup):
    def handler(request):
        assert str(request.url) == BROWSE_ENDPOINT
        body = json.loads(request.content)
        assert body["contentFormat"] == "html"
        assert body["maxLength"] == 200000
        assert body["liveCrawl"] == "none"
        assert body["renderDynamicPages"] is False
        assert request.headers["x-apikey"] == "dummy-key"
        return httpx.Response(200, json={
            "url": str(brief().url), "content": markup,
            "crawledAt": "2026-10-16T10:00:00Z", "traceId": "html-trace",
        })

    return WebIQ(
        "dummy-key", transport=httpx.MockTransport(handler),
        url_validator=lambda value: value,
    )


def test_indexed_html_extracts_metadata_schema_and_body_signals():
    signals = markup_provider("""
        <!doctype html><html><head>
        <title>A useful public page title</title>
        <meta name="description" content="Page description">
        <meta name="robots" content="index,follow">
        <meta property="og:title" content="Public page">
        <link rel="canonical" href="/page">
        <link rel="alternate" hreflang="en-GB" href="/page">
        <script type="application/ld+json">
        {"@graph":[{"@type":"Organization"},{"@type":["WebPage","FAQPage"]}]}
        </script></head><body><h1>Public page</h1><h2>Details</h2>
        <img src="one.jpg" alt="An example"><img src="two.jpg">
        <a href="/help">Help</a><a href="https://example.org/help#details">Same help</a>
        <a href="mailto:hello@example.org">Email</a><a href="#details">Section</a>
        <a href="javascript:void(0)">Action</a>
        <a href="https://other.example/help">Elsewhere</a>
        </body></html>
    """).browse_markup(brief())

    assert signals.status == "captured"
    assert signals.reason is None
    assert signals.browse_trace_id == "html-trace"
    assert signals.crawled_at == "2026-10-16T10:00:00Z"
    assert signals.canonical.value == str(brief().url)
    assert signals.meta_title.value == "A useful public page title"
    assert signals.meta_description.value == "Page description"
    assert signals.robots.value == "index,follow"
    assert signals.hreflang.value == ("en-GB",)
    assert signals.open_graph.value is True
    assert signals.jsonld_types.value == ("FAQPage", "Organization", "WebPage")
    assert signals.h1.value == signals.h2.value == 1
    assert signals.image_alt_coverage.value == 0.5
    assert signals.internal_link_count.value == 1


@pytest.mark.parametrize("markup", [
    "<h1>Page</h1><p>Only a body fragment.</p>",
    "<html><head><title>Short</title>",
    "<html><body><h1>Page</h1></body></html>",
])
def test_partial_html_does_not_invent_missing_metadata_or_schema(markup):
    signals = markup_provider(markup).browse_markup(brief())

    assert signals.status == "captured"
    assert signals.reason == "partial-html"
    for name in ("canonical", "robots", "meta_description", "jsonld_types", "open_graph"):
        assert getattr(signals, name).state == "not-provided"
    findings = run_technical_checks(
        signals, brief(),
        PageSnapshot(
            url=brief().url, title="Page", content="Public content.", provenance="recorded",
        ),
    )
    assert not any(
        finding.field in {"canonical", "robots", "meta_description", "jsonld_types", "open_graph"}
        for finding in findings.findings
    )


def test_text_response_is_not_treated_as_html_with_missing_tags():
    signals = markup_provider("# Public page\nUseful content but no HTML.").browse_markup(brief())

    assert signals.status == "not-captured"
    assert signals.reason == "html-not-provided"
    assert signals.canonical.state == "not-provided"
    assert signals.h1.state == "not-provided"


def test_complete_html_supports_bounded_absence_findings():
    signals = markup_provider(
        "<html><head></head><body><p>Public content.</p></body></html>",
    ).browse_markup(brief())

    assert signals.reason is None
    assert signals.canonical.state == "not-found"
    assert signals.meta_title.state == "not-found"
    assert signals.jsonld_types.state == "not-found"
    assert signals.h1.state == "present" and signals.h1.value == 0
    assert signals.image_alt_coverage.state == "not-provided"


@pytest.mark.parametrize("jsonld", ['{"@type":', "{}", '{"@context":"https://schema.org"}'])
def test_unusable_jsonld_is_unknown_instead_of_absent(jsonld):
    signals = markup_provider(
        '<html><head><script type="application/ld+json">'
        + jsonld + "</script></head><body></body></html>",
    ).browse_markup(brief())

    assert signals.jsonld_types.state == "not-provided"


def test_size_limited_html_does_not_claim_complete_body_coverage():
    markup = "<html><head></head><body><p>" + "x" * 200000 + "</p></body></html>"
    signals = markup_provider(markup).browse_markup(brief())

    assert signals.reason == "partial-html"
    assert signals.h1.state == "not-provided"
    assert signals.internal_link_count.state == "not-provided"
    assert signals.jsonld_types.state == "not-provided"


def test_browse_accepts_apex_to_www_canonicalisation():
    easyjet_brief = Brief(
        url="https://easyjet.com/",
        audience="Travellers",
        goal="Book a flight",
        locale="en-GB",
    )
    provider = WebIQ(
        "dummy-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "url": "https://www.easyjet.com/",
                    "content": "Live airline page evidence",
                },
            )
        ),
        url_validator=lambda value: value,
    )

    snapshot = provider.browse(easyjet_brief)

    assert str(snapshot.url) == "https://easyjet.com/"


def test_browse_accepts_removed_tracking_query_but_not_content_query():
    tracked_brief = Brief(
        url="https://www.easyjet.com/flights?msockid=tracking-value",
        audience="Travellers",
        goal="Book a flight",
        locale="en-GB",
    )
    provider = WebIQ(
        "dummy-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"url": "https://www.easyjet.com/flights", "content": "Live flights"},
            )
        ),
        url_validator=lambda value: value,
    )
    assert provider.browse(tracked_brief).provenance == "live"

    content_brief = tracked_brief.model_copy(
        update={"url": "https://www.easyjet.com/flights?destination=majorca"}
    )
    with pytest.raises(ProviderError, match="different page"):
        provider.browse(content_brief)


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


@pytest.mark.parametrize(
    "status,code",
    [
        (202, ProviderFailure.REQUEST),
        (301, ProviderFailure.REQUEST),
        (401, ProviderFailure.AUTH),
        (403, ProviderFailure.AUTH),
        (404, ProviderFailure.REQUEST),
        (429, ProviderFailure.RATE_LIMIT),
        (430, ProviderFailure.REQUEST),
        (500, ProviderFailure.REQUEST),
    ],
)
def test_errors_are_not_retried_or_exposed(status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://other.example"}, text="dummy-secret-body")
    provider = WebIQ("dummy-key", transport=httpx.MockTransport(handler), url_validator=lambda value: value)
    with pytest.raises(ProviderError, match=f"HTTP {status}") as caught:
        provider.browse(brief())
    assert len(calls) == 1
    assert caught.value.code == code
    assert caught.value.safe_detail == f"Web IQ returned HTTP {status}; no automatic retry"
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