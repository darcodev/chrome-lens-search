"""Anchor harvesting from server-rendered HTML.

All fixtures here are synthetic: hand-written markup that mimics the *shape*
of a results page (anchors + aria-labels). Never commit real Google responses.
"""

from glens.lens import (
    _harvest_anchors,
    _is_external,
    _parse_anchors_from_html,
    _to_matches,
    _unwrap_google_redirect,
)

# Hand-crafted: one relative google-redirect wrapper, one aria-labelled tile,
# one nested-markup anchor, one anchor with no href, and an HTML entity.
SYNTHETIC_HTML = """
<html><body>
  <a href="/url?q=https://shop.example/item&amp;sa=U&amp;ved=abc">Wrapped <b>Link</b></a>
  <a href="https://merchant.example/product/runner-9" aria-label="Acme Runner 9 Trail Shoe">
    <img src="tile.png">
  </a>
  <a href="https://other.example/page">  Nested
      <span>text   here</span></a>
  <a class="no-href-anchor">skip me</a>
</body></html>
"""


def test_parse_anchors_harvests_href_and_text():
    items = _parse_anchors_from_html(SYNTHETIC_HTML)
    hrefs = [i["href"] for i in items]

    # The relative /url?q= wrapper is joined onto google.com then unwrapped.
    assert "https://shop.example/item" in hrefs
    assert "https://merchant.example/product/runner-9" in hrefs
    assert "https://other.example/page" in hrefs
    # Anchor without an href is skipped entirely.
    assert len(items) == 3


def test_parse_anchors_prefers_aria_label():
    items = _parse_anchors_from_html(SYNTHETIC_HTML)
    by_href = {i["href"]: i["text"] for i in items}
    assert by_href["https://merchant.example/product/runner-9"] == "Acme Runner 9 Trail Shoe"


def test_parse_anchors_strips_tags_and_collapses_whitespace():
    items = _parse_anchors_from_html(SYNTHETIC_HTML)
    by_href = {i["href"]: i["text"] for i in items}
    assert by_href["https://shop.example/item"] == "Wrapped Link"
    assert by_href["https://other.example/page"] == "Nested text here"


def test_parse_anchors_empty_input():
    assert _parse_anchors_from_html("") == []
    assert _parse_anchors_from_html("<p>no anchors at all</p>") == []


def test_unwrap_google_redirect_q_param():
    wrapped = "https://www.google.com/url?q=https://shop.example/item&sa=U"
    assert _unwrap_google_redirect(wrapped) == "https://shop.example/item"


def test_unwrap_google_redirect_url_param():
    wrapped = "https://www.google.com/url?url=https://shop.example/other&sa=U"
    assert _unwrap_google_redirect(wrapped) == "https://shop.example/other"


def test_unwrap_google_redirect_passthrough():
    plain = "https://merchant.example/product"
    assert _unwrap_google_redirect(plain) == plain


def test_unwrap_google_redirect_non_http_target_unchanged():
    wrapped = "https://www.google.com/url?q=javascript:alert(1)"
    assert _unwrap_google_redirect(wrapped) == wrapped


def test_unwrap_leaves_a_merchants_own_url_route_alone():
    # Only Google's wrapper gets unwrapped. A merchant with its own /url?q=
    # route (an affiliate hop, say) must keep the link we actually harvested --
    # rewriting it would silently swap in a third party's URL.
    merchant = "https://shop.example/url?q=https://elsewhere.example/x"
    assert _unwrap_google_redirect(merchant) == merchant


def test_unwrap_handles_country_google_domains():
    wrapped = "https://www.google.co.uk/url?q=https://shop.example/item&sa=U"
    assert _unwrap_google_redirect(wrapped) == "https://shop.example/item"


# ---------------------------------------------------------------------------
# Browser harvest: same normalisation as the HTML harvest
# ---------------------------------------------------------------------------

class _FakeDriver:
    """Returns a canned anchor list from the harvest's one JS round-trip."""

    def __init__(self, items):
        self._items = items

    def execute_script(self, _script):
        return self._items


WRAPPED_TILE = "https://www.google.com/url?q=https://shop.example/p/1&sa=U&ved=abc"


def test_browser_harvest_unwraps_google_wrappers():
    # The rendered DOM carries the same wrappers the raw HTML does. Left wrapped,
    # every tile is judged by google.com's host and thrown away as internal --
    # the browser pass would report zero matches on a page full of products.
    assert not _is_external(WRAPPED_TILE)

    items = _harvest_anchors(_FakeDriver(
        [{"href": WRAPPED_TILE, "text": "Acme Widget Pro Runner"},
         {"href": "https://merchant.example/p/2", "text": "Acme Widget Pro Runner"}]
    ))

    assert [i["href"] for i in items] == ["https://shop.example/p/1",
                                          "https://merchant.example/p/2"]
    assert [m.domain for m in _to_matches(items, 20)] == ["shop.example", "merchant.example"]


def test_browser_harvest_matches_the_html_harvest():
    # Both backends must hand the same shape downstream; that symmetry is what
    # makes the browser a real fallback for the req path.
    from_browser = _harvest_anchors(_FakeDriver([{"href": WRAPPED_TILE, "text": "Tile"}]))
    from_html = _parse_anchors_from_html(f'<a href="{WRAPPED_TILE}">Tile</a>')
    assert from_browser == from_html


def test_browser_harvest_survives_a_dead_driver():
    class Exploding:
        def execute_script(self, _script):
            raise RuntimeError("browser went away")

    assert _harvest_anchors(Exploding()) == []


def test_browser_harvest_tolerates_missing_hrefs():
    assert _harvest_anchors(_FakeDriver([{"text": "no href key"}])) == [{"href": "",
                                                                        "text": "no href key"}]


# A blank aria-label is not a label. Google emits them on image-wrapper tiles,
# and taking them literally used to drop the anchor's own text -- and with it
# the match -- while the browser harvest (JS: "" is falsy) kept it.
BLANK_ARIA_HTML = """
<html><body>
  <a href="https://merchant.example/killshot" aria-label="">Acme Killshot 2 Leather</a>
  <a href="https://other.example/samba" aria-label="   ">Acme Samba OG Shoe</a>
  <a href="https://third.example/gel" aria-label="Acme Gel Trail Runner">ignored inner</a>
</body></html>
"""


def test_blank_aria_label_falls_back_to_the_anchor_text():
    items = _parse_anchors_from_html(BLANK_ARIA_HTML)
    assert [i["text"] for i in items] == [
        "Acme Killshot 2 Leather",       # aria-label="" -> inner text
        "Acme Samba OG Shoe",            # whitespace-only aria-label -> inner text
        "Acme Gel Trail Runner",         # a real aria-label still wins
    ]


def test_blank_aria_label_anchors_still_become_matches():
    matches = _to_matches(_parse_anchors_from_html(BLANK_ARIA_HTML), 10)
    assert [m.domain for m in matches] == [
        "merchant.example", "other.example", "third.example",
    ]
