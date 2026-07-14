# glens — API Reference

Complete reference for the `glens` package (PyPI: `chrome-lens-search`). For concepts, tutorials, and deployment recipes, see the [Guide](GUIDE.md).

- Python ≥ 3.10.
- Core imports depend only on `requests`. The browser backend (`selenium` + `undetected-chromedriver`) is imported **lazily** and only needed to mint/refresh the session.
- Nothing in the public API raises for ordinary network/parse failures — functions return `None` / `False` / empty instead. The documented exceptions are called out per function.

---

## Table of contents

- [Package overview](#package-overview)
- [`search()`](#search)
- [`search_many()`](#search_many)
- [`warmup()`](#warmup)
- [`status()`](#status)
- [`LensResult`](#lensresult)
- [`Match`](#match)
- [`glens.driver.build_driver()`](#glensdriverbuild_driver)
- [`glens.driver.wait_for_document_ready()`](#glensdriverwait_for_document_ready)
- [Environment variables](#environment-variables)
- [CLI reference](#cli-reference)
- [Logging](#logging)
- [Error & return semantics](#error--return-semantics)
- [On-disk file formats](#on-disk-file-formats)

---

## Package overview

```python
import glens

glens.search        # run one Lens lookup
glens.search_many   # run several, preserving order
glens.warmup        # mint/refresh the session jar (needs [browser])
glens.status        # health check: is the fast path ready?
glens.LensResult    # result dataclass
glens.Match         # one result link
glens.__version__   # str, e.g. "0.1.0"
```

`__all__` is `["search", "search_many", "warmup", "status", "LensResult", "Match"]`.

Importing `glens` requires only `requests`; it does **not** import `selenium`/`undetected-chromedriver`.

Two additional public helpers live in the `glens.driver` submodule (`build_driver`, `wait_for_document_ready`) for callers who want to customize or reuse the Chrome builder.

---

## `search()`

```python
glens.search(
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
) -> LensResult | None
```

Run one Google Lens pass on an image.

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `image` | `str \| os.PathLike \| bytes` | *required* | A filesystem path, an `http(s)` image URL (must be a `str`), or raw image `bytes`. |
| `backend` | `str \| None` | `None` → `GLENS_BACKEND` → `"auto"` | One of `"auto"`, `"req"`, `"browser"`. |
| `driver_factory` | `Callable \| None` | `None` → `glens.driver.build_driver` | Zero-arg callable returning a Selenium-compatible WebDriver, used by the browser backend. |
| `cookie_file` | `str \| os.PathLike \| None` | `None` → `GLENS_COOKIE_FILE` → `~/.cache/glens/cookies.json` | Location of the session cookie jar. |
| `max_matches` | `int \| None` | `None` → `GLENS_MAX_MATCHES` → `20` | Maximum `LensResult.matches` returned. |
| `max_titles` | `int \| None` | `None` → `GLENS_MAX_TITLES` → `6` | Maximum `LensResult.titles` returned. |
| `timeout` | `int \| None` | `None` → `GLENS_RESULT_TIMEOUT` → `25` | Seconds the **browser** backend waits for result tiles to render. |
| `lang` | `str \| None` | `None` → `GLENS_LANG` → *(none)* | Preferred response language, sent as `Accept-Language` on the fast path. Best-effort. Part of the cache key. |
| `use_cache` | `bool \| None` | `None` → `GLENS_CACHE` → `False` | Read/write the local result cache. |

`0` is a meaningful value for `max_matches`/`max_titles`/`timeout` and is honored (it is **not** treated as "unset"); only `None` falls through to the env/default.

### Returns

`LensResult` on success, or `None` when the lookup fails or nothing matched. `None` is returned (never an exception) for: a missing/unreadable image path, a URL that couldn't be downloaded, a stale/missing jar under `backend="req"`, network errors, and pages that yielded no usable matches or titles.

### Raises

- `ValueError` — if `backend` is not one of `"auto"`, `"req"`, `"browser"`. (A configuration bug, not a lookup failure.)

### Image input handling

- **`bytes`** → used directly.
- **`str` starting with `http://` or `https://`** → downloaded via `requests` (30 s timeout); a failed download returns `None`.
- **anything else** (`str` path, `os.PathLike`) → read from disk; an `OSError` (missing, unreadable, is-a-directory, locked) returns `None`.

> A URL passed as a `pathlib.Path` is treated as a path, not a URL — pass URLs as plain strings.

### Backend dispatch

- `"auto"`: try `req`; if it returns nothing, fall back to `browser` (which re-mints the jar).
- `"req"`: `req` only; returns `None` if the jar is missing/stale.
- `"browser"`: `browser` only; always re-mints the jar; **never reads** the result cache (but does write it).

### Examples

```python
import glens

# Path, URL, or bytes
glens.search("sneaker.jpg")
glens.search("https://example.com/sneaker.jpg")
glens.search(open("sneaker.jpg", "rb").read())

# Fast path only, custom caps, German preference, with caching
glens.search("s.jpg", backend="req", max_matches=5, lang="de", use_cache=True)

# Custom jar location
glens.search("s.jpg", cookie_file="/opt/app/jar.json")
```

---

## `search_many()`

```python
glens.search_many(
    images: list[str | os.PathLike | bytes],
    **kwargs,
) -> list[LensResult | None]
```

Run [`search()`](#search) over several images, preserving order. All keyword arguments are forwarded to each `search()` call.

- Lookups **serialize** through the module lock (see [Guide §14](GUIDE.md#14-performance--concurrency)).
- A failed lookup becomes a `None` entry rather than aborting the batch.
- Returns a list the same length and order as `images`.

```python
results = glens.search_many(["a.jpg", "b.jpg", "c.jpg"], backend="auto", max_matches=10)
for img, r in zip(["a.jpg", "b.jpg", "c.jpg"], results):
    print(img, r.top_title if r else "(no results)")
```

---

## `warmup()`

```python
glens.warmup(
    image: str | os.PathLike | bytes | None = None,
    *,
    cookie_file: str | os.PathLike | None = None,
    driver_factory: Callable | None = None,
    timeout: int | None = None,
    force: bool = False,
) -> bool
```

Mint (or refresh) the session cookie jar with one browser run, so subsequent lookups can run over plain HTTP. The deploy-time counterpart of `search()` — call it at container start / CI setup, then use `backend="req"` at runtime.

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `image` | `str \| os.PathLike \| bytes \| None` | `None` | Image to warm up with. When `None`, a tiny locally-generated synthetic PNG is used — the upload content doesn't matter, only the JS-verified session it creates. Accepts the same path/URL/bytes forms as `search()`. |
| `cookie_file` | `str \| os.PathLike \| None` | `None` → `GLENS_COOKIE_FILE` → default | Where to write the jar. |
| `driver_factory` | `Callable \| None` | `None` → `build_driver` | Custom WebDriver factory. |
| `timeout` | `int \| None` | `None` → `10` | Seconds to wait for the (throwaway) results page to render. Shorter than `search()`'s default because a synthetic image renders few tiles. |
| `force` | `bool` | `False` | Re-mint even when a valid jar already exists. |

### Returns

`True` when the jar is ready after the call, `False` otherwise.

- If a valid jar already exists and `force=False`, returns `True` **without launching Chrome** (a cheap no-op — safe to call unconditionally at startup).
- A bad `image` (unreadable path / failed URL download) returns `False`.

### Raises

Nothing for ordinary failures. Requires the `[browser]` extra to actually mint; if it's missing, the underlying browser run fails and `warmup()` returns `False` (the CLI checks for the extra up front and gives a clearer message — see [CLI](#glens-warmup)).

```python
import glens

if not glens.status()["req_ready"]:
    glens.warmup()            # mint once at startup

glens.warmup(force=True)      # force a re-mint (e.g. scheduled refresh)
glens.warmup("real.jpg")     # warm up with a real image
```

---

## `status()`

```python
glens.status(cookie_file: str | os.PathLike | None = None) -> dict
```

Health check for automation: report whether the fast HTTP path is ready right now. Returns a plain, **JSON-safe** dict.

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `cookie_file` | `str \| os.PathLike \| None` | `None` → `GLENS_COOKIE_FILE` → default | Which jar to inspect. |

### Returns

```python
{
  "cookie_file": str,             # jar path inspected
  "jar_valid": bool,              # a loadable jar with cookies exists
  "jar_age_seconds": float | None,  # seconds since minted, or None if unknown/absent
  "browser_extra_installed": bool,  # is undetected-chromedriver importable?
  "req_ready": bool,              # fast path available without launching Chrome
  "default_backend": str,         # the resolved GLENS_BACKEND default
  "cache_enabled": bool,          # the resolved GLENS_CACHE default
}
```

> **`req_ready` reflects presence, not freshness.** It's `True` when a well-formed jar with cookies exists. It **cannot** tell you the cookies are still valid on Google's side — only a live lookup can. Treat `req_ready: True` as "no warmup needed," not "guaranteed to succeed."

```python
import glens
info = glens.status()
if not info["req_ready"]:
    glens.warmup()
```

---

## `LensResult`

```python
@dataclass
class LensResult:
    top_title: str | None
    titles: list[str]
    matches: list[Match]
    raw_anchors: list[dict] = field(default_factory=list)
    results_url: str | None = None
```

The outcome of one Lens pass.

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `top_title` | `str \| None` | Best single product name (`titles[0]`, or `None` if there are no titles). |
| `titles` | `list[str]` | Frequency-ranked product names. Length-filtered (~8–100 chars) and junk-filtered. Capped at `max_titles`. |
| `matches` | `list[Match]` | External merchant/result links, deduped by `(domain, title)`, external-only, in Google's order. Capped at `max_matches`. |
| `raw_anchors` | `list[dict]` | Every harvested anchor, unfiltered: each is `{"href": str, "text": str}`. |
| `results_url` | `str \| None` | The internal Google results URL the anchors came from (has `udm=26`). **Session-bound** — it carries a `vsrid`/`gsessionid` tied to the browser-minted session, so it only renders for a client replaying that session's cookies and is **not** reopenable in an ordinary browser. Use it for logging/telemetry, not sharing. |

### Methods

**`__bool__(self) -> bool`** — falsy when there are neither matches nor titles. Enables `if result:`.

**`__str__(self) -> str`** — a short human summary: the top title plus up to the first 5 matches, with an "… and N more" line. Used by `print(result)`.

**`to_dict(self, *, raw_anchors: bool = False) -> dict`** — a JSON-ready dict of built-in types. Excludes `raw_anchors` by default (it's large); pass `raw_anchors=True` to include it. Nested `Match` objects become dicts.

```python
r = glens.search("s.jpg")
r.top_title                       # "Nike Air Max 90 ..."
r.matches[0].url                  # "https://www.nike.com/..."
bool(r)                           # True if it has matches or titles
print(r)                          # short summary
r.to_dict()                       # {"top_title":..., "titles":[...], "matches":[...], "results_url":...}
r.to_dict(raw_anchors=True)       # + "raw_anchors": [{"href":...,"text":...}, ...]
```

---

## `Match`

```python
@dataclass
class Match:
    title: str
    url: str
    domain: str
```

One external product/result link harvested from the Lens page.

| Field | Type | Description |
|-------|------|-------------|
| `title` | `str` | Cleaned link text / product name (marketplace prefixes, ratings, and UI cruft stripped; truncated to 200 chars). |
| `url` | `str` | The external URL (Google redirect wrappers unwrapped). |
| `domain` | `str` | Netloc, lowercased, `www.` stripped. |

---

## `glens.driver.build_driver()`

```python
from glens.driver import build_driver

build_driver(
    *,
    headless: bool | None = None,
    user_data_dir: str | os.PathLike | None = None,
)  # -> selenium WebDriver (undetected_chromedriver.Chrome)
```

Build a stealthy Chrome via undetected-chromedriver. This is the default `driver_factory` for the browser backend, exposed so you can call it with custom options or wrap it.

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `headless` | `bool \| None` | `None` → `GLENS_HEADLESS` (default `True`) | Run headless. |
| `user_data_dir` | `str \| os.PathLike \| None` | `None` → `GLENS_CHROME_PROFILE` → none | Persistent Chrome profile directory. Recommended: lets the JS-verified session survive across runs. |

### Behavior

- **Version self-heal:** if undetected-chromedriver fetches a driver ahead of your installed Chrome, `build_driver` parses the installed major version from the error and retries pinned to it (`version_main=`). You may see a one-line mismatch warning followed by success.
- Sets a 60 s page-load timeout.

### Raises

- `ImportError` — if the `[browser]` extra (`undetected-chromedriver`) isn't installed. The message includes the fix.
- Other exceptions from Chrome startup propagate if they can't be recovered by the version self-heal.

```python
from glens.driver import build_driver
import glens

# Force a visible, profile-persistent Chrome for minting:
glens.warmup(driver_factory=lambda: build_driver(headless=False,
                                                 user_data_dir="~/.glens-chrome"))
```

---

## `glens.driver.wait_for_document_ready()`

```python
from glens.driver import wait_for_document_ready

wait_for_document_ready(driver, timeout: float = 30) -> None
```

Block until the driver reports an `interactive` or `complete` `document.readyState`. A small utility used internally; exposed for custom driver flows. Raises Selenium's `TimeoutException` if the state isn't reached within `timeout` seconds.

---

## Environment variables

All are **defaults** overridden by explicit arguments. Precedence: **argument > env var > built-in default.**

| Variable | Type | Default | Affects |
|----------|------|---------|---------|
| `GLENS_BACKEND` | `auto`/`req`/`browser` | `auto` | Default `backend`. |
| `GLENS_COOKIE_FILE` | path | `~/.cache/glens/cookies.json` | Session jar location. |
| `GLENS_MAX_MATCHES` | int ≥ 1 | `20` | Default `max_matches`. |
| `GLENS_MAX_TITLES` | int ≥ 1 | `6` | Default `max_titles`. |
| `GLENS_RESULT_TIMEOUT` | int ≥ 5 | `25` | Default browser `timeout`. |
| `GLENS_LANG` | language tag | *(none)* | Default `lang` (Accept-Language). |
| `GLENS_CACHE` | flag | off | Default `use_cache`. Truthy = `1`/`true`/`yes`/`on`. |
| `GLENS_CACHE_TTL` | int ≥ 1 | `86400` | Cache entry lifetime (seconds). |
| `GLENS_CACHE_DIR` | path | `~/.cache/glens/results` | Result cache directory. |
| `GLENS_HEADLESS` | flag | `1` (headless) | Bundled Chrome headless mode. **Setting it (to anything) also disables the visible-window gate-retry.** |
| `GLENS_CHROME_PROFILE` | path | *(none)* | Persistent Chrome profile dir for `build_driver`. |

**Malformed integers** (e.g. `GLENS_MAX_MATCHES=lots`) are ignored with a warning and fall back to the default — they never break `import glens`. Integer minimums are clamped (e.g. `GLENS_RESULT_TIMEOUT` is at least 5).

**Flag parsing:** `GLENS_CACHE` is truthy for `1`/`true`/`yes`/`on` (case-insensitive); anything else is off. `GLENS_HEADLESS` is falsy for `0`/`false`/`no`/`off`/empty and truthy otherwise.

**When each variable is read:** the numeric/flag defaults — `GLENS_BACKEND`, `GLENS_MAX_MATCHES`, `GLENS_MAX_TITLES`, `GLENS_RESULT_TIMEOUT`, `GLENS_LANG`, `GLENS_CACHE`, `GLENS_CACHE_TTL` — are resolved **once, at import time**, into module constants. Set them in the process environment *before* `import glens`; mutating `os.environ[...]` afterward has no effect (pass a per-call argument instead). By contrast, `GLENS_COOKIE_FILE` and `GLENS_CACHE_DIR` are read **per call**, and `GLENS_HEADLESS`/`GLENS_CHROME_PROFILE` **per browser build**, so those *can* be changed at runtime. This matters mostly for tests and notebooks that set env vars programmatically.

---

## CLI reference

Installed as the `glens` console command. Three subcommands; search is the default (no subcommand token needed). `glens search <image>` is accepted as a legacy alias for `glens <image>`.

### `glens <image>` (search)

```
glens IMAGE [--backend {auto,req,browser}] [--json] [--raw]
            [--max-matches N] [--max-titles N] [--timeout SECONDS]
            [--lang LANG] [--cache] [--cookie-file PATH] [-v] [--version]
```

| Flag | Description |
|------|-------------|
| `IMAGE` | Path or `http(s)` URL of the image (required). |
| `--backend {auto,req,browser}` | Lookup backend. Default: `GLENS_BACKEND` or `auto`. |
| `--json` | Emit JSON instead of pretty text. On no-results, prints literal `null`. |
| `--raw` | Include every harvested anchor in the JSON (`raw_anchors`). |
| `--max-matches N` | Cap on merchant links. |
| `--max-titles N` | Cap on ranked titles. |
| `--timeout SECONDS` | Browser-backend wait for result tiles. |
| `--lang LANG` | Preferred response language (Accept-Language), e.g. `de`, `pt-BR`. |
| `--cache` | Reuse recent results for the same image+lang. |
| `--cookie-file PATH` | Session jar location. |
| `-v`, `--verbose` | Show progress logging on stderr. |
| `--version` | Print version and exit. |

**Output:** pretty text by default (top title, ranked titles, matches). The internal, session-bound results URL is printed only under `-v`, clearly labeled as a non-browser-openable debug handle — never in the default output. With `--json`, stdout is valid JSON **whenever a lookup runs** — the result object on success or `null` on no-results — so it pipes cleanly to `jq`; a usage error or an undownloadable URL image instead prints only to stderr and leaves stdout empty. Non-ASCII output is forced to UTF-8 (important on Windows with redirected stdout).

**Exit codes:**

| Code | Meaning |
|------|---------|
| `0` | Results found. |
| `1` | No results (nothing matched or lookup failed); or a URL image failed to download. |
| `2` | Usage error: bad flag/value, unreadable image path, or an invalid backend config reaching `search()`. |

### `glens warmup`

```
glens warmup [--image PATH_OR_URL] [--cookie-file PATH] [--timeout SECONDS] [--force] [-v]
```

Launch Chrome once to mint the session jar.

| Flag | Description |
|------|-------------|
| `--image PATH_OR_URL` | Warm up with a real image instead of the synthetic one. |
| `--cookie-file PATH` | Where to write the jar. |
| `--timeout SECONDS` | Render wait. |
| `--force` | Re-mint even if a valid jar exists. |
| `-v`, `--verbose` | Progress logs. |

**Exit codes:** `0` jar ready · `1` warmup failed · `2` the `[browser]` extra isn't installed.

### `glens status`

```
glens status [--cookie-file PATH] [--json]
```

Report whether the fast path is ready.

| Flag | Description |
|------|-------------|
| `--cookie-file PATH` | Which jar to inspect. |
| `--json` | Emit the status dict as JSON. |

**Output:** human-readable `key: value` lines, or JSON with `--json`. **Exit code** `0` if `req_ready`, else `1` — usable as a health probe.

---

## Logging

glens logs to the standard `logging` logger named **`glens`**. It configures no handlers itself (so it's silent by default in library use). To see progress:

```python
import logging
logging.basicConfig(level=logging.INFO)
# or target just glens:
logging.getLogger("glens").setLevel(logging.INFO)
```

On the CLI, `-v` sets this up for you (INFO to stderr).

Levels used: **INFO** for normal progress (cache hits, backend chosen, anchors harvested, fallbacks); **WARNING** for things you likely want to know even unconfigured (browser extra unavailable, version mismatch retry, headless→visible retry, malformed env ints).

---

## Error & return semantics

| Situation | Result |
|-----------|--------|
| Missing / unreadable image path | `search()` → `None` |
| URL image fails to download | `search()` → `None` |
| `backend="req"` with no/stale jar | `search()` → `None` (no Chrome launch) |
| Network error, parse failure, gated page | `search()` → `None` (with `auto`, falls back to browser first) |
| Nothing matched | `search()` → `None` |
| Invalid `backend=` value | `search()` raises `ValueError` |
| `[browser]` extra missing during a mint | browser run fails → `search()`/`warmup()` → `None`/`False`; a WARNING is logged |
| Bad image in `warmup()` | `warmup()` → `False` |

The design contract: **`search()`, `search_many()`, `warmup()`, and `status()` never raise for operational failures.** The only intentional exception is `ValueError` for an invalid `backend` (a programming error). `glens.driver.build_driver()` may raise `ImportError` when the extra is absent, and Selenium exceptions during an actual browser run are caught internally by the backends.

---

## On-disk file formats

These are internal formats, documented for the "mint on one machine, serve on another" workflow ([Guide §7](GUIDE.md#7-deployment-recipes)). Treat them as opaque where possible.

### Cookie jar (`GLENS_COOKIE_FILE`)

```json
{
  "user_agent": "Mozilla/5.0 ... Chrome/149.0.0.0 ...",
  "cookies": [
    {"name": "NID", "value": "...", "domain": ".google.com", "path": "/"}
  ],
  "minted_at": 1751800000.0
}
```

Only google.com cookies are stored. `minted_at` is a Unix timestamp used for `status()`'s `jar_age_seconds`. A jar is considered valid if it loads and has a non-empty `cookies` list.

### Result cache entry (`GLENS_CACHE_DIR/<key>.json`)

```json
{
  "saved_at": 1751800000.0,
  "results_url": "https://www.google.com/search?...&udm=26",
  "items": [{"href": "https://...", "text": "..."}]
}
```

`<key>` is `sha256(image_bytes + lang)`. Only the raw harvested anchors are stored; shaping (`max_matches`/`max_titles`) is re-applied per call. Entries older than `GLENS_CACHE_TTL` are ignored. Writes are atomic (temp file + rename).

---

*See also: the [Guide](GUIDE.md) for concepts and recipes, [Best Practices](BEST_PRACTICES.md) for usage guidance, and the [README](../README.md) for the quickstart.*
