"""Opt-in result cache: backend monkeypatched, cache dir under tmp_path."""

import json
import time

import pytest

import glens
import glens.lens as lens_mod
from glens.lens import _cache_key

SYNTHETIC_ITEMS = [
    {"href": "https://a.example/product/1", "text": "Acme Widget Pro Runner"},
    {"href": "https://b.example/p/2", "text": "Acme Widget Pro Runner"},
    {"href": "https://c.example/p/3", "text": "Different Gadget Deluxe"},
]
RESULTS_URL = "https://www.google.com/search?udm=26&synthetic=1"


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    directory = tmp_path / "results-cache"
    monkeypatch.setenv("GLENS_CACHE_DIR", str(directory))
    return directory


def _search(monkeypatch, tmp_path, backend_fn, **kwargs):
    monkeypatch.setattr(lens_mod, "_search_via_requests", backend_fn)
    return glens.search(b"synthetic-image", backend="req",
                        cookie_file=tmp_path / "jar.json", **kwargs)


def _hit_backend(counter):
    def fake(frame_bytes, cookie_file, *locale):
        counter.append(1)
        return SYNTHETIC_ITEMS, RESULTS_URL
    return fake


def test_cache_key_depends_on_image_and_lang():
    base = _cache_key(b"image-a", None)
    assert base == _cache_key(b"image-a", None)
    assert base != _cache_key(b"image-b", None)
    assert base != _cache_key(b"image-a", "de")
    assert _cache_key(b"image-a", "de") != _cache_key(b"image-a", "fr")


def test_second_lookup_served_from_cache(monkeypatch, tmp_path, cache_dir):
    calls = []
    first = _search(monkeypatch, tmp_path, _hit_backend(calls), use_cache=True)
    assert first is not None and len(calls) == 1
    assert list(cache_dir.glob("*.json")), "cache entry should be written"

    def explode(*args):
        raise AssertionError("backend must not run on a cache hit")

    second = _search(monkeypatch, tmp_path, explode, use_cache=True)
    assert second is not None
    assert second.top_title == first.top_title
    assert second.results_url == RESULTS_URL
    assert second.raw_anchors == SYNTHETIC_ITEMS


def test_cache_off_by_default(monkeypatch, tmp_path, cache_dir):
    calls = []
    _search(monkeypatch, tmp_path, _hit_backend(calls))
    assert not list(cache_dir.glob("*.json"))

    _search(monkeypatch, tmp_path, _hit_backend(calls))
    assert len(calls) == 2                     # both lookups hit the backend


def test_failed_lookup_not_cached(monkeypatch, tmp_path, cache_dir):
    result = _search(monkeypatch, tmp_path, lambda *a: (None, None), use_cache=True)
    assert result is None
    assert not list(cache_dir.glob("*.json"))


def test_expired_entry_is_a_miss(monkeypatch, tmp_path, cache_dir):
    calls = []
    _search(monkeypatch, tmp_path, _hit_backend(calls), use_cache=True)
    (entry,) = cache_dir.glob("*.json")
    data = json.loads(entry.read_text(encoding="utf-8"))
    data["saved_at"] = time.time() - (lens_mod.DEFAULT_CACHE_TTL + 60)
    entry.write_text(json.dumps(data), encoding="utf-8")

    _search(monkeypatch, tmp_path, _hit_backend(calls), use_cache=True)
    assert len(calls) == 2                     # stale entry ignored


def test_corrupt_entry_is_a_miss(monkeypatch, tmp_path, cache_dir):
    calls = []
    _search(monkeypatch, tmp_path, _hit_backend(calls), use_cache=True)
    (entry,) = cache_dir.glob("*.json")
    entry.write_text("not json{", encoding="utf-8")

    result = _search(monkeypatch, tmp_path, _hit_backend(calls), use_cache=True)
    assert result is not None
    assert len(calls) == 2


def test_locale_keys_cache_separately(monkeypatch, tmp_path, cache_dir):
    calls = []
    _search(monkeypatch, tmp_path, _hit_backend(calls), use_cache=True, lang="de")
    _search(monkeypatch, tmp_path, _hit_backend(calls), use_cache=True, lang="fr")
    assert len(calls) == 2
    assert len(list(cache_dir.glob("*.json"))) == 2


def test_env_default_enables_cache(monkeypatch, tmp_path, cache_dir):
    # GLENS_CACHE=1 (parsed into DEFAULT_USE_CACHE) must apply when the caller
    # doesn't pass use_cache at all.
    monkeypatch.setattr(lens_mod, "DEFAULT_USE_CACHE", True)
    calls = []
    _search(monkeypatch, tmp_path, _hit_backend(calls))
    assert list(cache_dir.glob("*.json"))


def test_explicit_false_overrides_env_default(monkeypatch, tmp_path, cache_dir):
    monkeypatch.setattr(lens_mod, "DEFAULT_USE_CACHE", True)
    calls = []
    _search(monkeypatch, tmp_path, _hit_backend(calls), use_cache=False)
    assert not list(cache_dir.glob("*.json"))


def test_browser_backend_bypasses_cache_read(monkeypatch, tmp_path, cache_dir):
    # Seed the cache via the req backend...
    calls = []
    _search(monkeypatch, tmp_path, _hit_backend(calls), use_cache=True)
    assert list(cache_dir.glob("*.json"))

    # ...then an explicit browser run must still hit the browser (it re-mints
    # the cookie jar; a cache hit must not skip that side effect).
    browser_calls = []

    def fake_browser(frame_bytes, cookie_file, timeout, driver_factory, *locale):
        browser_calls.append(1)
        return SYNTHETIC_ITEMS, RESULTS_URL

    monkeypatch.setattr(lens_mod, "_search_via_browser", fake_browser)
    result = glens.search(b"synthetic-image", backend="browser",
                          cookie_file=tmp_path / "jar.json", use_cache=True,
                          driver_factory=lambda: None)

    assert browser_calls == [1]
    assert result is not None


def test_cache_write_leaves_no_tmp_residue(monkeypatch, tmp_path, cache_dir):
    calls = []
    _search(monkeypatch, tmp_path, _hit_backend(calls), use_cache=True)
    assert not list(cache_dir.glob("*.tmp"))
    assert len(list(cache_dir.glob("*.json"))) == 1


def test_shaping_applies_to_cached_items(monkeypatch, tmp_path, cache_dir):
    calls = []
    _search(monkeypatch, tmp_path, _hit_backend(calls), use_cache=True)

    def explode(*args):
        raise AssertionError("backend must not run on a cache hit")

    capped = _search(monkeypatch, tmp_path, explode, use_cache=True, max_matches=1)
    assert capped is not None
    assert len(capped.matches) == 1            # per-call shaping, not baked in
