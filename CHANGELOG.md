# Changelog

Notable changes to chrome-lens-search.

## Unreleased

- Fixed: **lookups work again.** Google retired `www.google.com/searchbyimage/upload`, which now answers 404 for every upload — both backends failed on every image. Uploads go to Lens' own endpoint (`https://lens.google.com/v3/upload`) instead.
- Fixed: the browser backend uploads by submitting an in-page multipart form rather than calling `fetch`. The upload endpoint is on `lens.google.com` while Google lands the session on `www.google.com`, so the `fetch` was cross-origin and its response came back CORS-opaque (`TypeError: Failed to fetch`); a form submission is a navigation, which CORS doesn't apply to.
- Fixed: the browser backend no longer throws away every merchant link on a page where Google wraps its outbound links (`google.com/url?q=<merchant>`). Those wrappers were only unwrapped on the `req` path, so the browser pass — the self-healing fallback — judged each wrapped link by `google.com`'s host and discarded it, reporting no matches for a page full of products. Its wait-for-tiles check was blind to the same wrappers and burned the full timeout before harvesting.
- Fixed: a blank `GLENS_BACKEND` (`-e GLENS_BACKEND=` in a container, an empty line in a `.env`) is treated as "not configured" and falls back to `auto`, like every other `GLENS_*` default. It previously became an empty backend that failed validation on every lookup, raising `ValueError` from `search()` and exiting 2 from the CLI.
- Fixed: `warmup(force=True)` (and `glens warmup --force`) reports failure when the re-mint fails. It only checked whether *a* jar existed afterwards, so the stale jar it was asked to replace counted as success — telling a deploy or CI check the session had been refreshed when it hadn't.
- Fixed: `_unwrap_google_redirect` only unwraps Google's own redirect wrapper; a merchant URL with its own `/url?q=` route keeps the link that was actually harvested.
- Fixed: a merchant link is no longer dropped when it carries a Google token in its path or query (an affiliate tag like `?utm_source=google.com`, a `&url=` redirect target, a `ved` param). External links are now judged by their host, not by a substring scan of the whole URL.
- Fixed: `status()` now reports `jar_age_seconds` as `None` for a jar that has no `minted_at` timestamp (for example, one copied from another machine), instead of a bogus multi-decade age.

## 0.1.0

First public release.

- Google Lens visual product search over plain HTTP: image in, exact product titles + merchant links out (not just OCR).
- Fast/slow hybrid: one browser run mints a Google session, then every lookup runs over plain `requests` (~2s) with no browser in the hot path. Self-heals when the session goes stale.
- Python API (`import glens`): `search`, `search_many`, `warmup`, `status`, plus the `LensResult` and `Match` dataclasses.
- `glens` command-line tool with `search`, `warmup`, and `status` subcommands, JSON output, and scriptable exit codes.
- Opt-in result cache and best-effort language preference.
