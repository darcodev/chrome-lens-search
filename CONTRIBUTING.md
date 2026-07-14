# Contributing

Thanks for helping keep an unofficial scraper alive. Ground rules first, then
the practical bits.

## The one thing to understand

This library scrapes an **undocumented Google endpoint**. Google changes the
endpoint, the JS-gate behaviour, and the markup without notice, so **it WILL
break** from time to time. That's expected; the project is built to degrade
rather than shatter (selector-light parsing, `req` → `browser` fallback), and
fixing breaks is the main form of contribution.

## Reporting a break

Open an issue with:

1. **What you ran** — backend (`auto`/`req`/`browser`), library version, OS,
   Python version.
2. **What happened** — enable logging first and paste the `glens:` log lines:

   ```python
   import logging
   logging.basicConfig(level=logging.INFO)
   ```

   or run the CLI with `-v`: `glens search photo.jpg -v`.
3. **What the failure looks like** — e.g. "req always falls back", "browser
   harvests 0 anchors", "upload no longer redirects to `udm=26`".

Please **describe** the response you got rather than attaching full HTML dumps —
saved Google pages are Google's copyrighted content and can carry your session
cookies.

## Non-negotiable project rules

These keep the project safe to host and MIT-clean. PRs that violate them will
be declined:

- **No Google assets in the repo — ever.** That includes test fixtures: no saved
  Lens response HTML/JSON, no logos, no UI screenshots. All HTML fixtures must
  be **synthetic** — hand-written minimal markup that mimics the *shape*
  (`<a href>` + `aria-label`) but contains no Google content. See `tests/` for
  the pattern.
- **Stay unofficial.** The trademark disclaimer and "unofficial, not affiliated"
  framing in the README and package metadata must remain intact.
- **No monetization.** No paid tiers, access keys, ads, or telemetry/analytics.
- **Core depends on `requests` only.** `selenium` / `undetected-chromedriver`
  live in the optional `[browser]` extra and are imported lazily. No
  app-specific coupling.
- **Selector-light parsing.** Harvest anchors + aria-labels; never hardcode
  Google's rotating CSS class names.
- **`search()` never raises** for ordinary network/parse failures — it returns
  `None`.
- **Python ≥ 3.10.**

## Development setup

```bash
git clone https://github.com/darcodev/chrome-lens-search
cd chrome-lens-search
pip install -e ".[dev]"
pytest            # must pass with NO network and NO browser
ruff check .
```

The test suite is hermetic by design: if your new test needs the network or
Chrome, restructure it around the pure functions in `glens/lens.py` (parsing,
title cleaning, result shaping) or monkeypatch the backend functions like the
existing tests do.

To try the real thing end-to-end (needs Chrome):

```bash
pip install -e ".[dev,browser]"
glens search path/to/photo.jpg -v
```

## Releasing (maintainer only)

Not automated on purpose — run locally with your own PyPI account:

```bash
pip install build twine
python -m build            # dist/*.whl + dist/*.tar.gz
twine check dist/*
twine upload dist/*
```

Bump `version` in `pyproject.toml` **and** `__version__` in
`glens/__init__.py` together before building.
