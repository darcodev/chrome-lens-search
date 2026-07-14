"""Command-line interface: ``glens photo.jpg`` (or ``glens search photo.jpg``).

Pretty-prints by default; ``--json`` emits machine-readable output. Exit codes:
0 = results found, 1 = no results (lookup failed or nothing matched),
2 = bad usage (argparse, bad config, or an unreadable image path).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .lens import (
    DEFAULT_BACKEND,
    DEFAULT_CACHE_TTL,
    DEFAULT_LANG,
    DEFAULT_USE_CACHE,
    LensResult,
    _cache_key,
    _cache_read,
    _default_cookie_file,
    _download_image,
    _load_cookie_jar,
    search,
    status,
    warmup,
)


def _normalise_argv(argv: list[str] | None) -> list[str]:
    """Accept the legacy explicit-subcommand form: ``glens search photo.jpg``."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "search":
        argv = argv[1:]
    return argv


def _browser_extra_missing() -> bool:
    return importlib.util.find_spec("undetected_chromedriver") is None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glens",
        description="Unofficial Google Lens visual product search: image in, "
                    "product titles + merchant links out.",
        epilog="examples:  glens photo.jpg   |   glens --json https://... "
               "  |   glens warmup   |   glens status",
    )
    parser.add_argument("--version", action="version", version=f"glens {__version__}")
    parser.add_argument("image", help="path or http(s) URL of the image")
    parser.add_argument("--backend", choices=("auto", "req", "browser"), default=DEFAULT_BACKEND,
                        help="lookup backend (default: %(default)s; env GLENS_BACKEND)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="emit JSON instead of pretty text")
    parser.add_argument("--raw", action="store_true",
                        help="include every harvested anchor in the JSON output")
    parser.add_argument("--max-matches", type=int, default=None, metavar="N",
                        help="cap on external merchant links returned")
    parser.add_argument("--max-titles", type=int, default=None, metavar="N",
                        help="cap on frequency-ranked product titles returned")
    parser.add_argument("--timeout", type=int, default=None, metavar="SECONDS",
                        help="browser-backend wait for result tiles")
    parser.add_argument("--lang", default=None, metavar="LANG",
                        help='preferred response language, e.g. "de", "pt-BR" '
                             "(Accept-Language; best-effort; env GLENS_LANG)")
    parser.add_argument("--cache", action="store_true",
                        help="reuse recent results for the same image+locale "
                             "(24h TTL; env GLENS_CACHE/GLENS_CACHE_TTL)")
    parser.add_argument("--cookie-file", default=None, metavar="PATH",
                        help="where the browser-minted cookie jar lives")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show progress logging on stderr")
    return parser


def _print_pretty(result: LensResult, show_internal_url: bool = False) -> None:
    print(f"Top title: {result.top_title or '(none)'}")
    if result.titles:
        print("\nRanked titles:")
        for i, title in enumerate(result.titles, 1):
            print(f"  {i}. {title}")
    if result.matches:
        print(f"\nMatches ({len(result.matches)}):")
        for m in result.matches:
            print(f"  [{m.domain}] {m.title}")
            print(f"      {m.url}")
    # results_url is session-bound (vsrid/gsessionid) and won't render in an
    # ordinary browser, so don't dangle it as a "results page" by default. Show
    # it only under -v, clearly labelled as an internal/debug handle.
    if show_internal_url and result.results_url:
        print(f"\n(internal results URL, session-bound; not browser-openable):"
              f"\n{result.results_url}")


def _print_json(result: LensResult, include_raw: bool) -> None:
    print(json.dumps(result.to_dict(raw_anchors=include_raw), indent=2, ensure_ascii=False))


def _first_run_hint(backend: str, had_jar: bool) -> str | None:
    """An actionable next step when a lookup fails before the session jar exists."""
    if had_jar:
        return None
    if _browser_extra_missing():
        return (
            "Setup needed: the FIRST lookup uses Chrome once to create a reusable "
            "Google session, and the browser extra isn't installed.\n"
            'Fix:  pip install "chrome-lens-search[browser]"   '
            "(after the first run, lookups are plain HTTP)"
        )
    if backend == "req":
        return ("No session jar yet: run once without --backend req so Chrome can "
                "create it; the req fast path works from then on.")
    return "The browser cold-start failed; re-run with -v to see the progress logs."


def _force_utf8_output() -> None:
    """Product titles are routinely non-ASCII; on Windows a redirected stdout
    defaults to the legacy ANSI code page and print() would crash. Best-effort."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _cmd_status(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="glens status",
        description="Show whether the fast HTTP path is ready (exit 0 = ready).",
    )
    parser.add_argument("--cookie-file", default=None, metavar="PATH")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    _force_utf8_output()

    info = status(cookie_file=args.cookie_file)
    if args.as_json:
        print(json.dumps(info, indent=2))
    else:
        for key, value in info.items():
            print(f"{key}: {value}")
    return 0 if info["req_ready"] else 1


def _cmd_warmup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="glens warmup",
        description="Launch Chrome once to mint the session jar, so later "
                    "lookups run over plain HTTP. Deploy-time counterpart of a search.",
    )
    parser.add_argument("--image", default=None, metavar="PATH_OR_URL",
                        help="optional real image to warm up with")
    parser.add_argument("--cookie-file", default=None, metavar="PATH")
    parser.add_argument("--timeout", type=int, default=None, metavar="SECONDS")
    parser.add_argument("--force", action="store_true",
                        help="re-mint even when a valid jar already exists")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _force_utf8_output()
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    if _browser_extra_missing():
        print('glens: warmup needs the browser extra: '
              'pip install "chrome-lens-search[browser]"', file=sys.stderr)
        return 2

    print("Warming up: launching Chrome once to mint the session jar...", file=sys.stderr)
    ok = warmup(image=args.image, cookie_file=args.cookie_file,
                timeout=args.timeout, force=args.force)
    if ok:
        print("Session jar ready. Lookups now run over plain HTTP.", file=sys.stderr)
        return 0
    print("Warmup failed. Re-run with -v to see the progress logs.", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "status":
        return _cmd_status(argv[1:])
    if argv and argv[0] == "warmup":
        return _cmd_warmup(argv[1:])

    args = _build_parser().parse_args(_normalise_argv(argv))
    _force_utf8_output()
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    # Resolve the image up front: a typo'd path or dead URL must produce a
    # precise error here, not a misleading backend hint after the lookup.
    if args.image.lower().startswith(("http://", "https://")):
        frame = _download_image(args.image)
        if frame is None:
            print(f"glens: could not download image: {args.image}", file=sys.stderr)
            return 1
    else:
        try:
            frame = Path(args.image).read_bytes()
        except OSError as exc:
            print(f"glens: cannot read image: {exc}", file=sys.stderr)
            return 2

    cookie_path = Path(args.cookie_file) if args.cookie_file else _default_cookie_file()
    # The library's own validity check, not a bare exists(): a corrupt/empty jar
    # must not suppress the first-run messaging.
    had_jar = _load_cookie_jar(cookie_path) is not None
    # Mirror search()'s cache decision so the notice never announces a Chrome
    # launch that a cache hit is about to make unnecessary.
    use_cache = True if args.cache else DEFAULT_USE_CACHE
    cache_hit_expected = (
        use_cache and args.backend != "browser"
        and _cache_read(_cache_key(frame, args.lang or DEFAULT_LANG),
                        DEFAULT_CACHE_TTL) is not None
    )
    if (not had_jar and not cache_hit_expected
            and args.backend in ("auto", "browser") and not _browser_extra_missing()):
        print("First run: launching Chrome once to create the fast-path session "
              "(takes a while; a window may appear briefly; later runs are "
              "plain HTTP)...", file=sys.stderr)

    try:
        result = search(
            frame,
            backend=args.backend,
            max_matches=args.max_matches,
            max_titles=args.max_titles,
            timeout=args.timeout,
            cookie_file=args.cookie_file,
            lang=args.lang,
            use_cache=True if args.cache else None,  # None -> GLENS_CACHE default
        )
    except ValueError as exc:
        # e.g. an invalid GLENS_BACKEND env value slipping past argparse, which
        # never validates defaults against choices. Usage error, not "no results".
        print(f"glens: {exc}", file=sys.stderr)
        return 2

    if not result:
        if args.as_json:
            print("null")  # keep stdout valid JSON for scripted callers
        print("No results (lookup failed or nothing matched).", file=sys.stderr)
        hint = _first_run_hint(args.backend, had_jar)
        if hint:
            print(hint, file=sys.stderr)
        return 1

    if args.as_json:
        _print_json(result, include_raw=args.raw)
    else:
        _print_pretty(result, show_internal_url=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
