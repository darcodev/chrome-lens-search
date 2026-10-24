"""warmup()/status() automation helpers, fake browser, no network."""

import json
import logging
import time
import zlib

import pytest

import glens
import glens.lens as lens_mod
from glens.lens import _tiny_png

SYNTHETIC_ITEMS = [{"href": "https://a.example/1", "text": "Acme Widget Pro Runner"}]
RESULTS_URL = "https://www.google.com/search?udm=26&synthetic=1"


def _write_valid_jar(path, minted_at=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "user_agent": "synthetic-agent/1.0",
        "cookies": [{"name": "TEST", "value": "1", "domain": ".google.com", "path": "/"}],
        "minted_at": time.time() if minted_at is None else minted_at,
    }), encoding="utf-8")
    return path


def _minting_browser(jar_path, calls):
    """A fake _search_via_browser that mints the jar like the real one would."""
    def fake(frame_bytes, cookie_file, timeout, driver_factory, *locale):
        calls.append(frame_bytes)
        _write_valid_jar(jar_path)
        return SYNTHETIC_ITEMS, RESULTS_URL
    return fake


# ---------------------------------------------------------------------------
# _tiny_png
# ---------------------------------------------------------------------------

def test_tiny_png_is_a_valid_png():
    data = _tiny_png()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IHDR" in data and b"IDAT" in data and data.endswith(b"IEND\xaeB`\x82")
    # The IDAT payload must decompress to 16 rows of filter byte + 16 RGB px.
    idat_start = data.index(b"IDAT") + 4
    idat_len_pos = data.index(b"IDAT") - 4
    length = int.from_bytes(data[idat_len_pos:idat_len_pos + 4], "big")
    assert len(zlib.decompress(data[idat_start:idat_start + length])) == 16 * (1 + 16 * 3)


# ---------------------------------------------------------------------------
# warmup()
# ---------------------------------------------------------------------------

def test_warmup_short_circuits_on_valid_jar(monkeypatch, tmp_path):
    jar = _write_valid_jar(tmp_path / "jar.json")

    def explode(*args):
        raise AssertionError("browser must not run when the jar is valid")

    monkeypatch.setattr(lens_mod, "_search_via_browser", explode)
    assert glens.warmup(cookie_file=jar) is True


def test_warmup_mints_jar_with_synthetic_image(monkeypatch, tmp_path):
    jar = tmp_path / "jar.json"
    calls = []
    monkeypatch.setattr(lens_mod, "_search_via_browser", _minting_browser(jar, calls))

    assert glens.warmup(cookie_file=jar, driver_factory=lambda: None) is True
    assert len(calls) == 1
    assert calls[0].startswith(b"\x89PNG")     # the built-in synthetic image


def test_warmup_force_reminints(monkeypatch, tmp_path):
    # Seeded a day old, which is why you'd force a re-mint in the first place --
    # and it keeps "was the jar replaced?" independent of the clock resolution.
    jar = _write_valid_jar(tmp_path / "jar.json", minted_at=time.time() - 86400)
    calls = []
    monkeypatch.setattr(lens_mod, "_search_via_browser", _minting_browser(jar, calls))

    assert glens.warmup(cookie_file=jar, force=True, driver_factory=lambda: None) is True
    assert len(calls) == 1
    assert json.loads(jar.read_text(encoding="utf-8"))["minted_at"] > time.time() - 60


def test_warmup_force_reports_failure_when_remint_fails(monkeypatch, tmp_path, caplog):
    # The stale jar is still on disk after a failed browser run, so a bare
    # "does a jar exist?" check reads as success. force=True asked for a *fresh*
    # session; saying yes here tells a deploy/CI check the fast path was
    # refreshed when it wasn't, and every later lookup rides the dead jar.
    jar = _write_valid_jar(tmp_path / "jar.json", minted_at=time.time() - 86400)
    monkeypatch.setattr(lens_mod, "_search_via_browser", lambda *a: (None, None))

    with caplog.at_level(logging.WARNING, logger="glens"):
        ok = glens.warmup(cookie_file=jar, force=True, driver_factory=lambda: None)

    assert ok is False
    assert any("could not re-mint" in r.getMessage() for r in caplog.records)
    # The old jar is left alone: a dead session still beats no session.
    assert json.loads(jar.read_text(encoding="utf-8"))["minted_at"] < time.time() - 3600


def test_warmup_without_force_still_reports_a_usable_jar(monkeypatch, tmp_path):
    # Not forcing means "make sure the fast path is ready"; an existing jar
    # short-circuits before the browser ever runs.
    jar = _write_valid_jar(tmp_path / "jar.json", minted_at=time.time() - 86400)
    monkeypatch.setattr(lens_mod, "_search_via_browser", lambda *a: (None, None))
    assert glens.warmup(cookie_file=jar, driver_factory=lambda: None) is True


def test_warmup_uses_supplied_image(monkeypatch, tmp_path):
    jar = tmp_path / "jar.json"
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"real-image-bytes")
    calls = []
    monkeypatch.setattr(lens_mod, "_search_via_browser", _minting_browser(jar, calls))

    assert glens.warmup(str(photo), cookie_file=jar, driver_factory=lambda: None) is True
    assert calls[0] == b"real-image-bytes"


def test_warmup_returns_false_when_browser_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(lens_mod, "_search_via_browser", lambda *a: (None, None))
    assert glens.warmup(cookie_file=tmp_path / "jar.json",
                        driver_factory=lambda: None) is False


def test_warmup_unreadable_image_returns_false(tmp_path):
    assert glens.warmup(str(tmp_path / "missing.jpg"),
                        cookie_file=tmp_path / "jar.json") is False


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

def test_status_without_jar(tmp_path):
    info = glens.status(cookie_file=tmp_path / "nope.json")
    assert info["jar_valid"] is False
    assert info["req_ready"] is False
    assert info["jar_age_seconds"] is None
    assert isinstance(info["browser_extra_installed"], bool)
    assert info["default_backend"] in ("auto", "req", "browser")


def test_status_with_jar_reports_age(tmp_path):
    jar = _write_valid_jar(tmp_path / "jar.json", minted_at=time.time() - 120)
    info = glens.status(cookie_file=jar)
    assert info["jar_valid"] is True and info["req_ready"] is True
    assert 100 < info["jar_age_seconds"] < 600


def test_status_jar_without_minted_at_reports_none_age(tmp_path):
    # A jar copied from another machine (or an older build) may lack "minted_at";
    # its age is unknown, so status() must report None, not a bogus ~55-year age.
    jar = tmp_path / "jar.json"
    jar.write_text(json.dumps({
        "user_agent": "synthetic-agent/1.0",
        "cookies": [{"name": "TEST", "value": "1", "domain": ".google.com", "path": "/"}],
    }), encoding="utf-8")
    info = glens.status(cookie_file=jar)
    assert info["jar_valid"] is True and info["req_ready"] is True
    assert info["jar_age_seconds"] is None


def test_status_is_json_safe(tmp_path):
    json.dumps(glens.status(cookie_file=tmp_path / "nope.json"))


@pytest.mark.parametrize("invalid", [
    {"cookies": 42},
    {"cookies": "not a cookie list"},
    {"cookies": [None]},
    {"cookies": [{"value": "1"}]},
    {"cookies": [{"name": "TEST", "value": 123}]},
    {"cookies": [{"name": "TEST", "value": "1", "domain": []}]},
    {"user_agent": ["not a user agent"]},
])
def test_malformed_cookie_jar_is_not_ready_or_used(tmp_path, invalid, monkeypatch):
    jar = _write_valid_jar(tmp_path / "jar.json")
    data = json.loads(jar.read_text(encoding="utf-8"))
    data.update(invalid)
    jar.write_text(json.dumps(data), encoding="utf-8")

    def no_network(*args, **kwargs):
        raise AssertionError("malformed jars must be rejected before creating a session")

    monkeypatch.setattr(lens_mod, "_req_session", no_network)
    assert glens.status(cookie_file=jar)["req_ready"] is False
    assert glens.search(b"synthetic-image", backend="req", cookie_file=jar) is None


# ---------------------------------------------------------------------------
# _save_cookie_jar
# ---------------------------------------------------------------------------

class _FakeCookieDriver:
    def __init__(self, cookies):
        self._cookies = cookies

    def get_cookies(self):
        return self._cookies

    def execute_script(self, _script):
        return "synthetic-agent/1.0"


def test_save_cookie_jar_keeps_only_real_google_domains(tmp_path):
    # A substring scan for "google" also swept up cookies set by unrelated hosts
    # that merely contain the word, writing a third party's cookies into our jar.
    driver = _FakeCookieDriver([
        {"name": "SID", "value": "1", "domain": ".google.com", "path": "/"},
        {"name": "NID", "value": "2", "domain": "www.google.co.uk", "path": "/"},
        {"name": "spoof", "value": "3", "domain": "notgoogle.com", "path": "/"},
        {"name": "spoof2", "value": "4", "domain": "google.com.cheap.example", "path": "/"},
    ])
    jar_path = tmp_path / "jar.json"
    lens_mod._save_cookie_jar(driver, jar_path)

    saved = json.loads(jar_path.read_text(encoding="utf-8"))
    assert [c["name"] for c in saved["cookies"]] == ["SID", "NID"]


def test_save_cookie_jar_leaves_no_stray_temp_file(tmp_path):
    driver = _FakeCookieDriver(
        [{"name": "SID", "value": "1", "domain": ".google.com", "path": "/"}]
    )
    jar_path = tmp_path / "jar.json"
    lens_mod._save_cookie_jar(driver, jar_path)

    assert lens_mod._load_cookie_jar(jar_path) is not None
    assert [p.name for p in tmp_path.iterdir()] == ["jar.json"]


def test_save_cookie_jar_never_truncates_a_good_jar_on_failure(tmp_path, monkeypatch):
    # Writing in place meant a crash or a full disk mid-write left an unloadable
    # half-jar behind, taking out the fast path the run meant to refresh.
    jar_path = _write_valid_jar(tmp_path / "jar.json", minted_at=1000.0)
    before = jar_path.read_text(encoding="utf-8")

    def explode(self, *args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(lens_mod.Path, "write_text", explode)
    lens_mod._save_cookie_jar(
        _FakeCookieDriver([{"name": "SID", "value": "1", "domain": ".google.com", "path": "/"}]),
        jar_path,
    )

    assert jar_path.read_text(encoding="utf-8") == before
    assert lens_mod._load_cookie_jar(jar_path) is not None


def test_save_cookie_jar_no_google_cookies_writes_nothing(tmp_path):
    jar_path = tmp_path / "jar.json"
    lens_mod._save_cookie_jar(
        _FakeCookieDriver([{"name": "x", "value": "1", "domain": "example.com", "path": "/"}]),
        jar_path,
    )
    assert not jar_path.exists()
