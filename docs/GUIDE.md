# glens — Complete Guide

> **Unofficial.** Not affiliated with, endorsed by, or sponsored by Google. "Google Lens" is a trademark of Google LLC. This is an independent, community-built client for research and educational use. It scrapes an **undocumented** endpoint, is against Google's Terms of Service, and **will** break when Google changes things.

This guide is the long-form companion to the [README](../README.md). If you want the exact signatures, env vars, and exit codes, jump to the [API Reference](API.md). If you want to *understand* the library — how it works, how to embed it, how to run it on a server, and what to do when it breaks — read on.

---

## Table of contents

1. [What glens is](#1-what-glens-is)
2. [The mental model: a fast/slow hybrid](#2-the-mental-model-a-fastslow-hybrid)
3. [Installation](#3-installation)
4. [Your first lookup](#4-your-first-lookup)
5. [The three backends](#5-the-three-backends)
6. [Automating glens in your own code](#6-automating-glens-in-your-own-code)
7. [Deployment recipes](#7-deployment-recipes)
8. [The CLI in depth](#8-the-cli-in-depth)
9. [Working with results](#9-working-with-results)
10. [Configuration](#10-configuration)
11. [Language preference](#11-language-preference)
12. [Result caching](#12-result-caching)
13. [Bring your own Chrome](#13-bring-your-own-chrome)
14. [Performance & concurrency](#14-performance--concurrency)
15. [Troubleshooting](#15-troubleshooting)
16. [How it works under the hood](#16-how-it-works-under-the-hood)
17. [Limitations & caveats](#17-limitations--caveats)
18. [FAQ](#18-faq)

---

## 1. What glens is

`glens` (PyPI name `chrome-lens-search`) takes an **image** and returns the **products Google Lens sees in it**: exact product titles plus external merchant/result links.

```python
import glens

result = glens.search("sneaker.jpg")
if result:
    print(result.top_title)          # "Nike Air Max 90 Men's Shoes"
    for m in result.matches:
        print(m.domain, m.title, m.url)
```

It exists because the obvious alternatives don't fit:

| Option | Problem |
|--------|---------|
| SerpApi's Lens endpoint | Paid. |
| `chrome-lens-py` and friends | OCR / translation only — no product/shopping matches. |
| Plain `requests` to Google | Blocked: you get an "enable JavaScript" shell, not results. |

glens talks to Google's **own** Lens upload endpoint (`https://lens.google.com/v3/upload`). The image goes only to Google, never a third-party host.

**What you get back** is deliberately *high-recall, noisy* data: Google Lens surfaces the exact product plus a lot of visually-similar and loosely-related links. glens harvests all of it and ranks the product titles by frequency (the exact match repeats across tiles, so it floats to the top). If you need precision, verify matches downstream.

---

## 2. The mental model: a fast/slow hybrid

This is the one concept worth internalizing. Everything else follows from it.

Google only server-renders the Lens product tiles for a **JS-verified browser session**. A cold `requests` client — no matter how you set the headers — gets an "enable JavaScript" shell instead of results.

glens gets around this with a **two-speed design**:

```
                          ┌─────────────────────────────────────────┐
   FIRST run (once):      │  BROWSER: undetected-chromedriver drives  │
   mint the session       │  a real Chrome, uploads the image,        │
                          │  renders results, and SAVES the           │
                          │  google.com cookies + User-Agent to a     │
                          │  small "jar" file.                        │
                          └────────────────────┬──────────────────────┘
                                               │  cookies.json
                                               ▼
                          ┌─────────────────────────────────────────┐
   EVERY run after:       │  REQUESTS: replay the jar's cookies +     │
   plain, fast HTTP       │  a full Chrome header surface over plain  │
   (~2 seconds)           │  `requests`. Google now serves the full   │
                          │  product HTML to a browser-less client.   │
                          └─────────────────────────────────────────┘

   SELF-HEALING: when the jar goes stale, the requests path detects the
   degraded page and transparently falls back to the browser, which re-mints
   the jar. You never manage cookies by hand.
```

So:

- **The first lookup** on a machine launches Chrome once (headless; a window may flash if Google gates the headless attempt — see [§16](#16-how-it-works-under-the-hood)). It takes ~20–40 s.
- **Every lookup after that** is plain HTTP: ~2 s, no browser, no Selenium in the hot path.
- When cookies expire, the next lookup silently re-mints them via the browser.

The practical upshot: **you need Chrome once to bootstrap, but not in your hot path.** That's what makes glens embeddable in servers and pipelines — see [§6](#6-automating-glens-in-your-own-code) and [§7](#7-deployment-recipes).

---

## 3. Installation

```bash
pip install "chrome-lens-search[browser]"   # recommended — works out of the box
```

The `[browser]` extra pulls in `selenium` + `undetected-chromedriver`. You need it to **mint or refresh** the session (the first run, and whenever the jar goes stale). It is **not** used in the fast path.

If you're deploying to a machine that will *only* run the fast path against an already-minted jar (see [§7](#7-deployment-recipes)), the slim core is enough:

```bash
pip install chrome-lens-search            # requests-only core; needs an existing jar
```

**Requirements**

- Python ≥ 3.10 (the code uses `X | None` type syntax).
- The core depends only on `requests`.
- The `[browser]` extra additionally needs a real **Google Chrome / Chromium** installed on the machine (undetected-chromedriver drives your local Chrome; it does not bundle a browser).

Verify the install:

```bash
python -c "import glens; print(glens.__version__)"
glens --version
```

---

## 4. Your first lookup

### From the terminal

```bash
glens sneaker.jpg
```

The very first time, with no session jar yet, you'll see:

```
First run: launching Chrome once to create the fast-path session
(takes a while; a window may appear briefly; later runs are plain HTTP)...
```

Then, once it finishes, the pretty-printed result:

```
Top title: Nike Air Max 90 Men's Shoes

Ranked titles:
  1. Nike Air Max 90 Men's Shoes
  2. Air Max 90
  ...

Matches (20):
  [nike.com] Nike Air Max 90 Men's Shoes
      https://www.nike.com/t/...
  [stockx.com] Nike Air Max 90
      https://stockx.com/...
  ...
```

Run it again — it's now a ~2-second HTTP call, no browser.

You can pass a **URL** instead of a path:

```bash
glens https://example.com/product.jpg
```

### From Python

```python
import glens

result = glens.search("sneaker.jpg")       # local file path
# result = glens.search("https://example.com/product.jpg")   # or an image URL
# result = glens.search(open("sneaker.jpg", "rb").read())     # or raw bytes

if result:                                   # None on failure — never raises
    print(result.top_title)
    for m in result.matches:
        print(f"[{m.domain}] {m.title} -> {m.url}")
else:
    print("No results.")
```

Three important properties of `search()`:

1. **It never raises for ordinary failures.** A missing file, a dead URL, a network blip, a parse failure — all return `None`. (The one exception: an invalid `backend=` value raises `ValueError`, because that's a bug in *your* code, not a lookup failure.)
2. **It accepts a path, an `http(s)` URL, or raw `bytes`.**
3. **It's synchronous and serialized** — see [§14](#14-performance--concurrency).

---

## 5. The three backends

`search(..., backend=...)` (or the `--backend` CLI flag, or `GLENS_BACKEND` env var) picks how a lookup runs:

| `backend` | Behaviour | Launches Chrome? |
|-----------|-----------|------------------|
| `"auto"` *(default)* | Try the fast `req` path; if there's no jar or it's stale, fall back to `browser` (which also re-mints the jar). | Only on fallback |
| `"req"` | Plain HTTP only. Returns `None` if the jar is missing or stale — never launches Chrome. | Never |
| `"browser"` | Always drive Chrome, harvest the rendered DOM, and re-mint the jar. | Always |

**When to use which:**

- **`auto`** — the default and the right choice for interactive use and one-off scripts. It "just works," self-healing when cookies expire.
- **`req`** — production hot paths where you never want to accidentally launch Chrome. Pair it with a jar you minted ahead of time (`warmup`, [§6](#6-automating-glens-in-your-own-code)). On a Chrome-less server this is the *only* backend that works.
- **`browser`** — force a fresh browser pass, e.g. to deliberately re-mint the jar or debug rendering. Note it **always** skips the result cache (see [§12](#12-result-caching)).

---

## 6. Automating glens in your own code

glens is built to drop into other people's code. The API is deliberately small and boring: one call in, one dataclass (or `None`) out, nothing raises for ordinary failures. Two helper functions cover the operational side so you never have to reason about cookies.

### The two ops helpers

```python
import glens

glens.status()   # -> dict: is the fast path ready right now?
glens.warmup()   # -> bool: mint the session (one browser run); no-op if already ready
```

**`status()`** returns a JSON-safe dict:

```python
{
  "cookie_file": "/home/you/.cache/glens/cookies.json",
  "jar_valid": True,               # a loadable jar with cookies exists
  "jar_age_seconds": 1172.8,       # how long since it was minted
  "browser_extra_installed": True, # is [browser] available to re-mint?
  "req_ready": True,               # can the fast path run without Chrome?
  "default_backend": "auto",
  "cache_enabled": False,
}
```

**`warmup()`** launches Chrome once to mint (or, with `force=True`, refresh) the jar, then returns `True` when the fast path is ready. It's a **no-op that returns `True`** if a valid jar already exists, so it's safe to call unconditionally at startup.

### The canonical startup pattern

```python
import glens

# At process/container start: guarantee the fast path is ready.
if not glens.status()["req_ready"]:
    glens.warmup()                 # one headless Chrome run; needs [browser]

# In your hot path: pure HTTP, ~2 s each, no Chrome.
for path in incoming_images:
    result = glens.search(path, backend="req")
    if result:
        record(path, result.top_title, [m.url for m in result.matches])
```

Using `backend="req"` in the loop guarantees your request-handling code can never block on a Chrome launch. The `warmup()` call up front means the jar is always there when the loop runs.

> **Caveat on staleness.** `status()["req_ready"]` tells you a *valid* jar exists — it cannot tell you the cookies are still *fresh* on Google's side (only a live lookup can). If you want the self-healing behavior, use `backend="auto"` instead of `"req"` in your loop, or catch a run of `None` results and call `warmup(force=True)`.

### Batch jobs

For "reverse-search every image in a folder," see [examples/batch_folder.py](../examples/batch_folder.py): folder in, CSV of matches out. The gist:

```python
import csv, glens
from pathlib import Path

with open("results.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["image", "top_title", "best_url"])
    for p in Path("photos").glob("*.jpg"):
        r = glens.search(p)
        best = r.matches[0].url if r and r.matches else ""
        w.writerow([p.name, r.top_title if r else "", best])
```

There's also `glens.search_many([...])`, which runs a list in order and returns a list of `LensResult | None`, a failed lookup becoming a `None` entry rather than aborting the batch.

---

## 7. Deployment recipes

### The core insight for servers

Because the fast path is pure `requests`, you can **separate minting from serving**:

- **Mint** the jar once on any machine that has Chrome.
- **Serve** with `backend="req"` on a machine that has only the `requests` core — no Chrome, no Selenium.

The jar (`~/.cache/glens/cookies.json`, or wherever `GLENS_COOKIE_FILE` points) is a small JSON file of cookies + a User-Agent. Copy it to the serving machine and point glens at it.

### Recipe: a server with no Chrome

On a machine **with** Chrome:

```bash
pip install "chrome-lens-search[browser]"
glens warmup                 # writes ~/.cache/glens/cookies.json
```

Copy that file to the server, then on the server (Chrome-free):

```bash
pip install chrome-lens-search  # slim core
export GLENS_COOKIE_FILE=/opt/app/glens-cookies.json
export GLENS_BACKEND=req      # never try to launch Chrome
```

```python
result = glens.search(image_bytes)   # pure HTTP
```

When the jar eventually goes stale, re-mint it the same way and redeploy the file. (You can automate this: a small cron job on the Chrome-equipped machine runs `glens warmup --force` and rsyncs the jar.)

### Recipe: Docker (self-minting)

If your container *does* have Chrome, warm up at container start:

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y chromium && rm -rf /var/lib/apt/lists/*
RUN pip install "chrome-lens-search[browser]"
ENV GLENS_HEADLESS=1
# Warm up at build or entrypoint time; then your app uses backend="req".
```

> **Headless-server gotcha.** By default, if headless Chrome gets a degraded page, glens retries once with a **visible** window — which fails on a box with no display. On a headless server, set `GLENS_HEADLESS=1`. That keeps Chrome headless *and* disables the visible-window retry (see [§16](#16-how-it-works-under-the-hood)). If headless itself gets gated in your environment, run under `xvfb-run` so a virtual display is available.

### Recipe: CI / cron warmups

Keep a scheduled job that refreshes the jar before it expires:

```bash
glens warmup --force && aws s3 cp ~/.cache/glens/cookies.json s3://my-bucket/glens/
```

and have your workers pull that object into `GLENS_COOKIE_FILE` on boot.

---

## 8. The CLI in depth

Installing the package puts a `glens` command on your PATH. There are three subcommands; the search one is the default (no subcommand needed).

### `glens <image>` — search

```bash
glens sneaker.jpg                       # pretty output
glens https://example.com/sneaker.jpg   # URLs work too
glens sneaker.jpg --json                # machine-readable
glens sneaker.jpg --json --raw          # + every harvested anchor
glens sneaker.jpg --backend req -v      # fast path only, with progress logs
glens sneaker.jpg --max-matches 5 --max-titles 3
glens sneaker.jpg --lang de             # prefer German result pages
glens sneaker.jpg --cache               # reuse a recent result for this image
glens search sneaker.jpg                # explicit subcommand also works
```

**Exit codes** make it scriptable:

| Code | Meaning |
|------|---------|
| `0` | Results found. |
| `1` | No results (nothing matched, or the lookup failed), or a URL image couldn't be downloaded. |
| `2` | Usage error: bad flag, unreadable image path, or invalid backend config. |

```bash
glens photo.jpg --json > out.json || echo "nothing matched"
```

Whenever a lookup actually runs, `--json` makes stdout valid JSON — the result object on success, or the literal `null` on no-results — so you can pipe it straight into `jq`. (The exceptions are errors that happen *before* the lookup: a usage error or an undownloadable URL image prints only a stderr message and exits non-zero, leaving stdout empty.)

### `glens warmup` — mint the session

```bash
glens warmup                 # no-op if a valid jar already exists
glens warmup --force         # re-mint even if a jar exists
glens warmup --image real.jpg  # warm up with a real image instead of the synthetic one
glens warmup -v              # show progress logs
```

Exit codes: `0` jar ready, `1` warmup failed, `2` the `[browser]` extra isn't installed.

### `glens status` — health check

```bash
glens status          # human-readable key: value lines
glens status --json   # JSON for scripts/health checks
```

Exit code `0` if the fast path is ready (`req_ready`), `1` otherwise — so it doubles as a container health probe:

```bash
glens status >/dev/null && echo "ready"
```

For the full flag list of every command, see the [API Reference → CLI](API.md#cli-reference).

---

## 9. Working with results

`search()` returns a `LensResult` or `None`. The dataclass:

```python
@dataclass
class LensResult:
    top_title: str | None      # best single product name (titles[0], or None)
    titles: list[str]          # frequency-ranked product names
    matches: list[Match]       # external merchant/result links (deduped)
    raw_anchors: list[dict]    # every anchor harvested, unfiltered: {"href","text"}
    results_url: str | None    # internal, session-bound Google URL (logging only; not browser-openable)

@dataclass
class Match:
    title: str
    url: str
    domain: str                # normalized, "www." stripped
```

### Truthiness

`LensResult` is falsy when it has neither matches nor titles, so `if result:` is the idiomatic guard. `search()` already returns `None` in that case, but the `__bool__` is there if you build results yourself or filter.

### Pretty-printing and JSON

```python
print(result)                       # short human summary (top title + first 5 matches)
result.to_dict()                    # JSON-ready dict, WITHOUT raw_anchors
result.to_dict(raw_anchors=True)    # include raw_anchors too
```

`to_dict()` is what you want for logging, APIs, or writing to disk — it's a plain dict of built-in types. `raw_anchors` is excluded by default because it's large; pass `raw_anchors=True` if you want to do your own mining.

### Understanding the ranking

`titles` are ranked by **frequency**: the exact product tends to repeat across many result tiles, so it rises to the top on its own. `top_title` is just `titles[0]` (or `None`). Titles are also length-filtered (roughly 8–100 chars) to drop noise, so a valid `Match` may exist without a corresponding entry in `titles`.

`matches` are **deduplicated** by `(domain, title)` and are **external only** — links back to `google.com` are dropped. They preserve Google's original ordering.

### Filtering the noise yourself

Results favor recall. If you need precision, filter on `raw_anchors` or `matches` — for example, keep only known retailer domains:

```python
RETAILERS = {"nike.com", "stockx.com", "goat.com", "amazon.com"}
hits = [m for m in result.matches if m.domain in RETAILERS]
```

---

## 10. Configuration

Every knob is a **per-call argument**; environment variables just set the defaults. Precedence is always: **explicit argument > environment variable > built-in default.**

| Argument | Env var | Default | Meaning |
|----------|---------|---------|---------|
| `backend` | `GLENS_BACKEND` | `auto` | `auto` / `req` / `browser`. |
| `cookie_file` | `GLENS_COOKIE_FILE` | `~/.cache/glens/cookies.json` | Where the session jar lives. |
| `max_matches` | `GLENS_MAX_MATCHES` | `20` | Cap on `matches`. |
| `max_titles` | `GLENS_MAX_TITLES` | `6` | Cap on `titles`. |
| `timeout` | `GLENS_RESULT_TIMEOUT` | `25` | Browser-backend wait (seconds) for result tiles. |
| `lang` | `GLENS_LANG` | *(none)* | Preferred response language (Accept-Language). |
| `use_cache` | `GLENS_CACHE` | off | Reuse recent results for the same image. |
| — | `GLENS_CACHE_TTL` | `86400` | Cache entry lifetime (seconds). |
| — | `GLENS_CACHE_DIR` | `~/.cache/glens/results` | Cache directory. |
| — | `GLENS_HEADLESS` | `1` | Run the bundled Chrome headless. See below. |
| — | `GLENS_CHROME_PROFILE` | *(none)* | Persistent Chrome profile dir for the bundled driver. |

**Notes:**

- Malformed integer env vars (e.g. `GLENS_MAX_MATCHES=lots`) are ignored with a warning — they never break `import glens`.
- `GLENS_CHROME_PROFILE` pointing at a persistent directory lets the JS-verified session survive across runs, keeping the fast path fast for longer.
- `GLENS_HEADLESS` has a subtle interaction with the headless-gate retry — see [§16](#16-how-it-works-under-the-hood).

---

## 11. Language preference

`lang` asks Google for a response language via the fast path's `Accept-Language` header:

```python
glens.search("img.jpg", lang="de")     # prefer German result pages
```

```bash
glens img.jpg --lang de
```

It's **best-effort by design**: Google decides, and the country/market always follows your **IP**. There is deliberately **no** `hl`/`gl` URL override, because the Lens results URL is session-signed — editing its query (or putting locale params on the upload request) gets a `403` from Google. This was tested live; the Accept-Language header is the only lever Google honors here.

Note that `lang` is part of the [cache key](#12-result-caching): the same image under `lang="de"` and `lang="fr"` are cached separately.

---

## 12. Result caching

Caching is **opt-in**. Turn it on with `use_cache=True`, the `--cache` flag, or `GLENS_CACHE=1`:

```python
glens.search("img.jpg", use_cache=True)
```

```bash
glens img.jpg --cache
```

**How it works:**

- The cache key is `sha256(image_bytes)` combined with the `lang`. Same image + same language ⇒ same entry.
- Entries live under `~/.cache/glens/results` (`GLENS_CACHE_DIR`) and expire after `GLENS_CACHE_TTL` seconds (default 24 h). Stale and corrupt entries are treated as misses.
- Only the **raw harvest** is cached. `max_matches` / `max_titles` are applied *per call* to the cached data, so you can cache once and re-shape freely.
- Writes are **atomic** (temp file + rename), so concurrent processes can't leave a torn entry.

**Interaction with `backend="browser"`:** an explicit browser run **never reads** the cache (because a browser run is also how the jar gets re-minted, and a cache hit must not skip that side effect). A successful browser run *does* refresh the cache entry. So `--cache` + `--backend browser` = "always do a fresh browser pass and update the cache."

Caching is great for dev loops and re-runs — it avoids re-uploading the same image to Google. It's not an eviction-managed store: entries only disappear on TTL expiry, so if you cache millions of distinct images, prune `GLENS_CACHE_DIR` yourself.

---

## 13. Bring your own Chrome

The browser backend defaults to the bundled `undetected-chromedriver` builder, but you can inject any factory that returns a Selenium-compatible driver — handy if you already run a stealth or persistent Chrome:

```python
def my_driver():
    return make_my_undetected_chrome()

glens.search("img.jpg", driver_factory=my_driver)
glens.warmup(driver_factory=my_driver)
```

The factory must be a **zero-argument callable** returning a full Selenium-compatible WebDriver. glens drives it with `.get`, `.execute_script`, `.execute_async_script`, `.set_script_timeout`, `.find_elements` (and clicks the elements it returns), `.get_cookies`, and `.quit` — so a standard Selenium / undetected-chromedriver driver is the safe bet, not a hand-rolled subset.

Two behaviors are reserved for the **bundled** `build_driver` and won't apply to your custom factory:

- The **headless-gate retry** (visible-window fallback) only triggers for the bundled driver.
- The **chromedriver/Chrome version self-heal** (see [§16](#16-how-it-works-under-the-hood)) is built into `build_driver`.

If you want those, use (or wrap) `glens.driver.build_driver` — it accepts `headless=` and `user_data_dir=` keyword args.

---

## 14. Performance & concurrency

- **Fast path latency:** ~2 s per lookup over plain HTTP once the jar exists (measured live). A cached hit is ~sub-second.
- **First run / re-mint:** ~20–40 s (a full Chrome cold-start).
- **Concurrency:** all lookups serialize through a single in-process lock (`threading.Lock`). Calling `search()` from multiple threads is safe but **not** parallel — calls queue. This is deliberate: it keeps you from hammering Google. If you need throughput, batch politely rather than fanning out.
- **Be gentle.** This is an undocumented endpoint. One image per lookup, don't parallelize across processes to blast Google, and expect that aggressive use ages your session jar faster (rapid successive uploads can stale a jar within an hour — normal, and `auto` self-heals).

---

## 15. Troubleshooting

Turn on progress logs first — they explain almost everything:

```bash
glens photo.jpg -v
```

```python
import logging
logging.basicConfig(level=logging.INFO)   # everything logs to the "glens" logger
```

| Symptom | Cause & fix |
|---------|-------------|
| **"No results" on the very first run**, and a hint about `chrome-lens-search[browser]` | The first lookup needs Chrome to mint the session and the extra isn't installed. `pip install "chrome-lens-search[browser]"`. |
| **`browser backend unavailable`** in logs | Same: `[browser]` extra missing, or Chrome not installed on the machine. |
| **`chromedriver/Chrome version mismatch; retrying pinned...`** | Harmless — glens detected undetected-chromedriver fetched a driver ahead of your Chrome and auto-retried pinned to your version. |
| **`headless Chrome got a degraded page; retrying...with a visible Chrome window`** | Google gated the headless session; glens is re-minting with a real window. On a **headless server** this retry fails — set `GLENS_HEADLESS=1` to disable it, or run under `xvfb-run`. |
| **`JS-gate shell served (stale/weak jar); falling back to the browser`** | Normal self-heal: the cookies aged out and the browser is re-minting. With `backend="req"` this instead returns `None` (it won't launch Chrome). |
| **`req backend: HTTP error (403/429...)`** | Google refused the session (rate-limited or blocked). glens falls back to the browser to re-mint. If it persists, you're being too aggressive — slow down. |
| **`glens status` says `req_ready: false`** | No valid jar. Run `glens warmup` (needs `[browser]`). |
| **CLI crashes with `cannot read image`** | Typo'd path or unreadable file — exit code 2, a precise error, not a backend problem. |
| **Works locally, fails on the server** | Almost always the headless gotcha or a missing jar. See [§7](#7-deployment-recipes). |

If a lookup that used to work suddenly returns `None` everywhere, Google likely changed the markup or gating. Because the parser is [selector-light](#16-how-it-works-under-the-hood) it degrades rather than shatters, but the endpoint *will* break sometimes — that's the nature of an unofficial scraper. See [CONTRIBUTING](../CONTRIBUTING.md) for how to report a break.

---

## 16. How it works under the hood

For the curious and for contributors. This is the "why" behind the code in [`glens/lens.py`](../glens/lens.py) and [`glens/driver.py`](../glens/driver.py).

**The two legs of a lookup.** Every lookup is (1) an **upload**: a multipart POST of the image to `lens.google.com/v3/upload`, which redirects to a results URL carrying `udm=26`; and (2) a **read** of that results URL to harvest anchors. The browser backend does both from inside Chrome (it submits an in-page multipart form — a navigation, since a cross-origin `fetch` to the endpoint is CORS-blocked — then harvests the DOM); the requests backend does both over `requests`.

**Why the jar isn't enough — header fingerprinting.** Early on, replaying just the cookies over `requests` still got the JS-gate shell. Live testing showed Google fingerprints the **header surface**, not only cookies: with a bare header set you're gated even with fresh cookies, but with Chrome's full surface (`sec-ch-ua` derived from the jar's User-Agent, `Sec-Fetch-*`, `Referer`/`Origin`, a realistic `Accept`) you get the server-rendered results. `_req_session` mirrors Chrome for exactly this reason.

**Gate detection by content, not marker.** You might expect to detect the gate by looking for the "enable JS" marker in the HTML — but healthy results pages *also* carry that `noscript` link. So glens judges a page by its **content**: it counts external anchors, and treats "too few" (< 3) as gated/degraded and falls back. Healthy pages carry dozens.

**Headless gating + the visible-window retry.** Google frequently serves *headless* Chrome a degraded page (and thus useless cookies). So `_search_via_browser` checks whether the rendered page really looks like results (≥ 10 anchors); if a headless pass comes back degraded, it retries **once with a visible window**. Two guards keep this from surprising you: it only happens for the **bundled** `build_driver`, and only when you **haven't** set `GLENS_HEADLESS` yourself. That's why setting `GLENS_HEADLESS=1` on a display-less server both keeps Chrome headless and disables the doomed visible retry.

**Only save cookies from real pages.** The jar is saved **only** when a browser pass actually rendered results (≥ 10 anchors). A degraded/gated page never overwrites a good jar with junk cookies — which is what keeps `status()` honest.

**chromedriver/Chrome version self-heal.** undetected-chromedriver sometimes fetches a driver one major version ahead of your installed Chrome, which fails the cold start. `build_driver` parses the installed version out of the error message ("Current browser version is 149…") and retries pinned to it (`version_main=149`). This is why a first run may log a mismatch warning and then succeed anyway.

**Selector-light parsing.** Lens markup uses randomized, rotating CSS class names, so glens never hardcodes them. It harvests every `<a href>` plus its `aria-label`/text and mines those. This favors recall and degrades gracefully when Google reshuffles the DOM. Merchant links are often wrapped in `google.com/url?q=...` redirects, which glens unwraps.

**The lock.** A module-level `threading.Lock` serializes the actual network work (both backends) so concurrent callers don't stampede Google.

---

## 17. Limitations & caveats

- **It scrapes an undocumented endpoint.** Not an official API, against Google's ToS, and it *will* break when Google changes things. Use it where that's acceptable to you.
- **Results are noisy by design.** High recall, lower precision. Verify downstream if you need certainty (see [§9](#9-working-with-results)).
- **Country/market follows your IP.** `lang` nudges the response language only; there's no working way to spoof the market on this endpoint.
- **Session jars age.** Especially under rapid use. `auto` self-heals; `req`-only setups need periodic re-mints.
- **No monetization, no telemetry.** glens never phones home. The image goes only to Google.
- **Chrome required to bootstrap.** You can't mint a jar without a real Chrome somewhere; the fast path is Chrome-free only *after* minting.

---

## 18. FAQ

**Do end users need Chrome?**
Only to *mint* the session (first run, or re-mints). Once a jar exists, the fast path is pure `requests`. You can even mint on one machine and copy the jar to a Chrome-less server ([§7](#7-deployment-recipes)).

**Is my image sent to a third party?**
No. It's uploaded only to Google's Lens endpoint, exactly as the Google Lens web UI does.

**Can I run it fully headless / all-`req`, no browser ever?**
Not for the first mint — Google gates cold, cookieless sessions (verified). After a jar exists, yes: `backend="req"` never launches Chrome.

**Why is the first call slow and the rest fast?**
The first call cold-starts Chrome to mint the session; the rest ride the minted cookies over plain HTTP. See [§2](#2-the-mental-model-a-fastslow-hybrid).

**How do I get JSON out?**
`result.to_dict()` in Python, or `glens img.jpg --json` on the CLI.

**How do I make it quiet / verbose?**
It logs nothing by default. Add `-v` (CLI) or `logging.basicConfig(level=logging.INFO)` (Python) to see progress on the `glens` logger.

**Does it work on Windows / macOS / Linux?**
Yes — CI runs the test suite on all three across Python 3.10–3.12.

**Something broke against live Google. Now what?**
Enable `-v`, capture the `glens:` log lines, and open an issue per [CONTRIBUTING](../CONTRIBUTING.md). Don't paste raw Google HTML (it's their copyrighted content and can carry your session cookies).

---

*See also: the [API Reference](API.md) for exact signatures, [Best Practices](BEST_PRACTICES.md) for how to use it well, the [README](../README.md) for the quickstart, and [CONTRIBUTING](../CONTRIBUTING.md) for reporting breaks.*
