"""glens.driver helpers; no selenium/undetected-chromedriver needed."""

from glens.driver import _env_flag, _parse_browser_major


def test_parse_browser_major_from_mismatch_message():
    message = ("session not created: This version of ChromeDriver only supports "
               "Chrome version 150\nCurrent browser version is 149.0.7827.201")
    assert _parse_browser_major(message) == 149


def test_parse_browser_major_absent():
    assert _parse_browser_major("some unrelated chrome crash") is None


def test_env_flag(monkeypatch):
    monkeypatch.setenv("GLENS_TEST_HEADLESS", "0")
    assert _env_flag("GLENS_TEST_HEADLESS", True) is False
    monkeypatch.setenv("GLENS_TEST_HEADLESS", "yes")
    assert _env_flag("GLENS_TEST_HEADLESS", False) is True
    monkeypatch.delenv("GLENS_TEST_HEADLESS", raising=False)
    assert _env_flag("GLENS_TEST_HEADLESS", True) is True
