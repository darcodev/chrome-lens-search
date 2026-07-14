"""Language preference threading with fake sessions, no network.

The only locale lever Google honours on this endpoint is the Accept-Language
header; hl/gl URL overrides get a 403 (live-verified 2026-07), so ``lang``
rides the req session and nothing ever edits the session-signed results URL.
"""

import json

import pytest

import glens
import glens.lens as lens_mod
from glens.lens import _UPLOAD_ENDPOINT, _req_session

SYNTHETIC_RESULTS_HTML = """
<html><body>
  <a href="https://a.example/p/1" aria-label="Acme Widget Pro Runner">t</a>
  <a href="https://b.example/p/2" aria-label="Acme Widget Pro Runner">t</a>
  <a href="https://c.example/p/3" aria-label="Different Gadget Deluxe">t</a>
</body></html>
"""
RESULTS_URL = "https://www.google.com/search?udm=26&vsrid=abc123&gsessionid=signed"


# ---------------------------------------------------------------------------
# Session header
# ---------------------------------------------------------------------------

def test_req_session_accept_language():
    jar = {"user_agent": "ua", "cookies": []}
    assert _req_session(jar).headers["Accept-Language"].startswith("en-US")
    assert _req_session(jar, lang="de").headers["Accept-Language"].startswith("de,")


# ---------------------------------------------------------------------------
# The req backend sends lang via the session; signed URLs are never edited
# ---------------------------------------------------------------------------

class RecordingSession:
    def __init__(self):
        self.posts = []
        self.gets = []

    class _Resp:
        def __init__(self, url, text):
            self.url = url
            self.text = text

        def raise_for_status(self):
            pass

    def post(self, url, *args, **kwargs):
        self.posts.append(url)
        # Upload leg redirects to the signed results URL, HTML already usable.
        return self._Resp(RESULTS_URL, SYNTHETIC_RESULTS_HTML)

    def get(self, url, **kwargs):
        self.gets.append(url)
        return self._Resp(url, SYNTHETIC_RESULTS_HTML)


@pytest.fixture
def jar_file(tmp_path):
    jar = tmp_path / "jar.json"
    jar.write_text(json.dumps({
        "user_agent": "synthetic-agent/1.0",
        "cookies": [{"name": "TEST", "value": "1", "domain": ".google.com", "path": "/"}],
    }), encoding="utf-8")
    return jar


def test_req_backend_passes_lang_to_session(monkeypatch, jar_file):
    session = RecordingSession()
    session_args = {}

    def fake_session(jar, lang=None):
        session_args["lang"] = lang
        return session

    monkeypatch.setattr(lens_mod, "_req_session", fake_session)

    items, url = lens_mod._search_via_requests(b"img", jar_file, "de")

    assert items is not None
    # The upload URL and the signed results URL are used exactly as served.
    assert session.posts == [_UPLOAD_ENDPOINT]
    assert session.gets == []
    assert url == RESULTS_URL
    # lang must reach the session builder (it drives the Accept-Language header).
    assert session_args["lang"] == "de"


# ---------------------------------------------------------------------------
# search() threads lang through
# ---------------------------------------------------------------------------

def test_search_forwards_lang(monkeypatch, tmp_path):
    seen = {}

    def fake_req(frame_bytes, cookie_file, lang=None):
        seen["lang"] = lang
        return ([{"href": "https://a.example/1", "text": "Acme Widget Pro Runner"}],
                RESULTS_URL)

    monkeypatch.setattr(lens_mod, "_search_via_requests", fake_req)
    result = glens.search(b"img", backend="req", cookie_file=tmp_path / "jar.json",
                          lang="de")

    assert result is not None
    assert seen == {"lang": "de"}


def test_env_default_lang_flows_to_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(lens_mod, "DEFAULT_LANG", "de")
    seen = {}

    def fake_req(frame_bytes, cookie_file, lang=None):
        seen["lang"] = lang
        return ([{"href": "https://a.example/1", "text": "Acme Widget Pro Runner"}],
                RESULTS_URL)

    monkeypatch.setattr(lens_mod, "_search_via_requests", fake_req)
    glens.search(b"img", backend="req", cookie_file=tmp_path / "jar.json")

    assert seen == {"lang": "de"}
