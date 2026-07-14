# chrome-lens-search

[![CI](https://github.com/darcodev/chrome-lens-search/actions/workflows/ci.yml/badge.svg)](https://github.com/darcodev/chrome-lens-search/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/chrome-lens-search)](https://pypi.org/project/chrome-lens-search/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> **Unofficial.** Not affiliated with, endorsed by, or sponsored by Google. "Google Lens" is a trademark of Google LLC. This is an independent, community-built client for research and educational use.

**Unofficial Google Lens visual product search over plain HTTP.** Give it an image, get back the products Google Lens sees in it — exact product titles plus external merchant/result links. No SerpApi bill, and none of the OCR-only limitations of the free Lens libraries.

<!-- TODO: add demo screenshot/GIF (real `glens search` output on a real image) -->

## Quickstart

```bash
pip install "chrome-lens-search[browser]"
glens sneaker.jpg
```

That's it. The **first** lookup launches Chrome once to create a reusable Google session — it tries headless first and may briefly show a window if Google blocks the headless attempt. **Every lookup after that is a plain, fast HTTP request** (~2 s), no browser, no Selenium in the hot path.

Or from Python — paths, image URLs, or raw bytes all work:

```python
import glens

result = glens.search("sneaker.jpg")                       # local file
result = glens.search("https://example.com/sneaker.jpg")   # or an image URL

if result:                             # None when the lookup fails
    print(result.top_title)            # "Nike Air Max 90 Men's Shoes"
    for m in result.matches:
        print(f"[{m.domain}] {m.title} -> {m.url}")
```

```
[nike.com]      Nike Air Max 90 Men's Shoes -> https://www.nike.com/t/...
[stockx.com]    Nike Air Max 90 -> https://stockx.com/...
[goat.com]      Air Max 90 -> https://www.goat.com/...
```

---

## Why this exists

Google Lens is uniquely good at naming the *exact* product in a photo. But:

- **SerpApi's Lens endpoint is paid.**
- The free Python Lens libraries (`chrome-lens-py` et al.) do **OCR/translation only** — they can't give you the product/shopping matches.
- Lens from a plain server just gets blocked.

This library talks to Google's **own** Lens upload endpoint (the image goes only to Google, never a third-party host) and reads the product matches straight back.

## Install

```bash
pip install "chrome-lens-search[browser]"   # recommended: works out of the box
```

The `[browser]` extra (Selenium + undetected-chromedriver) is only used to create — and occasionally refresh — the Google session. On a machine that already has a session jar (see `GLENS_COOKIE_FILE` below), the slim core is enough:

```bash
pip install chrome-lens-search              # plain-HTTP core only (needs an existing jar)
```

## CLI

```bash
glens sneaker.jpg                            # pretty-printed results
glens https://example.com/sneaker.jpg        # image URLs work too
glens sneaker.jpg --json                     # machine-readable output
glens sneaker.jpg --backend req -v           # fast path only, with progress logs
glens sneaker.jpg --max-matches 5 --max-titles 3
glens sneaker.jpg --lang de                  # prefer German result pages
glens sneaker.jpg --cache                    # reuse recent results for this image
```

(`glens search sneaker.jpg` also works, if you prefer the explicit subcommand.)

Pretty output shows the top title, the frequency-ranked titles, and the merchant links; `--json` emits the full `LensResult` (add `--raw` to include every harvested anchor). Exit code `0` means results were found, `1` means no results, so it scripts cleanly:

```bash
glens photo.jpg --json > result.json || echo "nothing matched"
```

## How it works — the fast/slow hybrid

Google only server-renders the product tiles for a **JS-verified session**. A cold `requests` client just gets an "enable JavaScript" shell. The trick:

1. **Browser (once):** drive a real [undetected-chromedriver](https://github.com/ultrafunkamsterdam/undetected_chromedriver) session, upload the image, harvest the rendered results — and **save the google.com cookies + user-agent** to a small jar.
2. **Requests (every time after):** replay those cookies over plain `requests`. Google now serves the full product HTML to a browser-less client. Fast, no window, no Selenium in the hot path.
3. **Self-healing:** when the jar goes stale, Google serves the degraded JS-gate page; the `req` path detects it *by content* (too few product links, not by sniffing for a marker) and transparently falls back to the browser, which re-mints the jar. You never manage cookies by hand.

So: **first call launches Chrome; subsequent calls are just HTTP** until the cookies expire.

## Backends

| `backend` | Behaviour |
|-----------|-----------|
| `"auto"` (default) | Try `req`, fall back to `browser` if there's no/stale jar. |
| `"req"` | Plain HTTP only. Returns `None` if the jar is missing/stale (won't launch Chrome). |
| `"browser"` | Always drive Chrome; also re-mints the jar. |

```python
glens.search("img.jpg", backend="req")     # fast path only
glens.search("img.jpg", backend="browser") # force a fresh browser pass
```

### Bring your own Chrome

The browser backend defaults to the bundled builder, but you can inject any factory that returns a Selenium-compatible driver — handy if you already run a stealth/persistent Chrome:

```python
def my_driver():
    return make_my_undetected_chrome()

glens.search("img.jpg", driver_factory=my_driver)
```

## The result

`search()` returns a `LensResult` (or `None` on failure — it never raises for ordinary network/parse errors):

```python
@dataclass
class LensResult:
    top_title: str | None      # best single product name
    titles: list[str]          # frequency-ranked product names
    matches: list[Match]       # external merchant/result links
    raw_anchors: list[dict]    # every anchor harvested, unfiltered
    results_url: str | None    # internal, session-bound Google URL (logging only; not browser-openable)

@dataclass
class Match:
    title: str
    url: str
    domain: str
```

`titles` are frequency-ranked: the *exact* product repeats across tiles, so it rises to the top on its own.

For batches there's `search_many`, which runs the images in order through the same in-process lock (be gentle — see the caveats):

```python
results = glens.search_many(["a.jpg", "b.jpg"], backend="auto")  # list of LensResult | None
```

## Automate it

The API is built to be dropped into your own code: one call in, one dataclass (or `None`) out, nothing raises for ordinary failures. Two helpers cover the ops side:

```python
import glens

# At startup / deploy time: make sure the fast path is ready.
if not glens.status()["req_ready"]:
    glens.warmup()                      # one headless Chrome run (needs [browser])

# In your pipeline: pure HTTP from here on, ~2-3s per lookup.
for path in my_images:
    result = glens.search(path, backend="req")
    if result:
        print(path, "->", result.top_title)
```

- **`glens.status()`** — JSON-safe health dict: is the session jar valid, how old is it, is the browser extra installed, is the fast path (`req_ready`) available. Same thing on the CLI: `glens status --json` (exit 0 = ready), perfect for container health checks.
- **`glens.warmup()`** — mints the jar with one browser run and returns `True` when ready; a no-op if the jar already exists. CLI: `glens warmup`. Run it once at container start or CI setup, then use `backend="req"` at runtime so production code can never accidentally launch Chrome.
- **Headless servers without Chrome:** run `glens warmup` on any machine that has Chrome, ship the jar file (`GLENS_COOKIE_FILE`), and the server only ever needs the slim `requests` core.
- **Batch jobs:** see [examples/batch_folder.py](examples/batch_folder.py) — folder in, CSV of product matches out. Calls serialise through an in-process lock; keep it to one image at a time and don't hammer.

For machine consumption, `result.to_dict()` gives you JSON-ready output, and the CLI's `--json` + exit codes (0 results / 1 none / 2 usage) script cleanly.

## Configuration

Everything is a per-call argument; env vars are just the defaults.

| Argument | Env var | Default |
|----------|---------|---------|
| `backend` | `GLENS_BACKEND` | `auto` |
| `cookie_file` | `GLENS_COOKIE_FILE` | `~/.cache/glens/cookies.json` |
| `max_matches` | `GLENS_MAX_MATCHES` | `20` |
| `max_titles` | `GLENS_MAX_TITLES` | `6` |
| `timeout` | `GLENS_RESULT_TIMEOUT` | `25` |
| `lang` | `GLENS_LANG` | *(none)* — preferred response language via `Accept-Language`, e.g. `de`, `pt-BR` |
| `use_cache` | `GLENS_CACHE` | off |
| — | `GLENS_CACHE_TTL` | `86400` — cache entry lifetime in seconds |
| — | `GLENS_CACHE_DIR` | `~/.cache/glens/results` |
| — | `GLENS_HEADLESS` | `1` |
| — | `GLENS_CHROME_PROFILE` | *(none)* — persistent Chrome profile dir |

Pointing `GLENS_CHROME_PROFILE` at a persistent directory lets the verified session survive across runs, keeping the fast path fast.

### Language

`lang` asks Google for a response language via the fast path's `Accept-Language` header:

```python
glens.search("img.jpg", lang="de")   # prefer German result pages
```

Best-effort by design: Google decides, and the country/market always follows your IP. (There's deliberately no `hl`/`gl` URL override — the Lens results URL is session-signed and Google answers any query tampering, on either the upload or the results request, with a 403. Live-tested.)

### Result caching (opt-in)

`use_cache=True` (or `--cache`, or `GLENS_CACHE=1`) stores the harvested anchors per image+locale (SHA-256 keyed) under `~/.cache/glens/results` and reuses them for 24 hours (`GLENS_CACHE_TTL`). Great for dev loops and re-runs — it avoids re-uploading the same image to Google. `max_matches`/`max_titles` still apply per call; only the raw harvest is cached. An explicit `backend="browser"` always skips the cache *read* (a successful browser run still *refreshes* the cached entry), so forcing a jar re-mint keeps working with caching on.

## Troubleshooting

**"No results" on the very first run** → the first lookup needs Chrome to create the session. Install the extra and retry: `pip install "chrome-lens-search[browser]"`. The CLI prints this hint when it applies.

**Want a server with no Chrome at all?** Run one lookup on any machine that has Chrome, then copy the jar (`~/.cache/glens/cookies.json`) to the server and point `GLENS_COOKIE_FILE` at it. Use `backend="req"` so the server never tries to launch a browser. When the jar eventually goes stale, re-mint it the same way.

**Nothing prints / want to see progress?** Add `-v` on the CLI, or in Python: `logging.basicConfig(level=logging.INFO)` — all progress goes to the `glens` logger.

## Caveats — read these

- **This scrapes an undocumented Google endpoint.** It is not an official API, and it's against Google's Terms of Service. Use it where that's acceptable to you.
- **It will break.** Google changes the endpoint, the JS-gate behaviour, and the markup without notice. When that happens the harvesting is deliberately *selector-light* (anchors + aria-labels, no hardcoded class names) so it degrades rather than shatters — but expect to patch it. PRs welcome.
- **Results are noisy by design.** The parser favours recall; verify matches downstream if you need precision.
- **Be gentle.** One image per lookup, a module lock serialises calls in-process. Don't hammer it.

## Documentation

- **[Guide](docs/GUIDE.md)** — the long-form walkthrough: the fast/slow hybrid, automating glens in your code, deployment recipes (Docker, Chrome-less servers, CI), the CLI in depth, troubleshooting, and how it works under the hood.
- **[API Reference](docs/API.md)** — every function, dataclass, environment variable, CLI flag, and exit code, with exact signatures and return/error semantics.
- **[Best Practices](docs/BEST_PRACTICES.md)** — what to actually do: backend choice, minting vs serving, being gentle, handling `None`, caching strategy, deployment, jar hygiene, security, and testing your integration.

## Development

```bash
pip install -e ".[dev]"
pytest          # hermetic: no network, no browser
ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) — especially the rules about synthetic test fixtures (never commit Google content).

## License

MIT — see [LICENSE](LICENSE).

> Not affiliated with, endorsed by, or sponsored by Google. "Google Lens" is a trademark of Google LLC.
