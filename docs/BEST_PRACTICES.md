# glens — Best Practices

Opinionated, field-tested guidance for using `glens` well. The [Guide](GUIDE.md) explains how things work and the [API Reference](API.md) gives exact signatures; this page is the "what you should actually do" layer.

Each item is a short rule, the reason behind it, and the code that follows from it.

---

## Table of contents

1. [Choosing a backend](#1-choosing-a-backend)
2. [Separate minting from serving](#2-separate-minting-from-serving)
3. [Be gentle — respect the endpoint](#3-be-gentle--respect-the-endpoint)
4. [Handle `None` everywhere](#4-handle-none-everywhere)
5. [Treat results as noisy — verify downstream](#5-treat-results-as-noisy--verify-downstream)
6. [Pin your configuration explicitly](#6-pin-your-configuration-explicitly)
7. [Use the cache deliberately](#7-use-the-cache-deliberately)
8. [Make failures observable](#8-make-failures-observable)
9. [Deploy defensively](#9-deploy-defensively)
10. [Keep the jar healthy](#10-keep-the-jar-healthy)
11. [Security & privacy](#11-security--privacy)
12. [Legal & ethical use](#12-legal--ethical-use)
13. [Test your integration without the network](#13-test-your-integration-without-the-network)
14. [Quick checklist](#14-quick-checklist)

---

## 1. Choosing a backend

**Rule:** pick the backend that matches where the code runs.

- **Interactive / one-off scripts → `auto` (the default).** It self-heals: when the jar goes stale it transparently re-mints via the browser. You never think about cookies.
- **Production hot paths → `req`, after an explicit `warmup()`.** `req` never launches Chrome, so a request handler can't block for 30 seconds on a cold start. The tradeoff: `req` returns `None` on a stale jar instead of self-healing, so you must refresh the jar out-of-band (see [§10](#10-keep-the-jar-healthy)).
- **Forcing a fresh pass / debugging rendering → `browser`.** Always drives Chrome, and re-mints the jar *on a successful render* — a gated/degraded page drives Chrome but deliberately won't overwrite a good jar with junk cookies.

**Why:** `auto` optimizes for "just works," `req` optimizes for "never surprises latency," `browser` optimizes for "I want a fresh browser pass right now." They are not interchangeable in production.

```python
# interactive
glens.search("photo.jpg")                 # auto

# server hot path
glens.warmup()                            # once at startup
glens.search(img_bytes, backend="req")    # per request — no Chrome, ever
```

---

## 2. Separate minting from serving

**Rule:** mint the session where Chrome lives; serve where it doesn't.

The fast path is pure `requests`, so the machine answering requests never needs Chrome or Selenium. Mint the jar on a Chrome-equipped machine (or a build step), ship the small `cookies.json`, and serve with the slim core.

```bash
# builder (has Chrome)
pip install "chrome-lens-search[browser]"
glens warmup                 # writes ~/.cache/glens/cookies.json

# server (no Chrome)
pip install chrome-lens-search
export GLENS_COOKIE_FILE=/opt/app/glens-cookies.json
export GLENS_BACKEND=req
```

**Why:** it keeps your production image slim and Chrome-free, and makes the one slow, fragile step (browser cold-start) a controlled build/ops concern instead of something that can fire mid-request. Full recipes in [Guide §7](GUIDE.md#7-deployment-recipes).

---

## 3. Be gentle — respect the endpoint

**Rule:** one image per lookup, don't parallelize to blast Google, and add your own pacing for bulk work.

glens already serializes every lookup through an in-process lock, so `search()` from multiple threads queues rather than stampedes. That protects a single process — it does **not** stop you from launching 50 processes. Don't.

For bulk jobs, go one at a time and consider a small delay:

```python
import time, glens

for path in images:
    r = glens.search(path, backend="req")
    handle(r)
    time.sleep(1.0)          # optional pacing for large batches
```

**Why:** this is an undocumented endpoint with no rate-limit contract. Aggressive use ages your session jar faster (rapid uploads can stale a jar within an hour) and risks `403`/`429` blocks. Recall doesn't improve by going faster; it just gets you throttled.

---

## 4. Handle `None` everywhere

**Rule:** every `search()` result is `LensResult | None`. Branch on it before touching fields.

```python
r = glens.search(path)
if not r:
    metrics.increment("glens.no_result")
    return
use(r.top_title, r.matches)
```

`search()` **does not raise** for ordinary failures (missing file, dead URL, stale jar under `req`, network error, nothing matched) — it returns `None`. The one exception is `ValueError` for an invalid `backend=` value, which is a bug in your call, not a runtime condition; don't wrap normal calls in try/except for it — just pass a valid backend.

For batches, use `search_many()`, which turns a failed lookup into a `None` slot instead of aborting the whole run:

```python
results = glens.search_many(paths, backend="req")
ok = [r for r in results if r]
```

---

## 5. Treat results as noisy — verify downstream

**Rule:** glens favors recall. If you need precision, filter.

Lens surfaces the exact product *and* visually-similar/loosely-related links. That's deliberate — noise only adds recall. Don't treat `matches[0]` as ground truth for anything consequential.

Good filtering strategies:

```python
# 1. Restrict to known retailers
RETAILERS = {"nike.com", "stockx.com", "goat.com", "amazon.com"}
hits = [m for m in r.matches if m.domain in RETAILERS]

# 2. Use frequency ranking as a confidence signal
#    top_title repeats across tiles, so it's the strongest single guess
best_guess = r.top_title

# 3. Cross-check the raw harvest when you need maximum recall
all_links = [a["href"] for a in r.raw_anchors]
```

**Why:** `matches` are deduped and cleaned, but Google's own relevance ordering is imperfect and the parser is intentionally selector-light. For automated decisions, corroborate (multiple matches agreeing, a title appearing in `titles`, a domain you trust).

---

## 6. Pin your configuration explicitly

**Rule:** in code you deploy, pass the arguments that matter rather than relying on ambient env vars.

Precedence is **argument > env var > default**. Env vars are great for ops overrides, but a service should be explicit about the things it depends on so behavior doesn't change because someone set `GLENS_BACKEND` in a shell:

```python
r = glens.search(
    img,
    backend="req",                 # don't inherit an ambient GLENS_BACKEND
    cookie_file="/opt/app/jar.json",
    max_matches=10,
)
```

**Why:** reproducibility. The env-var defaults are a convenience for interactive use and ops toggles; load-bearing behavior in a deployed service should be visible in the code.

---

## 7. Use the cache deliberately

**Rule:** turn caching on for repeated/identical images; leave it off for a stream of unique ones.

```python
glens.search(img, use_cache=True)      # dev loops, retries, re-processing
```

- The cache key is `sha256(image_bytes) + lang`, so it only helps when you see the **same bytes** again. A pipeline of all-unique images gets nothing but disk writes.
- Entries expire after `GLENS_CACHE_TTL` (24 h default) and there's **no eviction beyond TTL** — if you cache millions of distinct images, prune `GLENS_CACHE_DIR` yourself.
- `max_matches`/`max_titles` are applied *after* the cache, so you can cache once and re-shape freely.
- `backend="browser"` never *reads* the cache (it always re-mints), but a successful browser run *refreshes* it.

**Why:** caching's whole value is avoiding a re-upload of an image you've already searched. On unique inputs that never happens, so it's pure overhead.

---

## 8. Make failures observable

**Rule:** wire up the `glens` logger in anything long-running.

glens is silent by default (no handlers). In a service, attach it to your logging so you can see re-mints, fallbacks, and blocks:

```python
import logging
logging.getLogger("glens").setLevel(logging.INFO)
```

> Two of the signals below — the JS-gate self-heal and the `req` HTTP-error fallback — log at **INFO**, so this only surfaces them if a handler actually *passes* INFO through. In a service whose root handler filters at `WARNING`, raising the logger's level alone changes nothing; lower the handler too, or standalone just use `logging.basicConfig(level=logging.INFO)`.

Watch for these signals:

- `JS-gate shell served (stale/weak jar); falling back` — normal self-heal (on `auto`); a **cluster** of these means your jar keeps going stale — mint less aggressively or refresh proactively.
- `req backend: HTTP error (403/429...)` — you're being throttled/blocked; back off.
- `headless Chrome got a degraded page; retrying...with a visible window` — expected on desktops; a **problem on headless servers** (see [§9](#9-deploy-defensively)).
- `browser backend unavailable` — the `[browser]` extra isn't installed where a mint was needed.

Turn a run of `None` results into an alert, not a silent zero.

**Why:** the failure modes of a scraper are operational, not exceptional. They show up in logs and metrics, not stack traces — so you have to look.

---

## 9. Deploy defensively

**Rule:** on a headless server, set `GLENS_HEADLESS=1`, and prefer a pre-minted jar.

```bash
export GLENS_HEADLESS=1        # keep Chrome headless AND disable the visible-window retry
```

By default, if headless Chrome gets a degraded page, glens retries once with a **visible** window — which fails on a box with no display. Setting `GLENS_HEADLESS=1` disables that retry. If headless itself gets gated in your environment, run minting under `xvfb-run` so a virtual display exists — or, better, mint elsewhere and copy the jar ([§2](#2-separate-minting-from-serving)).

Other deploy rules:

- **Give the jar a stable, writable path** via `GLENS_COOKIE_FILE` — not a container layer that resets on restart.
- **Health-check with `glens status`** (exit 0 = fast path ready) or `glens.status()["req_ready"]` in a readiness probe.
- **Warm up at start, not on first request**, so no user eats the cold-start latency.

**Why:** the browser cold-start is the one slow, environment-sensitive step. Everything here moves it off the request path and out of surprising places.

---

## 10. Keep the jar healthy

**Rule:** if you serve with `req`, refresh the jar on a schedule; don't wait for it to fail.

`req` doesn't self-heal — a stale jar just returns `None`. Two good patterns:

```python
# A) Proactive scheduled refresh (cron / worker):
glens.warmup(force=True)       # re-mint before the old cookies expire

# B) Reactive: escalate after a run of failures
fails = 0
for img in stream:
    r = glens.search(img, backend="req")
    if r is None:
        fails += 1
        if fails >= 5:
            glens.warmup(force=True)   # re-mint, then continue
            fails = 0
    else:
        fails = 0
        handle(r)
```

Or simply use `backend="auto"` in the loop and accept that an occasional request pays the re-mint cost — often the simplest correct choice.

**Why:** `status()["req_ready"]` reports the jar *exists and is well-formed*, not that its cookies are still valid on Google's side. Only a live lookup reveals staleness, so a `req`-only design must have a refresh strategy.

---

## 11. Security & privacy

**Rule:** treat the cookie jar as a secret, and know where images go.

- **The jar (`cookies.json`) carries live Google session cookies.** Anyone with the file can replay your session. Store it with restrictive permissions, keep it out of version control (it lives under `~/.cache` by default, well away from your repo), and don't log its contents.
- **Images are uploaded to Google**, exactly as the Google Lens web UI does — and only to Google, never a third-party host. Don't feed it images you're not comfortable sending to Google.
- **glens has no telemetry** and never phones home. The only outbound traffic is to `google.com` (and, for URL inputs, the image URL you provide).
- If you share a jar across machines ([§2](#2-separate-minting-from-serving)), move it over a secure channel (scp/secrets manager), not a public bucket.

---

## 12. Legal & ethical use

**Rule:** know what you're running before you ship it.

- This scrapes an **undocumented** endpoint and is **against Google's Terms of Service**. Whether that's acceptable is your call and depends on your jurisdiction and use case.
- **Keep the "unofficial" framing** if you redistribute or build on it — it is not affiliated with or endorsed by Google.
- **Don't build abuse on top of it:** no mass-scraping campaigns, no reselling Google's results as an "official" API, no stripping the disclaimers.
- Expect breakage and design for it — this is a research/utility tool, not a durable SLA-backed dependency.

---

## 13. Test your integration without the network

**Rule:** don't hit live Google in your own test suite. Monkeypatch `search`, or the backend functions, and assert on synthetic data.

glens's own tests are fully hermetic (no network, no browser). Do the same in yours:

```python
import glens

def test_my_pipeline(monkeypatch):
    fake = glens.LensResult(
        top_title="Acme Runner",
        titles=["Acme Runner"],
        matches=[glens.Match(title="Acme Runner",
                             url="https://acme.example/p", domain="acme.example")],
    )
    monkeypatch.setattr(glens, "search", lambda *a, **k: fake)
    assert my_pipeline("whatever.jpg") == "acme.example"
```

For lower-level tests, `glens.LensResult` and `glens.Match` are plain dataclasses you can construct directly, and `result.to_dict()` gives you a stable serialization to assert against.

**Why:** live-Google tests are flaky (rate limits, markup drift, jar staleness), slow, and CI-hostile. Your pipeline logic doesn't need real results to be tested — it needs *deterministic* ones.

---

## 14. Quick checklist

Before you ship glens in something real:

- [ ] Backend chosen on purpose: `auto` for convenience, `req` + `warmup()` for hot paths.
- [ ] Every `search()` call handles `None`.
- [ ] `warmup()` runs at startup, not on the first user request.
- [ ] A jar-refresh strategy exists if you serve with `req` (scheduled or reactive).
- [ ] `GLENS_COOKIE_FILE` points at a stable, writable, **secret** location.
- [ ] On a headless server: `GLENS_HEADLESS=1` (or mint elsewhere and copy the jar).
- [ ] The `glens` logger is wired into your logging; a run of `None`s alerts.
- [ ] Results are filtered/corroborated, not trusted blindly.
- [ ] Your tests monkeypatch glens — no live-Google calls in CI.
- [ ] You've read the [caveats](GUIDE.md#17-limitations--caveats) and accept the ToS/breakage reality.

---

*See also: the [Guide](GUIDE.md) for concepts and deployment recipes, and the [API Reference](API.md) for exact signatures.*
