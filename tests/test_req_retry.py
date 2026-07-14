"""Retry/backoff behaviour of the req backend, with a fake session, no network.

The synthetic HTML below is hand-written to mimic only the *shape* of a results
page (three external anchors); it contains no Google content.
"""

import json

import pytest
import requests

import glens.lens as lens_mod

SYNTHETIC_RESULTS_HTML = """
<html><body>
  <a href="https://a.example/p/1" aria-label="Acme Widget Pro Runner">t</a>
  <a href="https://b.example/p/2" aria-label="Acme Widget Pro Runner">t</a>
  <a href="https://c.example/p/3" aria-label="Different Gadget Deluxe">t</a>
</body></html>
"""
RESULTS_URL = "https://www.google.com/search?udm=26&synthetic=1"
JS_GATE_HTML = '<a href="/httpservice/retry/enablejs">enable</a>'


class FakeResponse:
    def __init__(self, url: str, text: str, status: int = 200):
        self.url = url
        self.text = text
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"synthetic {self.status}")


class FlakySession:
    """Raises a transient transport error on the first N post() calls."""

    def __init__(self, fail_first: int, html: str = SYNTHETIC_RESULTS_HTML,
                 url: str = RESULTS_URL, status: int = 200):
        self.fail_first = fail_first
        self.posts = 0
        self.html = html
        self.url = url
        self.status = status

    def post(self, *args, **kwargs):
        self.posts += 1
        if self.posts <= self.fail_first:
            raise requests.ConnectionError("synthetic transport failure")
        return FakeResponse(self.url, self.html, self.status)

    def get(self, *args, **kwargs):
        return FakeResponse(self.url, self.html, self.status)


@pytest.fixture
def jar_file(tmp_path):
    jar = tmp_path / "jar.json"
    jar.write_text(json.dumps({
        "user_agent": "synthetic-agent/1.0",
        "cookies": [{"name": "TEST", "value": "1", "domain": ".google.com", "path": "/"}],
    }), encoding="utf-8")
    return jar


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(lens_mod.time, "sleep", lambda seconds: None)


def test_retries_once_on_transient_failure(monkeypatch, jar_file):
    session = FlakySession(fail_first=1)
    monkeypatch.setattr(lens_mod, "_req_session", lambda jar, lang=None: session)

    items, url = lens_mod._search_via_requests(b"img", jar_file)

    assert session.posts == 2
    assert url == RESULTS_URL
    assert items is not None and len(items) == 3


def test_gives_up_after_all_attempts(monkeypatch, jar_file):
    session = FlakySession(fail_first=99)
    monkeypatch.setattr(lens_mod, "_req_session", lambda jar, lang=None: session)

    items, url = lens_mod._search_via_requests(b"img", jar_file)

    assert session.posts == lens_mod._REQ_ATTEMPTS
    assert items is None and url is None


def test_js_gate_shell_does_not_retry(monkeypatch, jar_file):
    # A JS-gate shell means stale cookies, not a transient blip: fall back
    # immediately (one attempt) so the browser can re-mint the jar.
    session = FlakySession(fail_first=0, html=JS_GATE_HTML)
    monkeypatch.setattr(lens_mod, "_req_session", lambda jar, lang=None: session)

    items, url = lens_mod._search_via_requests(b"img", jar_file)

    assert session.posts == 1
    assert items is None
    assert url == RESULTS_URL


def test_no_jar_short_circuits(tmp_path):
    items, url = lens_mod._search_via_requests(b"img", tmp_path / "missing-jar.json")
    assert items is None and url is None


def test_http_error_does_not_retry(monkeypatch, jar_file):
    # A hard status (403/429) is deterministic: no second upload of the image.
    session = FlakySession(fail_first=0, status=429)
    monkeypatch.setattr(lens_mod, "_req_session", lambda jar, lang=None: session)

    items, url = lens_mod._search_via_requests(b"img", jar_file)

    assert session.posts == 1
    assert items is None and url is None


def test_non_results_redirect_falls_back(monkeypatch, jar_file):
    # Upload redirected somewhere that is neither udm=26 nor a lens URL.
    session = FlakySession(fail_first=0, url="https://www.google.com/sorry/index")
    monkeypatch.setattr(lens_mod, "_req_session", lambda jar, lang=None: session)

    items, url = lens_mod._search_via_requests(b"img", jar_file)

    assert session.posts == 1
    assert items is None and url is None


def test_degraded_page_with_few_external_anchors_falls_back(monkeypatch, jar_file):
    degraded = '<a href="https://only.example/one" aria-label="Lonely Product Tile">t</a>'
    session = FlakySession(fail_first=0, html=degraded)
    monkeypatch.setattr(lens_mod, "_req_session", lambda jar, lang=None: session)

    items, url = lens_mod._search_via_requests(b"img", jar_file)

    assert session.posts == 1
    assert items is None
    assert url == RESULTS_URL


def test_gate_marker_on_healthy_page_is_not_a_gate(monkeypatch, jar_file):
    # Healthy server-rendered pages also carry the enable-js noscript link;
    # the page must be judged by its anchors, not the marker (live-verified).
    healthy_with_marker = JS_GATE_HTML + SYNTHETIC_RESULTS_HTML
    session = FlakySession(fail_first=0, html=healthy_with_marker)
    monkeypatch.setattr(lens_mod, "_req_session", lambda jar, lang=None: session)

    items, url = lens_mod._search_via_requests(b"img", jar_file)

    assert items is not None and len(items) == 4
    assert url == RESULTS_URL


def test_chrome_header_surface():
    jar = {"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/149.0.0.0 Safari/537.36",
           "cookies": []}
    headers = lens_mod._req_session(jar).headers

    # Google gates bare header sets even with a fresh jar (live-verified);
    # the full Chrome surface must be present and version-consistent.
    assert headers["Referer"] == "https://www.google.com/"
    assert headers["Sec-Fetch-Mode"] == "navigate"
    assert 'v="149"' in headers["sec-ch-ua"]
    assert headers["sec-ch-ua-platform"] == '"Windows"'


def test_chrome_headers_skip_ua_hints_for_non_chrome_ua():
    jar = {"user_agent": "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/128.0",
           "cookies": []}
    headers = lens_mod._req_session(jar).headers
    assert "sec-ch-ua" not in headers
    assert headers["Referer"] == "https://www.google.com/"
