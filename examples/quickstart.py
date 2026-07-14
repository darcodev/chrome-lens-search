"""Minimal end-to-end example.

Usage:
    python examples/quickstart.py path/to/image.jpg

First run launches Chrome once to mint a JS-verified google.com cookie jar;
every run after that is a plain-requests HTTP call (fast, no browser window)
until the jar goes stale, at which point the browser re-mints it automatically.
"""

import logging
import sys

import glens

logging.basicConfig(level=logging.INFO, format="%(message)s")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python examples/quickstart.py <image>")
        raise SystemExit(1)

    result = glens.search(sys.argv[1])
    if not result:
        print("No results (lookup failed or nothing matched).")
        return

    print("\nTop title:", result.top_title)
    print("\nRanked titles:")
    for t in result.titles:
        print("  -", t)

    print("\nMatches:")
    for m in result.matches:
        print(f"  [{m.domain}] {m.title}\n      {m.url}")


if __name__ == "__main__":
    main()
