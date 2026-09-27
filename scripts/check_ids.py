#!/usr/bin/env python3
"""Fail if any minted numeric DGO id appears more than once in the source.

Each minted id (e.g. `dgo:DGO_00000011`) should be assigned exactly once — as
an `enum_uri`, `class_uri`, `slot_uri`, or a permissible-value `meaning`.
Class hierarchy is expressed by *name* (`is_a: role`), never by re-using an id,
so any id that shows up two or more times across the schema sources is a
collision.

This is a plain textual scan (no linkml dependency) so it runs under the system
`python3` and in CI. It reports every occurrence as `file:line`.

Usage:
    python3 scripts/check_ids.py src               # scan a directory tree
    python3 scripts/check_ids.py src/dgo.yaml ...  # or explicit files
    python3 scripts/check_ids.py src --pattern 'DGO_[0-9]+'
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path


def iter_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files.extend(sorted(p.rglob("*.yaml")))
            files.extend(sorted(p.rglob("*.yml")))
        elif p.is_file():
            files.append(p)
        else:
            print(f"warning: {p} not found", file=sys.stderr)
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="files or directories to scan")
    ap.add_argument(
        "--pattern",
        default=r"DGO_[0-9]+",
        help="regex identifying minted ids (default: DGO_[0-9]+)",
    )
    args = ap.parse_args()

    pat = re.compile(args.pattern)
    where: dict[str, list[str]] = defaultdict(list)

    for f in iter_files(args.paths):
        for lineno, line in enumerate(f.read_text().splitlines(), 1):
            for m in pat.finditer(line):
                where[m.group(0)].append(f"{f}:{lineno}")

    collisions = {i: locs for i, locs in where.items() if len(locs) > 1}

    if collisions:
        print(f"✗ {len(collisions)} colliding id(s) of {len(where)} found:", file=sys.stderr)
        for i in sorted(collisions):
            print(f"  {i}", file=sys.stderr)
            for loc in collisions[i]:
                print(f"      {loc}", file=sys.stderr)
        return 1

    print(f"✓ no id collisions ({len(where)} unique ids checked, /{args.pattern}/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
