"""Title cleaning + junk detection (pure string functions, synthetic inputs)."""

import pytest

from glens.lens import (
    _clean_title,
    _is_domain_only,
    _is_junk_title,
    _to_matches,
    _to_titles,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Generic marketplace prefixes get stripped...
        ("Amazon.com Acme Widget Pro", "Acme Widget Pro"),
        ("eBay Acme Widget Pro", "Acme Widget Pro"),
        ("Walmart Acme Widget Pro", "Acme Widget Pro"),
        ("www.shop.example Acme Widget Pro", "Acme Widget Pro"),
        # ...but a brand boutique name is left intact on purpose.
        ("Marine Layer Corduroy Shirt", "Marine Layer Corduroy Shirt"),
        # Trailing ratings like 4.7(20) / 4.5 (1,024) go away.
        ("Acme Widget Pro 4.7(20)", "Acme Widget Pro"),
        ("Acme Widget Pro 4.5 (1,024)", "Acme Widget Pro"),
        # UI suffixes go away.
        ("Acme Widget Pro Opens in new tab", "Acme Widget Pro"),
        ("Acme Widget Pro Out of stock", "Acme Widget Pro"),
        # Merchant usually follows a separator; keep the product part.
        ("Acme Widget Pro | Acme Store", "Acme Widget Pro"),
        ("Acme Widget Pro – Best Deals", "Acme Widget Pro"),
        ("Acme Widget Pro — Outlet", "Acme Widget Pro"),
        ("Acme Widget Pro · In stock", "Acme Widget Pro"),
        # Whitespace collapses.
        ("  Acme   Widget\n Pro  ", "Acme Widget Pro"),
        # A bare merchant host ahead of the product name, with or without Lens'
        # "(+N)" more-sellers badge, is a label -- not part of the title.
        ("thepremierstore.com (+1) - Acme Widget Pro", "Acme Widget Pro"),
        ("shop.example.co.uk Acme Widget Pro", "Acme Widget Pro"),
        ("acmestore.com: Acme Widget Pro", "Acme Widget Pro"),
        # Google flattens the result breadcrumb into the aria-label; everything
        # from the URL onward is chrome.
        ("Acme Widget Pro Acme https://www.acme.com › shoes", "Acme Widget Pro Acme"),
        ("Acme Widget Pro www.acme.com", "Acme Widget Pro"),
        ("Acme Widget Pro › Shoes", "Acme Widget Pro"),
        # Dotted names that are NOT hosts must survive untouched.
        ("U.S. Polo Assn Shirt", "U.S. Polo Assn Shirt"),
        ("St. Louis Blues Jersey", "St. Louis Blues Jersey"),
        # Everything at once: prefix + rating + UI suffix.
        ("Amazon.com Acme Widget Pro 4.7(20) Opens in new tab", "Acme Widget Pro"),
        ("", ""),
    ],
)
def test_clean_title(raw, expected):
    assert _clean_title(raw) == expected


@pytest.mark.parametrize("host", ["Amazon.com", "thepremierstore.com",
                                  "www.shop.example.co.uk", "nike.com"])
def test_domain_only_titles_are_not_product_names(host):
    assert _is_domain_only(host)


@pytest.mark.parametrize("real", ["Acme Widget Pro", "U.S. Polo Assn Shirt",
                                  "Runner 9 Trail Shoe (Blue)", "Killshot 2"])
def test_domain_only_leaves_real_titles_alone(real):
    assert not _is_domain_only(real)


def test_domain_only_titles_are_dropped_from_the_ranking():
    # Enough same-merchant tiles and a bare host outranks the actual product,
    # landing in top_title. The anchors themselves are still real links, so
    # _to_matches keeps them -- only the *ranking* screens them out.
    items = [
        {"href": "https://www.amazon.com/dp/A1", "text": "Amazon.com"},
        {"href": "https://www.amazon.com/dp/A2", "text": "Amazon.com"},
        {"href": "https://www.amazon.com/dp/A3", "text": "Amazon.com"},
        {"href": "https://a.example/p/1", "text": "Acme Widget Pro Runner"},
    ]
    assert _to_titles(items, 6) == ["Acme Widget Pro Runner"]
    assert any(m.domain == "amazon.com" for m in _to_matches(items, 20))


@pytest.mark.parametrize(
    "junk",
    [
        "Read more",
        "READ MORE",
        "Opens in new tab",
        "See more",
        "Shop now",
        "View related links",
        "Related links",
        "View product",
        "Product",
        "",
        "New arrivals & restocks every week",   # _JUNK_CONTAINS
        "Let us know your feedback",             # _JUNK_CONTAINS
    ],
)
def test_is_junk_title_true(junk):
    assert _is_junk_title(junk)


@pytest.mark.parametrize(
    "real",
    [
        "Acme Widget Pro",
        "Marine Layer Corduroy Shirt",
        "Runner 9 Trail Shoe (Blue)",
    ],
)
def test_is_junk_title_false(real):
    assert not _is_junk_title(real)


# ---------------------------------------------------------------------------
# Merchant display-name prefixes.
#
# Lens used to prefix a tile label with the seller's HOST ("shop.example (+1) -
# Acme Widget"), which _DOMAIN_PREFIX strips. It now uses the display NAME
# ("Sneaker Politics Acme Widget"), which nothing can recognise from the text
# alone -- so the domain the link resolved to is what settles it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "domain", "expected"),
    [
        # Multi-word names that spell out the domain.
        ("Sneaker Politics Acme Widget Pro", "sneakerpolitics.com", "Acme Widget Pro"),
        ("Foot Locker Acme Widget Pro", "footlocker.com", "Acme Widget Pro"),
        # Punctuation in the name is ignored on both sides.
        ("Macy's Acme Widget Pro", "macys.com", "Acme Widget Pro"),
        ("Sneakers.com Acme Widget Pro", "sneakers.com", "Acme Widget Pro"),
        # Case and country TLDs don't matter.
        ("DTLR Acme Widget Pro", "dtlr.com", "Acme Widget Pro"),
        ("Acme Buy Acme Widget Pro", "acme.sa", "Buy Acme Widget Pro"),
        # A dash or colon left behind by the prefix goes with it.
        ("Feature - Acme Widget Pro", "feature.com", "Acme Widget Pro"),
        # A brand selling on its own site: one copy of the name is the seller,
        # the other belongs to the product.
        ("Acme Acme Widget Pro", "acme.com", "Acme Widget Pro"),
    ],
)
def test_clean_title_strips_the_merchant_display_name(raw, domain, expected):
    assert _clean_title(raw, domain) == expected


@pytest.mark.parametrize(
    ("raw", "domain"),
    [
        # The name doesn't spell out the domain, so we can't be sure it IS the
        # name -- leave it alone rather than eat a real word off the product.
        ("Sports Illustrated Acme Widget Pro", "si.com"),
        ("Premier Acme Widget Pro", "thepremierstore.com"),
        # A product whose opening words merely resemble a merchant.
        ("Acme Widget Pro", "shop.example"),
        # No domain (an unresolved wrapper) means no signal to strip on.
        ("Foot Locker Acme Widget Pro", ""),
    ],
)
def test_clean_title_keeps_a_prefix_it_cannot_confirm(raw, domain):
    assert _clean_title(raw, domain) == _clean_title(raw)


def test_clean_title_never_strips_a_label_down_to_nothing():
    # The tile's whole label is the seller. Keep it: _is_domain_only and the
    # junk filters decide what to do with it downstream.
    assert _clean_title("Foot Locker", "footlocker.com") == "Foot Locker"


def test_stripping_the_seller_restores_the_frequency_ranking():
    # The point of the whole exercise. With the seller baked into every label,
    # each tile for the same shoe cleaned to a different string, every count
    # was 1, and the exact match could not rise to the top.
    items = [
        {"href": "https://footlocker.example/p/1", "text": "Foot Locker Acme Widget Pro"},
        {"href": "https://champssports.example/p/2", "text": "Champs Sports Acme Widget Pro"},
        {"href": "https://shop.example/p/3", "text": "Some Other Gadget Deluxe"},
    ]
    assert _to_titles(items, 6)[0] == "Acme Widget Pro"
    assert [m.title for m in _to_matches(items, 20)][:2] == ["Acme Widget Pro"] * 2
