"""Anchor harvesting from server-rendered HTML.

All fixtures here are synthetic: hand-written markup that mimics the *shape*
of a results page (anchors + aria-labels). Never commit real Google responses.
"""

from glens.lens import _parse_anchors_from_html, _unwrap_google_redirect

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
