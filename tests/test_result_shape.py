"""_to_matches / _to_titles over synthetic anchor items."""

import pytest

from glens.lens import _is_external, _to_matches, _to_titles


@pytest.mark.parametrize("href", [
    # A real merchant link that merely carries a Google token in its query or
    # path must still count as external (the host is what decides).
    "https://www.example-shop.com/product?utm_source=google.com",
    "https://shop.example/deal?ved=2ahUKEwi&url=https://google.com/aclk",
    "https://store.example/google.co-alternatives",
    "https://a.example/product/1",
])
def test_is_external_keeps_merchant_links_with_google_in_query(href):
    assert _is_external(href) is True


@pytest.mark.parametrize("href", [
    "https://www.google.com/search?q=x",       # google.com + subdomains
    "https://lens.google.com/uploadbyurl",
    "https://books.google.co.uk/books?id=1",   # country google.co.* hosts
    "https://encrypted-tbn0.gstatic.com/img",  # gstatic CDNs
    "https://blog.google/products/",           # the .google gTLD
    "relative/path",                           # no scheme -> not a link
    "https://",                                # no host -> not a link
])
def test_is_external_excludes_google_hosts(href):
    assert _is_external(href) is False


@pytest.mark.parametrize("href", [
    "https://www.google.de/search?q=x",
    "https://www.google.fr/imghp",
    "https://images.google.it/",
    "https://www.google.com.br/search?q=x",
    "https://www.google.co/",                  # google.co (Colombia)
])
def test_is_external_excludes_every_google_country_host(href):
    # Google's own links are internal whichever country domain the session
    # landed on -- not just the google.com / google.co.* ones. Counting them as
    # merchant links fills matches with Google's own chrome AND fools the req
    # backend's "enough external anchors?" gate check into trusting a gated page.
    assert _is_external(href) is False


@pytest.mark.parametrize("href", [
    "https://notgoogle.com/deal",
    "https://mygstatic.com/product/1",
    "https://google.com.cheap-deals.example/p",   # google.com only as a label prefix
])
def test_is_external_keeps_hosts_that_merely_contain_a_google_name(href):
    # The host has to *be* Google's, not merely contain the string: matching a
    # substring drops a real merchant link, which is the same class of bug that
    # moving the check from the whole URL onto the host was meant to fix.
    assert _is_external(href) is True

# The exact product repeats across tiles (that's the frequency-ranking signal);
# one internal link, one junk anchor, and one domain+title duplicate are mixed in.
ITEMS = [
    {"href": "https://www.google.com/search?q=widget", "text": "Acme Widget Pro Runner"},
    {"href": "https://a.example/product/1", "text": "Acme Widget Pro Runner"},
    {"href": "https://www.b.example/p/2", "text": "Acme Widget Pro Runner"},
    {"href": "https://a.example/product/1?ref=tile", "text": "Acme Widget Pro Runner"},  # dup
    {"href": "https://c.example/p/3", "text": "Different Gadget Deluxe"},
    {"href": "https://d.example/p/4", "text": "Opens in new tab"},  # junk title
]


def test_to_matches_external_only_deduped():
    matches = _to_matches(ITEMS, max_matches=10)
    domains = [m.domain for m in matches]

    # Internal google link and junk-titled anchor are dropped; the second
    # a.example anchor with the same title collapses into the first.
    assert domains == ["a.example", "b.example", "c.example"]
    # "www." is stripped from domains.
    assert "www.b.example" not in domains
    assert matches[0].url == "https://a.example/product/1"
    assert matches[0].title == "Acme Widget Pro Runner"


def test_to_matches_respects_cap():
    assert len(_to_matches(ITEMS, max_matches=2)) == 2


def test_to_matches_truncates_very_long_titles():
    long_items = [{"href": "https://e.example/p", "text": "Word " * 60}]
    (match,) = _to_matches(long_items, max_matches=5)
    assert len(match.title) == 200


def test_to_titles_frequency_ranked():
    titles = _to_titles(ITEMS, max_titles=6)
    # The repeated exact product ranks first; junk and internal links never count.
    assert titles == ["Acme Widget Pro Runner", "Different Gadget Deluxe"]


def test_to_titles_respects_cap():
    assert _to_titles(ITEMS, max_titles=1) == ["Acme Widget Pro Runner"]


def test_to_titles_drops_too_short_and_too_long():
    items = [
        {"href": "https://a.example/1", "text": "Tiny"},          # < 8 chars
        {"href": "https://b.example/2", "text": "x" * 150},        # > 100 chars
        {"href": "https://c.example/3", "text": "Right-Sized Product Name"},
    ]
    assert _to_titles(items, max_titles=5) == ["Right-Sized Product Name"]


def test_short_title_still_counts_as_match():
    # Matches only require a non-junk title; the 8..100 length window is a
    # titles-ranking rule, not a match filter.
    items = [{"href": "https://a.example/1", "text": "Tiny"}]
    assert len(_to_matches(items, max_matches=5)) == 1
    assert _to_titles(items, max_titles=5) == []
