"""Standalone undetected-chromedriver builder for the browser backend.

You only need a browser to:
  * run the ``browser`` backend directly, or
  * cold-start the ``req`` backend by minting a JS-verified google.com cookie jar
    (after that, subsequent lookups ride the jar over plain ``requests``).

Bring your own driver if you already manage Chrome: pass any zero-arg callable as
``search(..., driver_factory=my_factory)``. It must return a Selenium-compatible
WebDriver; the library calls ``.get``, ``.execute_script``,
``.execute_async_script``, ``.set_script_timeout``, ``.find_elements``,
``.get_cookies`` and ``.quit`` on it.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("glens")

_BROWSER_VERSION_RE = re.compile(r"Current browser version is (\d+)")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _parse_browser_major(message: str) -> int | None:
    """Extract the installed Chrome major version from a chromedriver
    version-mismatch error message ("Current browser version is 149.0...")."""
    match = _BROWSER_VERSION_RE.search(message)
    return int(match.group(1)) if match else None


def build_driver(*, headless: bool | None = None, user_data_dir: str | os.PathLike | None = None):
    """Build a stealthy Chrome via undetected-chromedriver.

    A persistent ``user_data_dir`` is recommended: it lets the JS-verified session
    survive across runs, so the cookie jar stays fresh and the fast ``req`` path
    keeps working without re-launching Chrome. Set ``GLENS_CHROME_PROFILE`` to pick
    one up from the environment.
    """
    try:
        import undetected_chromedriver as uc
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError(
            "The browser backend needs undetected-chromedriver. "
            "Install it with:  pip install 'chrome-lens-search[browser]'"
        ) from exc

    if headless is None:
        headless = _env_flag("GLENS_HEADLESS", True)
    if user_data_dir is None:
        env_profile = os.getenv("GLENS_CHROME_PROFILE", "").strip()
        user_data_dir = env_profile or None

    def make_options():  # uc mutates options; each attempt needs a fresh object
        options = uc.ChromeOptions()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--lang=en-US")
        options.add_argument("--window-size=1280,2000")
        if headless:
            options.add_argument("--headless=new")
        if user_data_dir:
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            options.add_argument(f"--user-data-dir={user_data_dir}")
        return options

    try:
        driver = uc.Chrome(options=make_options())
    except Exception as exc:
        # The classic cold-start failure: uc fetched a chromedriver one major
        # version ahead of the installed Chrome. The error names the installed
        # version, so retry pinned to it.
        major = _parse_browser_major(str(exc))
        if major is None:
            raise
        logger.warning("glens: chromedriver/Chrome version mismatch; retrying "
                       "pinned to Chrome %d.", major)
        driver = uc.Chrome(options=make_options(), version_main=major)
    driver.set_page_load_timeout(60)
    return driver


def wait_for_document_ready(driver, timeout: float = 30) -> None:
    """Block until the page reports an interactive/complete readyState."""
    from selenium.webdriver.support.ui import WebDriverWait

    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
    )
