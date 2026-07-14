"""Reverse-search every image in a folder and write the matches to a CSV.

Usage:
    python examples/batch_folder.py path/to/folder [results.csv]

Stdlib + glens only. The first lookup may launch Chrome once to mint the
session jar (or run `glens warmup` beforehand); the rest run over plain HTTP.
"""

import csv
import sys
from pathlib import Path

import glens

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("results.csv")
    images = sorted(p for p in folder.iterdir()
                    if p.suffix.lower() in IMAGE_SUFFIXES)
    if not images:
        print(f"No images found in {folder}")
        return 1

    if not glens.status()["req_ready"]:
        print("No session jar yet: the first lookup will launch Chrome once "
              "(or run `glens warmup` beforehand).")

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["image", "top_title", "best_match_url", "best_match_domain"])
        for path in images:
            result = glens.search(path)
            best = result.matches[0] if result and result.matches else None
            writer.writerow([
                path.name,
                result.top_title if result else "",
                best.url if best else "",
                best.domain if best else "",
            ])
            print(f"{path.name} -> {result.top_title if result else '(no results)'}")

    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
