"""Title cleaning + junk detection (pure string functions, synthetic inputs)."""

import pytest

from glens.lens import _clean_title, _is_junk_title


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
        # Everything at once: prefix + rating + UI suffix.
        ("Amazon.com Acme Widget Pro 4.7(20) Opens in new tab", "Acme Widget Pro"),
        ("", ""),
    ],
)
def test_clean_title(raw, expected):
    assert _clean_title(raw) == expected


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
