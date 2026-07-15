"""Google Lens visual product search over plain HTTP.

Lens is very good at naming the exact product in a photo, but the free Python
Lens libraries only do OCR/translation, and hitting Lens from a plain server just
gets you blocked. This sends the image straight to Google's own Lens upload
endpoint and reads the product matches back.

Every lookup is two steps: POST the image to searchbyimage/upload (the
``encoded_image`` field), which redirects to a results URL carrying ``&udm=26``;
then read that URL and scrape the result anchors. The image only ever touches
Google, never a third-party host.

Two backends (``backend=`` / ``GLENS_BACKEND``, default "auto"):

  req      replays a cookie jar minted earlier by a real browser, over plain
           ``requests``. Google only server-renders the product anchors for a
           JS-verified session, so a cold client gets an "enable JS" shell; with
           the browser's cookies, though, the raw HTML has everything we need. If
           the jar is missing or stale, the browser path runs and re-mints it.
  browser  drives undetected-chromedriver, uploads via an in-page fetch, renders
           the page and scrapes the anchors. Every good run saves the google.com
           cookies + UA back to the jar so req can reuse them.
  auto     req first, browser as a fallback. This is the default.

The parser stays selector-light on purpose. Lens markup uses randomized, rotating
class names, so instead of hardcoding classes we just grab every anchor and mine
its href and label. That's high-recall and a little noisy; filter it downstream
if you need precision.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import logging
import os
import re
import struct
import threading
import time
import zlib
from collections import Counter
from dataclasses import asdict, dataclass, field
from html import unescape
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urljoin, urlparse

import requests

logger = logging.getLogger("glens")


def _env_flag(name: str) -> bool:
    """Truthy env flag ("1", "true", "yes", "on"); anything else is off."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read an int env default; malformed values must never break `import glens`."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("glens: ignoring non-integer %s=%r (using %d).", name, raw, default)
        return default


DEFAULT_BACKEND = os.getenv("GLENS_BACKEND", "auto").strip().lower()
DEFAULT_MAX_MATCHES = _env_int("GLENS_MAX_MATCHES", 20)
DEFAULT_MAX_TITLES = _env_int("GLENS_MAX_TITLES", 6)
DEFAULT_RESULT_TIMEOUT = _env_int("GLENS_RESULT_TIMEOUT", 25, minimum=5)
DEFAULT_LANG = os.getenv("GLENS_LANG", "").strip() or None
DEFAULT_USE_CACHE = _env_flag("GLENS_CACHE")
DEFAULT_CACHE_TTL = _env_int("GLENS_CACHE_TTL", 86400)

_VALID_BACKENDS = ("auto", "req", "browser")
_REQ_ATTEMPTS = 2
_REQ_BACKOFF_SECONDS = 1.5


def _default_cookie_file() -> Path:
    env = os.getenv("GLENS_COOKIE_FILE", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".cache" / "glens" / "cookies.json"


def _default_cache_dir() -> Path:
    env = os.getenv("GLENS_CACHE_DIR", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".cache" / "glens" / "results"


_UPLOAD_ENDPOINT = "https://www.google.com/searchbyimage/upload"
_REQ_UA_FALLBACK = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
# Google serves this shell instead of results when the session isn't JS-verified.
_JS_GATE_MARKER = "/httpservice/retry/enablejs"

# Generic marketplace prefixes Lens prepends in aria-labels (NOT real brands, so
# strip them); brand boutiques like "Marine Layer" are left intact on purpose.
_MARKETPLACE_PREFIX = re.compile(
    r"^(Amazon\.com|www\.[^\s]+|eBay|Walmart|Target|AliExpress|Etsy|SHEIN|Google Shopping)\s+",
    re.IGNORECASE,
)
_JUNK_EXACT = {"read more", "view related links", "opens in new tab", "see more",
               "shop now", "related links", "view product", "product", ""}
_JUNK_CONTAINS = ("new arrivals & restocks", "let us know your")

_lens_lock = threading.Lock()


@dataclass
class Match:
    """One external product/result link harvested from the Lens page."""
    title: str
    url: str
    domain: str


@dataclass
class LensResult:
    """The outcome of one Lens pass.

    ``matches`` are external merchant/result links; ``titles`` are the
    frequency-ranked product names (the exact match repeats across tiles, so it
    rises to the top). ``raw_anchors`` is every anchor we scraped, unfiltered, for
    callers that want to mine it themselves. ``results_url`` is the internal
    Google results URL the anchors came from; it's tied to the browser-minted
    session (it carries a ``vsrid``/``gsessionid``), so it only renders for a
    client replaying that session's cookies. You can't reopen it in a normal
    browser, so keep it for logging, not for sharing.
    """
    top_title: str | None
    titles: list[str]
    matches: list[Match]
    raw_anchors: list[dict] = field(default_factory=list)
    results_url: str | None = None

    def __bool__(self) -> bool:
        return bool(self.matches or self.titles)

    def __str__(self) -> str:
        lines = [f"top title: {self.top_title or '(none)'}"]
        for m in self.matches[:5]:
            lines.append(f"  [{m.domain}] {m.title}")
        hidden = len(self.matches) - 5
        if hidden > 0:
            lines.append(f"  ... and {hidden} more match(es)")
        return "\n".join(lines)

    def to_dict(self, *, raw_anchors: bool = False) -> dict:
        """JSON-ready dict of the result (``raw_anchors=True`` to include them)."""
        data = asdict(self)
        if not raw_anchors:
            data.pop("raw_anchors", None)
        return data


def _is_external(href: str) -> bool:
    h = (href or "").lower()
    return ("://" in h and "google.com" not in h and "gstatic" not in h
            and ".google/" not in h and "google.co" not in h[:40])


def _domain_of(href: str) -> str:
    try:
        return (urlparse(href).netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _clean_title(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    for sep in (" | ", " – ", " — "):  # merchant usually follows; keep the product part
        if sep in t:
            t = t.split(sep)[0].strip()
    t = _MARKETPLACE_PREFIX.sub("", t)
    t = re.sub(r"\b(Opens in new tab|Out of stock|In stock|Read more|View related links)\b.*$",
               "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*\d\.\d\s*\(\d[\d,]*\).*$", "", t)  # trailing rating like 4.7(20)
    t = re.sub(r"\s*[·|].*$", "", t)                    # leftover separators
    return t.strip(" -–—|·.")


def _is_junk_title(t: str) -> bool:
    low = t.lower().strip()
    return low in _JUNK_EXACT or any(j in low for j in _JUNK_CONTAINS)


def _download_image(url: str) -> bytes | None:
    """Fetch an image URL so callers can pass links straight to search()."""
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": _REQ_UA_FALLBACK})
        resp.raise_for_status()
        return resp.content or None
    except requests.RequestException as exc:
        logger.warning("glens: could not download image %s (%s)", url, exc)
        return None


def _tiny_png() -> bytes:
    """A synthetic 16x16 grey PNG (pure stdlib) used by warmup() when the
    caller doesn't supply an image. Not a Google asset; generated locally."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0)   # 16x16, 8-bit RGB
    raw = b"".join(b"\x00" + bytes([120, 120, 120] * 16) for _ in range(16))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# Locale note (tested live, 2026-07): Google rejects hl/gl overrides here.
# Editing the session-signed results URL gets a 403, and so does putting the
# params on the upload request. The only lever that works is the Accept-Language
# header (see _req_session); country/market always follows your IP.


# Opt-in result cache: sha256(image)+lang -> harvested anchors. Shaping
# (max_matches/max_titles) is applied per call, so it never keys the cache.
def _cache_key(frame_bytes: bytes, lang: str | None) -> str:
    digest = hashlib.sha256(frame_bytes)
    digest.update(f"|{lang or ''}".encode())
    return digest.hexdigest()


def _cache_read(key: str, ttl: int) -> tuple[list[dict], str | None] | None:
    try:
        path = _default_cache_dir() / f"{key}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(data.get("saved_at", 0)) > ttl:
            return None
        items = data.get("items")
        if not items:
            return None
        logger.info("glens: result cache hit (%s...)", key[:12])
        return items, data.get("results_url")
    except Exception:
        return None  # unreadable/corrupt cache entries are simply misses


def _cache_write(key: str, items: list[dict], results_url: str | None) -> None:
    try:
        cache_dir = _default_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so concurrent writers can't leave a torn entry.
        tmp = cache_dir / f"{key}.{os.getpid()}.tmp"
        tmp.write_text(
            json.dumps({"saved_at": time.time(), "results_url": results_url, "items": items}),
            encoding="utf-8",
        )
        tmp.replace(cache_dir / f"{key}.json")
    except Exception as exc:
        logger.info("glens: could not write the result cache (%s)", exc)


def _save_cookie_jar(driver, cookie_file: Path) -> None:
    """Persist the browser's google.com cookies + UA so the req backend can reuse
    the JS-verified session. Best-effort; called after every successful browser run."""
    try:
        cookies = [
            {k: c.get(k) for k in ("name", "value", "domain", "path") if c.get(k) is not None}
            for c in driver.get_cookies()
            if "google" in (c.get("domain") or "")
        ]
        if not cookies:
            return
        ua = driver.execute_script("return navigator.userAgent") or _REQ_UA_FALLBACK
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text(
            json.dumps({"user_agent": ua, "cookies": cookies, "minted_at": time.time()}),
            encoding="utf-8",
        )
        logger.info("glens: saved %d google.com cookie(s) for the req backend.", len(cookies))
    except Exception as exc:
        logger.info("glens: could not save the cookie jar (%s).", exc)


def _load_cookie_jar(cookie_file: Path) -> dict | None:
    try:
        if not cookie_file.exists():
            return None
        jar = json.loads(cookie_file.read_text(encoding="utf-8"))
        return jar if jar.get("cookies") else None
    except Exception:
        return None


def _ua_platform(user_agent: str) -> str:
    if "Macintosh" in user_agent:
        return "macOS"
    if "Linux" in user_agent:
        return "Linux"
    return "Windows"


def _req_session(jar: dict, lang: str | None = None) -> requests.Session:
    session = requests.Session()
    user_agent = jar.get("user_agent") or _REQ_UA_FALLBACK
    # Google fingerprints more than cookies: a bare header set gets the JS-gate
    # shell even with a fresh jar, while the browser's full header surface gets
    # the server-rendered results (verified live 2026-07). Mirror Chrome.
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
                  "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": f"{lang},en;q=0.8" if lang else "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
        "Origin": "https://www.google.com",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Upgrade-Insecure-Requests": "1",
    })
    major = re.search(r"Chrome/(\d+)", user_agent)
    if major:
        version = major.group(1)
        session.headers.update({
            "sec-ch-ua": f'"Google Chrome";v="{version}", "Chromium";v="{version}", '
                         f'"Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": f'"{_ua_platform(user_agent)}"',
        })
    for cookie in jar.get("cookies", []):
        try:
            session.cookies.set(
                cookie["name"], cookie["value"],
                domain=cookie.get("domain") or ".google.com",
                path=cookie.get("path") or "/",
            )
        except Exception:
            continue
    # Consent opt-out; harmless if the jar already carries one.
    if "SOCS" not in session.cookies:
        session.cookies.set("SOCS", "CAI", domain=".google.com", path="/")
    return session


_HTML_ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HTML_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_HTML_ARIA_RE = re.compile(r"""aria-label\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


def _unwrap_google_redirect(href: str) -> str:
    """google.com/url?q=<target> -> <target> (raw HTML wraps merchant links)."""
    if "/url?" not in href:
        return href
    try:
        params = parse_qs(urlparse(href).query)
        target = (params.get("q") or params.get("url") or [""])[0]
        if target.startswith("http"):
            return target
    except Exception:
        pass
    return href


def _parse_anchors_from_html(html: str) -> list[dict]:
    """Server-rendered results -> the same [{href, text}] shape the browser
    harvest produces. Selector-light on purpose: anchors + aria-labels only."""
    items: list[dict] = []
    for match in _HTML_ANCHOR_RE.finditer(html):
        attrs, inner = match.group(1), match.group(2)
        href_match = _HTML_HREF_RE.search(attrs)
        if not href_match:
            continue
        href = _unwrap_google_redirect(
            urljoin("https://www.google.com/", unescape(href_match.group(1)))
        )
        aria = _HTML_ARIA_RE.search(attrs)
        text = aria.group(1) if aria else re.sub(r"<[^>]+>", " ", inner)
        text = re.sub(r"\s+", " ", unescape(text)).strip()
        items.append({"href": href, "text": text})
    return items


def _search_via_requests(
    frame_bytes: bytes, cookie_file: Path,
    lang: str | None = None,
) -> tuple[list[dict] | None, str | None]:
    """Both Lens legs over plain requests, riding the browser-minted cookie jar.

    Returns ``(items, results_url)``: the scraped anchor items, or ``(None, ...)``
    when the caller should fall back to the browser (no jar, upload failed, or
    Google served the JS-gate shell, i.e. the jar went stale and a browser run has
    to re-mint it). Transient transport errors get one retry before we give up.
    """
    jar = _load_cookie_jar(cookie_file)
    if jar is None:
        logger.info("glens req backend: no cookie jar yet; using the browser to mint one.")
        return None, None
    session = _req_session(jar, lang=lang)
    last_exc: Exception | None = None
    for attempt in range(1, _REQ_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(_REQ_BACKOFF_SECONDS)
        try:
            resp = session.post(
                _UPLOAD_ENDPOINT,
                files={"encoded_image": ("frame.jpg", frame_bytes, "image/jpeg")},
                data={"sbisrc": "Google Chrome"},
                timeout=30,
                allow_redirects=True,
            )
            resp.raise_for_status()
            results_url = resp.url or ""
            if "udm=26" not in results_url and "lens" not in results_url:
                logger.info("glens req upload didn't yield a results URL (%s); falling back.",
                            results_url[:120])
                return None, None
            html = resp.text
            items = _parse_anchors_from_html(html)
            external = [i for i in items if _is_external(i["href"])]
            # Judge the page by its CONTENT, not the enable-js marker: healthy
            # server-rendered pages carry that noscript link too. Some responses
            # redirect with an empty body and need an explicit GET.
            if len(external) < 3:
                resp = session.get(results_url, timeout=30)
                resp.raise_for_status()
                html = resp.text
                items = _parse_anchors_from_html(html)
                external = [i for i in items if _is_external(i["href"])]
            if len(external) < 3:
                # A verified session always renders merchant tiles; near-zero
                # external anchors means a gated/degraded page, so fall back and
                # let the browser re-mint the jar.
                if _JS_GATE_MARKER in html:
                    logger.info("glens req backend: JS-gate shell served (stale/weak "
                                "jar); falling back to the browser to re-mint it.")
                else:
                    logger.info("glens req backend: only %d external anchor(s); "
                                "falling back.", len(external))
                return None, results_url
            logger.info("glens req backend: harvested %d anchor(s) without a browser.",
                        len(items))
            return items, results_url
        except requests.HTTPError as exc:
            # A hard status (403/429/...) is deterministic, not transient: retrying
            # re-uploads the image to an endpoint that just refused it. Fall back
            # now so the browser can re-mint the jar.
            logger.info("glens req backend: HTTP error (%s); falling back to the browser.", exc)
            return None, None
        except requests.RequestException as exc:
            last_exc = exc
            logger.info("glens req backend attempt %d/%d failed (%s).",
                        attempt, _REQ_ATTEMPTS, exc)
    logger.info("glens req backend gave up after %d attempts (%s); falling back to the browser.",
                _REQ_ATTEMPTS, last_exc)
    return None, None


def _dismiss_consent(driver) -> None:
    """Best-effort click-through of Google's cookie/consent interstitial."""
    from selenium.webdriver.common.by import By
    for xpath in (
        "//button[contains(., 'Accept all')]",
        "//button[contains(., 'I agree')]",
        "//div[@role='button'][contains(., 'Accept all')]",
    ):
        try:
            buttons = driver.find_elements(By.XPATH, xpath)
            if buttons:
                buttons[0].click()
                time.sleep(1.0)
                return
        except Exception:
            continue


def _upload_to_google(driver, frame_bytes: bytes, result_timeout: int,
                      endpoint: str = _UPLOAD_ENDPOINT) -> str | None:
    """UPLOAD step from inside the browser (cookies/session match the page we'll
    render). Returns the ``&udm=26`` results URL, or None."""
    b64 = base64.b64encode(frame_bytes).decode()
    try:
        driver.set_script_timeout(result_timeout + 30)
        result = driver.execute_async_script(
            r"""
            const b64 = arguments[0], endpoint = arguments[1], done = arguments[arguments.length-1];
            try {
                const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
                const fd = new FormData();
                fd.append('encoded_image', new Blob([bytes], {type:'image/jpeg'}), 'frame.jpg');
                fetch(endpoint, {method:'POST', body:fd, redirect:'follow'})
                    .then(r => done({url: r.url, status: r.status}))
                    .catch(e => done({error: String(e)}));
            } catch (e) { done({error: String(e)}); }
            """,
            b64, endpoint,
        )
    except Exception as exc:
        logger.info("glens upload failed: %s", exc)
        return None
    if not result or result.get("error") or not result.get("url"):
        logger.info("glens upload returned no results URL (%s)", result)
        return None
    return result["url"]


def _harvest_anchors(driver) -> list[dict]:
    """Return [{href, text}] for every result anchor via one JS round-trip."""
    try:
        items = driver.execute_script(
            r"""
            const norm = s => (s||'').replace(/\s+/g,' ').trim();
            return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href || '',
                text: norm(a.getAttribute('aria-label') || a.innerText
                           || (a.querySelector('img')||{}).alt)
            })).filter(x => x.href);
            """
        )
    except Exception as exc:
        logger.info("glens anchor harvest failed: %s", exc)
        return []
    return items or []


def _render_and_harvest(driver, results_url: str, result_timeout: int, wait_ready) -> list[dict]:
    """RENDER step: load the results URL and wait for product anchors to appear."""
    from selenium.webdriver.support.ui import WebDriverWait

    driver.get(results_url)
    try:
        wait_ready(driver)
    except Exception:
        pass
    _dismiss_consent(driver)

    def _has_products(drv):
        try:
            return drv.execute_script(
                "return Array.from(document.querySelectorAll('a[href]'))"
                ".filter(a => a.href && a.href.includes('://')"
                " && !a.href.includes('google.com')).length"
            ) > 5
        except Exception:
            return False

    try:
        WebDriverWait(driver, result_timeout).until(_has_products)
    except Exception:
        pass
    time.sleep(2.0)  # let late tiles settle
    return _harvest_anchors(driver)


# A JS-gate/consent shell carries a handful of anchors; a real rendered results
# page carries dozens. Below this, don't trust the page (or its cookies).
_MIN_RENDERED_ANCHORS = 10


def _looks_rendered(items: list[dict] | None) -> bool:
    return bool(items) and len(items) >= _MIN_RENDERED_ANCHORS


def _headed_retry_allowed(driver_factory: Callable) -> bool:
    """Google routinely serves headless Chrome a degraded page. We can retry
    with a visible window, but only for the bundled factory and only when the
    user hasn't pinned GLENS_HEADLESS themselves."""
    if os.getenv("GLENS_HEADLESS") is not None:
        return False
    try:
        from .driver import build_driver
    except Exception:
        return False
    return driver_factory is build_driver


def _browser_pass(frame_bytes: bytes, cookie_file: Path, result_timeout: int,
                  driver_factory: Callable) -> tuple[list[dict] | None, str | None]:
    """One full-browser pass; saves the jar only when the page really rendered."""
    from .driver import wait_for_document_ready

    driver = None
    try:
        driver = driver_factory()
        driver.get("https://www.google.com/")
        try:
            wait_for_document_ready(driver)
        except Exception:
            pass
        _dismiss_consent(driver)
        results_url = _upload_to_google(driver, frame_bytes, result_timeout)
        if not results_url:
            return None, None
        items = _render_and_harvest(driver, results_url, result_timeout, wait_for_document_ready)
        if _looks_rendered(items):
            _save_cookie_jar(driver, cookie_file)
        else:
            logger.info("glens browser: page looks degraded (%d anchor(s)); "
                        "not saving the jar.", len(items or []))
        return (items or None), results_url
    except ImportError as exc:
        # The [browser] extra isn't installed. This is the most common first-run
        # stumble, so surface it even when logging isn't configured. Other
        # failures (a user driver_factory blowing up, say) stay at INFO below.
        logger.warning("glens: browser backend unavailable: %s", exc)
        return None, None
    except Exception as exc:
        logger.info("glens browser lookup failed: %s", exc)
        return None, None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def _search_via_browser(frame_bytes: bytes, cookie_file: Path, result_timeout: int,
                        driver_factory: Callable) -> tuple[list[dict] | None, str | None]:
    """Full-browser flow; also re-mints the req backend's cookie jar.

    Returns ``(items, results_url)``, or ``(None, ...)`` on failure. When headless
    Chrome gets a degraded page (Google gates headless sessions), retries once
    with a visible window (bundled driver only; opt out via GLENS_HEADLESS).
    """
    items, results_url = _browser_pass(frame_bytes, cookie_file, result_timeout,
                                       driver_factory)
    if not _looks_rendered(items) and _headed_retry_allowed(driver_factory):
        logger.warning("glens: headless Chrome got a degraded page; retrying once "
                       "with a visible Chrome window.")
        from .driver import build_driver
        items, results_url = _browser_pass(frame_bytes, cookie_file, result_timeout,
                                           lambda: build_driver(headless=False))
    return items, results_url


def _to_matches(items: list[dict], max_matches: int) -> list[Match]:
    seen: set[tuple[str, str]] = set()
    out: list[Match] = []
    for item in items:
        if len(out) >= max_matches:
            break
        href = item.get("href", "")
        if not _is_external(href):
            continue
        title = _clean_title(item.get("text", ""))
        if not title or _is_junk_title(title):
            continue
        domain = _domain_of(href)
        key = (domain, title.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(Match(title=title[:200], url=href, domain=domain))
    return out


def _to_titles(items: list[dict], max_titles: int) -> list[str]:
    """Frequency-ranked product titles (the exact match repeats across tiles, so it
    rises to the top; ties keep Google's relevance order)."""
    cleaned = []
    for item in items:
        if not _is_external(item.get("href", "")):
            continue
        title = _clean_title(item.get("text", ""))
        if 8 <= len(title) <= 100 and not _is_junk_title(title):
            cleaned.append(title)
    return [title for title, _ in Counter(cleaned).most_common(max_titles)]


def search(
    image: str | os.PathLike | bytes,
    *,
    backend: str | None = None,
    driver_factory: Callable | None = None,
    cookie_file: str | os.PathLike | None = None,
    max_matches: int | None = None,
    max_titles: int | None = None,
    timeout: int | None = None,
    lang: str | None = None,
    use_cache: bool | None = None,
) -> LensResult | None:
    """Run one Google Lens pass on an image.

    Args:
        image: a path to an image file, an http(s) image URL, or raw image bytes.
        backend: "auto" (default), "req", or "browser".
        driver_factory: zero-arg callable returning a Selenium WebDriver, used by
            the browser backend. Defaults to the bundled undetected-chromedriver
            builder (``glens.driver.build_driver``).
        cookie_file: where the browser-minted cookie jar lives. Defaults to
            ``~/.cache/glens/cookies.json`` (or ``$GLENS_COOKIE_FILE``).
        max_matches / max_titles / timeout: per-call overrides for the env defaults.
        lang: preferred response language ("de", "pt-BR"), sent as the fast
            path's Accept-Language header. Best-effort: Google decides, and the
            country/market always follows your IP (hl/gl URL overrides get
            rejected by this endpoint). Env default GLENS_LANG.
        use_cache: reuse recent results for the same image+locale from the local
            result cache (TTL ``GLENS_CACHE_TTL``, default 24h; dir
            ``GLENS_CACHE_DIR``). Defaults to ``GLENS_CACHE`` (off). Never
            consulted for ``backend="browser"``, since an explicit browser run
            also re-mints the cookie jar and a cache hit must not skip that.

    Returns a ``LensResult`` or ``None`` when the lookup fails. Never raises for
    ordinary network/parse failures; an unknown ``backend`` raises ``ValueError``
    (that's a config bug, not a lookup failure).
    """
    backend = (backend or DEFAULT_BACKEND).strip().lower()
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"backend must be one of {_VALID_BACKENDS!r}, not {backend!r}")
    max_matches = DEFAULT_MAX_MATCHES if max_matches is None else max_matches
    max_titles = DEFAULT_MAX_TITLES if max_titles is None else max_titles
    timeout = DEFAULT_RESULT_TIMEOUT if timeout is None else timeout
    lang = lang or DEFAULT_LANG
    use_cache = DEFAULT_USE_CACHE if use_cache is None else use_cache
    cookie_path = Path(cookie_file) if cookie_file else _default_cookie_file()

    if isinstance(image, bytes):
        frame_bytes = image
    elif isinstance(image, str) and image.lower().startswith(("http://", "https://")):
        maybe_bytes = _download_image(image)
        if maybe_bytes is None:
            return None
        frame_bytes = maybe_bytes
    else:
        try:
            frame_bytes = Path(image).read_bytes()
        except OSError as exc:
            # Missing, unreadable, locked, or a directory: an ordinary failure.
            logger.info("glens: could not read image %s (%s)", image, exc)
            return None

    if driver_factory is None:
        from .driver import build_driver
        driver_factory = build_driver

    items: list[dict] | None = None
    results_url: str | None = None
    cache_key = _cache_key(frame_bytes, lang) if use_cache else None
    if cache_key and backend != "browser":
        # An explicit browser run is also how the cookie jar gets re-minted;
        # never let a cache hit silently skip that side effect.
        cached = _cache_read(cache_key, DEFAULT_CACHE_TTL)
        if cached is not None:
            items, results_url = cached

    if items is None:
        with _lens_lock:
            if backend in ("auto", "req"):
                items, results_url = _search_via_requests(frame_bytes, cookie_path, lang)
            if items is None and backend in ("auto", "browser"):
                items, results_url = _search_via_browser(frame_bytes, cookie_path, timeout,
                                                         driver_factory)
        if items and cache_key:
            _cache_write(cache_key, items, results_url)

    if not items:
        return None

    matches = _to_matches(items, max_matches)
    titles = _to_titles(items, max_titles)
    if not matches and not titles:
        return None

    logger.info("glens: %d title(s), %d match(es)", len(titles), len(matches))
    return LensResult(
        top_title=titles[0] if titles else None,
        titles=titles,
        matches=matches,
        raw_anchors=items,
        results_url=results_url,
    )


def search_many(
    images: list[str | os.PathLike | bytes],
    **kwargs,
) -> list[LensResult | None]:
    """Run :func:`search` over several images, preserving order.

    Keyword arguments are forwarded to each ``search()`` call. Lookups serialise
    through the module lock (be gentle, see the README); a failed lookup yields a
    ``None`` entry instead of aborting the whole batch.
    """
    return [search(image, **kwargs) for image in images]


def status(cookie_file: str | os.PathLike | None = None) -> dict:
    """Health check for automation: is the fast HTTP path ready right now?

    Returns a plain dict (JSON-safe)::

        {
          "cookie_file": str,            # jar location being checked
          "jar_valid": bool,             # a loadable jar with cookies exists
          "jar_age_seconds": float|None, # since the jar was minted
          "browser_extra_installed": bool,
          "req_ready": bool,             # fast path available without Chrome
          "default_backend": str,
          "cache_enabled": bool,
        }

    Typical startup assertion: ``if not glens.status()["req_ready"]:
    glens.warmup()``. Note a *stale* jar can only be detected by a live lookup;
    ``jar_valid`` means present and well-formed.
    """
    cookie_path = Path(cookie_file) if cookie_file else _default_cookie_file()
    jar = _load_cookie_jar(cookie_path)
    age: float | None = None
    if jar is not None:
        minted_at = jar.get("minted_at")
        if minted_at is not None:
            try:
                age = max(0.0, time.time() - float(minted_at))
            except (TypeError, ValueError):
                age = None
    return {
        "cookie_file": str(cookie_path),
        "jar_valid": jar is not None,
        "jar_age_seconds": age,
        "browser_extra_installed":
            importlib.util.find_spec("undetected_chromedriver") is not None,
        "req_ready": jar is not None,
        "default_backend": DEFAULT_BACKEND,
        "cache_enabled": DEFAULT_USE_CACHE,
    }


def warmup(
    image: str | os.PathLike | bytes | None = None,
    *,
    cookie_file: str | os.PathLike | None = None,
    driver_factory: Callable | None = None,
    timeout: int | None = None,
    force: bool = False,
) -> bool:
    """Mint (or refresh) the session jar with one browser run, so subsequent
    lookups run over plain HTTP. The deploy-time counterpart of ``search()``:
    call it at container start / CI setup, then use ``backend="req"`` at runtime.

    Needs the ``[browser]`` extra. No-op (returns True) when a valid jar already
    exists, unless ``force=True``. ``image`` defaults to a tiny synthetic PNG;
    the upload content doesn't matter, only the JS-verified session it creates.

    Returns True when the jar is ready. Never raises for ordinary failures.
    """
    cookie_path = Path(cookie_file) if cookie_file else _default_cookie_file()
    if not force and _load_cookie_jar(cookie_path) is not None:
        return True

    if image is None:
        frame_bytes = _tiny_png()
    elif isinstance(image, bytes):
        frame_bytes = image
    elif isinstance(image, str) and image.lower().startswith(("http://", "https://")):
        maybe = _download_image(image)
        if maybe is None:
            return False
        frame_bytes = maybe
    else:
        try:
            frame_bytes = Path(image).read_bytes()
        except OSError as exc:
            logger.warning("glens: warmup could not read image %s (%s)", image, exc)
            return False

    if driver_factory is None:
        from .driver import build_driver
        driver_factory = build_driver

    # A junk image renders few tiles, so there's no point waiting long: any
    # rendered results page mints the cookies we're after.
    with _lens_lock:
        _search_via_browser(frame_bytes, cookie_path, timeout or 10, driver_factory)
    return _load_cookie_jar(cookie_path) is not None
