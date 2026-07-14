"""search() accepts http(s) image URLs; download is faked, no network."""

import requests

import glens
import glens.lens as lens_mod

SYNTHETIC_ITEMS = [
    {"href": "https://a.example/product/1", "text": "Acme Widget Pro Runner"},
    {"href": "https://b.example/p/2", "text": "Acme Widget Pro Runner"},
]
RESULTS_URL = "https://www.google.com/search?udm=26&synthetic=1"


def test_url_image_is_downloaded_and_searched(monkeypatch, tmp_path):
    downloads = []

    def fake_download(url):
        downloads.append(url)
        return b"synthetic-image-bytes"

    searched = {}

    def fake_req(frame_bytes, cookie_file, *locale):
        searched["bytes"] = frame_bytes
        return SYNTHETIC_ITEMS, RESULTS_URL

    monkeypatch.setattr(lens_mod, "_download_image", fake_download)
    monkeypatch.setattr(lens_mod, "_search_via_requests", fake_req)

    result = glens.search("https://example.com/photo.jpg", backend="req",
                          cookie_file=tmp_path / "jar.json")

    assert downloads == ["https://example.com/photo.jpg"]
    assert searched["bytes"] == b"synthetic-image-bytes"
    assert result is not None and result.top_title == "Acme Widget Pro Runner"


def test_failed_download_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(lens_mod, "_download_image", lambda url: None)
    result = glens.search("https://example.com/gone.jpg", backend="req",
                          cookie_file=tmp_path / "jar.json")
    assert result is None


def test_non_url_string_never_hits_downloader(monkeypatch, tmp_path):
    # No scheme -> path handling; the downloader must not be consulted at all
    # (a mis-routed path would otherwise fail "successfully" with None).
    def explode(url):
        raise AssertionError("plain paths must not be downloaded")

    monkeypatch.setattr(lens_mod, "_download_image", explode)
    assert glens.search(str(tmp_path / "photo.jpg"), backend="req") is None


class _FakeHTTP:
    def __init__(self, content=b"img-bytes", status=200, exc=None):
        self.content = content
        self.status = status
        self.exc = exc

    def get(self, url, **kwargs):
        if self.exc:
            raise self.exc
        return self

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"synthetic {self.status}")


def test_download_image_returns_bytes(monkeypatch):
    monkeypatch.setattr(lens_mod.requests, "get", _FakeHTTP(content=b"jpeg!").get)
    assert lens_mod._download_image("https://example.com/a.jpg") == b"jpeg!"


def test_download_image_swallows_http_error(monkeypatch):
    monkeypatch.setattr(lens_mod.requests, "get", _FakeHTTP(status=404).get)
    assert lens_mod._download_image("https://example.com/a.jpg") is None


def test_download_image_swallows_transport_error(monkeypatch):
    monkeypatch.setattr(lens_mod.requests, "get",
                        _FakeHTTP(exc=requests.ConnectionError("boom")).get)
    assert lens_mod._download_image("https://example.com/a.jpg") is None


def test_download_image_empty_body_is_none(monkeypatch):
    monkeypatch.setattr(lens_mod.requests, "get", _FakeHTTP(content=b"").get)
    assert lens_mod._download_image("https://example.com/a.jpg") is None
