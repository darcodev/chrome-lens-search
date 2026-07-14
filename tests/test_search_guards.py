"""search() guard rails and backend dispatch; no network, no browser.

Backends are monkeypatched with synthetic anchor items; nothing here touches
Google or launches Chrome.
"""

import logging

import pytest

import glens
import glens.lens as lens_mod

SYNTHETIC_ITEMS = [
    {"href": "https://a.example/product/1", "text": "Acme Widget Pro Runner"},
    {"href": "https://b.example/p/2", "text": "Acme Widget Pro Runner"},
    {"href": "https://c.example/p/3", "text": "Different Gadget Deluxe"},
]
RESULTS_URL = "https://www.google.com/search?udm=26&synthetic=1"


def test_missing_image_file_returns_none(tmp_path):
    assert glens.search(str(tmp_path / "does_not_exist.jpg"), backend="req") is None


def test_unreadable_image_path_returns_none(tmp_path):
    # A directory exists but can't be read as bytes; search() must swallow the
    # OSError (never-raises contract), not traceback out of the CLI/batch.
    assert glens.search(str(tmp_path), backend="req") is None


def test_req_backend_without_jar_returns_none(tmp_path):
    # No cookie jar and backend="req": returns None without launching a browser.
    result = glens.search(b"not-a-real-image", backend="req",
                          cookie_file=tmp_path / "jar.json")
    assert result is None


def test_req_backend_never_falls_back_to_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(lens_mod, "_search_via_requests", lambda *a: (None, None))

    def explode(*args):
        raise AssertionError("browser backend must not run for backend='req'")

    monkeypatch.setattr(lens_mod, "_search_via_browser", explode)
    assert glens.search(b"img", backend="req", cookie_file=tmp_path / "jar.json") is None


def test_search_assembles_lens_result(monkeypatch, tmp_path):
    monkeypatch.setattr(lens_mod, "_search_via_requests",
                        lambda *a: (SYNTHETIC_ITEMS, RESULTS_URL))
    result = glens.search(b"img", backend="req", cookie_file=tmp_path / "jar.json")

    assert isinstance(result, glens.LensResult)
    assert bool(result)
    assert result.top_title == "Acme Widget Pro Runner"
    assert result.titles == ["Acme Widget Pro Runner", "Different Gadget Deluxe"]
    assert [m.domain for m in result.matches] == ["a.example", "b.example", "c.example"]
    assert result.raw_anchors == SYNTHETIC_ITEMS
    assert result.results_url == RESULTS_URL


def test_auto_falls_back_to_browser(monkeypatch, tmp_path):
    calls = []

    def fake_requests(frame_bytes, cookie_file, *locale):
        calls.append("req")
        return None, None

    def fake_browser(frame_bytes, cookie_file, timeout, driver_factory, *locale):
        calls.append("browser")
        return SYNTHETIC_ITEMS, RESULTS_URL

    monkeypatch.setattr(lens_mod, "_search_via_requests", fake_requests)
    monkeypatch.setattr(lens_mod, "_search_via_browser", fake_browser)
    result = glens.search(b"img", backend="auto", cookie_file=tmp_path / "jar.json",
                          driver_factory=lambda: None)

    assert calls == ["req", "browser"]
    assert result is not None
    assert result.top_title == "Acme Widget Pro Runner"
    assert result.results_url == RESULTS_URL


def test_all_junk_items_returns_none(monkeypatch, tmp_path):
    junk = [{"href": "https://a.example/x", "text": "Opens in new tab"}]
    monkeypatch.setattr(lens_mod, "_search_via_requests", lambda *a: (junk, RESULTS_URL))
    assert glens.search(b"img", backend="req", cookie_file=tmp_path / "jar.json") is None


def test_invalid_backend_raises_value_error():
    with pytest.raises(ValueError):
        glens.search(b"img", backend="warp-drive")


def test_search_many_preserves_order_and_none_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(lens_mod, "_search_via_requests",
                        lambda *a: (SYNTHETIC_ITEMS, RESULTS_URL))
    results = glens.search_many(
        [b"img-one", str(tmp_path / "missing.jpg"), b"img-two"],
        backend="req", cookie_file=tmp_path / "jar.json",
    )
    assert len(results) == 3
    assert results[0] is not None and results[2] is not None
    assert results[1] is None


def test_lens_result_bool():
    assert not glens.LensResult(top_title=None, titles=[], matches=[])
    assert glens.LensResult(top_title="Acme Widget Pro", titles=["Acme Widget Pro"], matches=[])


def test_lens_result_str_is_human_readable():
    result = glens.LensResult(
        top_title="Acme Widget Pro Runner",
        titles=["Acme Widget Pro Runner"],
        matches=[glens.Match(title=f"Product {i} Name", url=f"https://s{i}.example/p",
                             domain=f"s{i}.example") for i in range(7)],
    )
    text = str(result)
    assert "Acme Widget Pro Runner" in text
    assert "s0.example" in text and "s4.example" in text
    assert "s5.example" not in text          # capped at 5 lines
    assert "2 more match(es)" in text


def test_lens_result_to_dict():
    match = glens.Match(title="Acme Widget Pro", url="https://a.example/p", domain="a.example")
    result = glens.LensResult(top_title="Acme Widget Pro", titles=["Acme Widget Pro"],
                              matches=[match], raw_anchors=[{"href": match.url}],
                              results_url="https://www.google.com/search?udm=26")
    data = result.to_dict()
    assert data["top_title"] == "Acme Widget Pro"
    assert data["matches"] == [{"title": "Acme Widget Pro", "url": "https://a.example/p",
                                "domain": "a.example"}]
    assert "raw_anchors" not in data
    assert result.to_dict(raw_anchors=True)["raw_anchors"] == [{"href": match.url}]


def test_explicit_zero_caps_are_honoured(monkeypatch, tmp_path):
    # max_matches=0 / max_titles=0 must not silently fall back to the defaults;
    # with both at zero there's nothing to return, so search() yields None.
    monkeypatch.setattr(lens_mod, "_search_via_requests",
                        lambda *a: (SYNTHETIC_ITEMS, RESULTS_URL))
    result = glens.search(b"img", backend="req", cookie_file=tmp_path / "jar.json",
                          max_matches=0, max_titles=0)
    assert result is None


class FakeDriver:
    """Records the calls _search_via_browser makes on a driver."""

    def __init__(self):
        self.calls = []

    def get(self, url):
        self.calls.append(("get", url))

    def quit(self):
        self.calls.append(("quit", None))


def _wire_browser_internals(monkeypatch, upload_url, harvested):
    # The selenium-touching internals are faked so the suite stays browser-free;
    # what's under test is _search_via_browser's own wiring and tuple contract.
    monkeypatch.setattr(lens_mod, "_dismiss_consent", lambda driver: None)
    monkeypatch.setattr(lens_mod, "_upload_to_google",
                        lambda driver, frame, timeout, endpoint=None: upload_url)
    monkeypatch.setattr(lens_mod, "_render_and_harvest",
                        lambda driver, url, timeout, wait: harvested)
    monkeypatch.setattr(lens_mod, "_save_cookie_jar",
                        lambda driver, cookie_file: None)
    monkeypatch.setattr("glens.driver.wait_for_document_ready", lambda driver: None)


def test_browser_backend_returns_items_and_url(monkeypatch, tmp_path):
    driver = FakeDriver()
    _wire_browser_internals(monkeypatch, RESULTS_URL, SYNTHETIC_ITEMS)

    items, url = lens_mod._search_via_browser(b"img", tmp_path / "jar.json", 5,
                                              lambda: driver)

    assert items == SYNTHETIC_ITEMS
    assert url == RESULTS_URL
    assert driver.calls[0] == ("get", "https://www.google.com/")
    assert driver.calls[-1] == ("quit", None)


def test_browser_backend_failed_upload_returns_none_tuple(monkeypatch, tmp_path):
    driver = FakeDriver()
    _wire_browser_internals(monkeypatch, None, SYNTHETIC_ITEMS)

    items, url = lens_mod._search_via_browser(b"img", tmp_path / "jar.json", 5,
                                              lambda: driver)

    assert items is None and url is None
    assert ("quit", None) in driver.calls


def test_browser_backend_empty_harvest_returns_none_items(monkeypatch, tmp_path):
    driver = FakeDriver()
    _wire_browser_internals(monkeypatch, RESULTS_URL, [])

    items, url = lens_mod._search_via_browser(b"img", tmp_path / "jar.json", 5,
                                              lambda: driver)

    assert items is None
    assert url == RESULTS_URL


RENDERED_ITEMS = [{"href": f"https://s{i}.example/p", "text": f"Product {i} Name Here"}
                  for i in range(12)]


def test_browser_saves_jar_only_from_rendered_pages(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(lens_mod, "_dismiss_consent", lambda driver: None)
    monkeypatch.setattr(lens_mod, "_upload_to_google",
                        lambda driver, frame, timeout, endpoint=None: RESULTS_URL)
    monkeypatch.setattr(lens_mod, "_save_cookie_jar",
                        lambda driver, cookie_file: saved.append(1))
    monkeypatch.setattr("glens.driver.wait_for_document_ready", lambda driver: None)

    # A degraded shell (few anchors) must NOT mint the jar...
    monkeypatch.setattr(lens_mod, "_render_and_harvest",
                        lambda driver, url, timeout, wait: SYNTHETIC_ITEMS)
    lens_mod._search_via_browser(b"img", tmp_path / "jar.json", 5, lambda: FakeDriver())
    assert saved == []

    # ...a really rendered page does.
    monkeypatch.setattr(lens_mod, "_render_and_harvest",
                        lambda driver, url, timeout, wait: RENDERED_ITEMS)
    lens_mod._search_via_browser(b"img", tmp_path / "jar.json", 5, lambda: FakeDriver())
    assert saved == [1]


def test_headless_degraded_page_retries_headed(monkeypatch, tmp_path):
    passes = []
    factories_used = []

    def fake_render(driver, url, timeout, wait):
        passes.append(driver.kind)
        return SYNTHETIC_ITEMS if len(passes) == 1 else RENDERED_ITEMS

    class KindedDriver(FakeDriver):
        def __init__(self, kind):
            super().__init__()
            self.kind = kind

    def fake_build_driver(headless=None, **kwargs):
        factories_used.append(headless)
        return KindedDriver("headed" if headless is False else "headless")

    monkeypatch.setattr(lens_mod, "_dismiss_consent", lambda driver: None)
    monkeypatch.setattr(lens_mod, "_upload_to_google",
                        lambda driver, frame, timeout, endpoint=None: RESULTS_URL)
    monkeypatch.setattr(lens_mod, "_save_cookie_jar", lambda driver, cookie_file: None)
    monkeypatch.setattr(lens_mod, "_render_and_harvest", fake_render)
    monkeypatch.setattr(lens_mod, "_headed_retry_allowed", lambda factory: True)
    monkeypatch.setattr("glens.driver.build_driver", fake_build_driver)
    monkeypatch.setattr("glens.driver.wait_for_document_ready", lambda driver: None)

    items, url = lens_mod._search_via_browser(
        b"img", tmp_path / "jar.json", 5, lambda: KindedDriver("headless"))

    assert passes == ["headless", "headed"]
    assert factories_used == [False]       # retry explicitly asked for a window
    assert items == RENDERED_ITEMS


def test_headed_retry_respects_pinned_env(monkeypatch):
    monkeypatch.setenv("GLENS_HEADLESS", "1")
    assert lens_mod._headed_retry_allowed(lambda: None) is False


def test_headed_retry_only_for_bundled_factory(monkeypatch):
    monkeypatch.delenv("GLENS_HEADLESS", raising=False)
    from glens.driver import build_driver
    assert lens_mod._headed_retry_allowed(build_driver) is True
    assert lens_mod._headed_retry_allowed(lambda: None) is False


def test_browser_backend_driver_factory_failure_is_swallowed(tmp_path, caplog):
    def exploding_factory():
        raise RuntimeError("no Chrome here")

    with caplog.at_level(logging.INFO, logger="glens"):
        items, url = lens_mod._search_via_browser(b"img", tmp_path / "jar.json", 5,
                                                  exploding_factory)
    assert items is None and url is None
    # A user driver_factory blowing up is an ordinary failure, not the loud
    # "extra not installed" diagnostic.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_missing_browser_extra_warns_loudly(tmp_path, caplog):
    def missing_dep_factory():
        raise ImportError("The browser backend needs undetected-chromedriver. "
                          "Install it with:  pip install 'chrome-lens-search[browser]'")

    with caplog.at_level(logging.WARNING, logger="glens"):
        items, url = lens_mod._search_via_browser(b"img", tmp_path / "jar.json", 5,
                                                  missing_dep_factory)
    assert items is None and url is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and "browser backend unavailable" in warnings[0].getMessage()
