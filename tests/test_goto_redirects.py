"""Google's encrypted outbound link wrapper (``google.com/goto?url=<blob>``).

Google used to hand merchant links out as ``google.com/url?q=<target>``, which
unwraps offline. Lens result tiles now use ``/goto?url=<blob>`` instead, where
the blob is encrypted and the merchant URL appears nowhere in the page. Judged
by its host alone a wrapper looks like one of Google's own links, so every
product tile got dropped and lookups returned nothing at all.

No network: the resolver's session is faked out.
"""

import pytest

import glens.lens as lens_mod
from glens.lens import (
    _is_external,
    _is_google_redirect,
    _parse_anchors_from_html,
    _redirect_target,
    _resolve_google_redirects,
    _to_matches,
    _to_titles,
    _unwrap_google_redirect,
)

# Shape only -- an opaque token standing in for Google's encrypted blob.
BLOB = "CAESoAEB6zswFSuQw0v1wLXirUf"
GOTO = f"https://www.google.com/goto?url={BLOB}"
PLAIN_WRAPPER = "https://www.google.com/url?q=https://shop.example/p/1&sa=U"


class FakeRedirectSession:
    """Answers 302 with a Location, exactly as the real wrapper does."""

    def __init__(self, targets: dict, fail: set = ()):
        self.targets = targets
        self.fail = set(fail)
        self.requested = []
        self.closed = False
        self.headers = {}

    def get(self, url, timeout=None, allow_redirects=None):
        self.requested.append((url, allow_redirects))
        if url in self.fail:
            raise lens_mod.requests.ConnectionError("synthetic transport failure")
        return type("R", (), {"headers": {"Location": self.targets.get(url, "")}})()

    def close(self):
        self.closed = True


@pytest.fixture
def fake_session(monkeypatch):
    """Install a fake requests.Session for the resolver; returns a setter."""
    holder = {}

    def install(targets, fail=()):
        holder["session"] = FakeRedirectSession(targets, fail)
        monkeypatch.setattr(lens_mod.requests, "Session", lambda: holder["session"])
        return holder["session"]

    return install


# --- recognising a wrapper ------------------------------------------------

@pytest.mark.parametrize("href", [
    GOTO,
    PLAIN_WRAPPER,
    "https://www.google.co.uk/goto?url=abc",     # country hosts wrap too
    "https://google.com/url?url=https://shop.example/x",
])
def test_google_wrappers_are_recognised(href):
    assert _is_google_redirect(href) is True


@pytest.mark.parametrize("href", [
    "https://www.google.com/search?q=x&url=notatarget",  # not a wrapper path
    "https://www.google.com/goto",                       # no target parameter
    "https://www.google.com/goto?other=1",
    "https://shop.example/goto?url=https://elsewhere.example/x",  # merchant's own
    "https://shop.example/url?q=https://elsewhere.example/x",
    "relative/path",
])
def test_non_wrappers_are_not_mistaken_for_one(href):
    assert _is_google_redirect(href) is False


def test_a_wrapper_counts_as_an_outbound_link():
    # The whole bug: judged by host alone this is google.com's own link, so
    # every product tile on the page was thrown away.
    assert _is_external(GOTO) is True
    assert _is_external("https://www.google.com/search?udm=26") is False


# --- unwrapping vs resolving ----------------------------------------------

def test_a_plain_target_is_unwrapped_without_the_network():
    assert _redirect_target(PLAIN_WRAPPER) == "https://shop.example/p/1"
    assert _unwrap_google_redirect(PLAIN_WRAPPER) == "https://shop.example/p/1"


def test_an_encrypted_blob_survives_unwrapping_intact():
    # There is no URL inside the blob, so unwrapping must leave it for the
    # resolver rather than inventing a target from the query string.
    assert _redirect_target(GOTO) is None
    assert _unwrap_google_redirect(GOTO) == GOTO


def test_resolving_rewrites_wrappers_to_the_merchant_url(fake_session):
    session = fake_session({GOTO: "https://shop.example/p/1"})
    items = [{"href": GOTO, "text": "Acme Widget Pro Runner"}]

    _resolve_google_redirects(items)

    assert items[0]["href"] == "https://shop.example/p/1"
    # Read the Location header and stop: never follow through to the merchant.
    assert session.requested == [(GOTO, False)]
    assert session.closed is True


def test_identical_wrappers_are_fetched_once(fake_session):
    session = fake_session({GOTO: "https://shop.example/p/1"})
    items = [{"href": GOTO, "text": "a"}, {"href": GOTO, "text": "b"}]

    _resolve_google_redirects(items)

    assert len(session.requested) == 1
    assert [i["href"] for i in items] == ["https://shop.example/p/1"] * 2


def test_already_plain_links_are_never_fetched(fake_session):
    session = fake_session({})
    items = [{"href": "https://shop.example/p/1", "text": "a"},
             {"href": "https://www.google.com/search?udm=26", "text": "b"}]

    _resolve_google_redirects(items)

    assert session.requested == []


def test_an_unresolvable_wrapper_keeps_its_working_link(fake_session):
    # The wrapper still resolves in a browser, so keep it rather than dropping
    # the match outright -- but don't claim google.com is the merchant.
    fake_session({}, fail={GOTO})
    items = [{"href": GOTO, "text": "Acme Widget Pro Runner"}]

    _resolve_google_redirects(items)

    assert items[0]["href"] == GOTO
    assert [m.domain for m in _to_matches(items, 20)] == [""]


def test_a_non_http_location_is_refused(fake_session):
    fake_session({GOTO: "javascript:alert(1)"})
    items = [{"href": GOTO, "text": "Acme Widget Pro Runner"}]

    _resolve_google_redirects(items)

    assert items[0]["href"] == GOTO


# --- the whole page -------------------------------------------------------

WRAPPED_PAGE = f"""
<html><body>
  <a href="/search?udm=26">Visual matches</a>
  <a href="/goto?url={BLOB}1" aria-label="Walmart Acme Widget Pro Runner">t</a>
  <a href="/goto?url={BLOB}2" aria-label="eBay Acme Widget Pro Runner">t</a>
  <a href="/goto?url={BLOB}3" aria-label="Acme Widget Pro Runner">t</a>
</body></html>
"""


def test_a_page_of_wrapped_tiles_yields_real_matches(fake_session):
    session = fake_session({
        f"https://www.google.com/goto?url={BLOB}1": "https://walmart.example/p/1",
        f"https://www.google.com/goto?url={BLOB}2": "https://ebay.example/p/2",
        f"https://www.google.com/goto?url={BLOB}3": "https://shop.example/p/3",
    })
    items = _parse_anchors_from_html(WRAPPED_PAGE)

    # The gate check runs before resolution, so wrapped tiles have to read as
    # real results right away or the req backend calls a healthy page gated.
    assert len([i for i in items if _is_external(i["href"])]) == 3

    _resolve_google_redirects(items)

    assert [m.domain for m in _to_matches(items, 20)] == [
        "walmart.example", "ebay.example", "shop.example",
    ]
    # All three tiles name the same product once the marketplace prefix is
    # stripped, so frequency ranking lifts it to the top -- the exact-match
    # signal the library exists to report.
    assert _to_titles(items, 6) == ["Acme Widget Pro Runner"]
    assert len(session.requested) == 3
