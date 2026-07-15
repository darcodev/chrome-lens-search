# Changelog

Notable changes to chrome-lens-search.

## 0.1.0

First public release.

- Google Lens visual product search over plain HTTP: image in, exact product titles + merchant links out (not just OCR).
- Fast/slow hybrid: one browser run mints a Google session, then every lookup runs over plain `requests` (~2s) with no browser in the hot path. Self-heals when the session goes stale.
- Python API (`import glens`): `search`, `search_many`, `warmup`, `status`, plus the `LensResult` and `Match` dataclasses.
- `glens` command-line tool with `search`, `warmup`, and `status` subcommands, JSON output, and scriptable exit codes.
- Opt-in result cache and best-effort language preference.
