#!/usr/bin/env python3
"""Rewrite owlgen's `owl:imports` string literals as proper IRIs.

owlgen (LinkML) emits schema `annotations` values as string literals, so an
`owl:imports` annotation comes out as `owl:imports "http://..."` — or, for a
list of imports, as a single stringified Python list. Neither is a valid OWL
import. This filter finds each `owl:imports "..."` object, extracts every URL
inside it, and rewrites them as comma-separated IRIs (valid Turtle).

Usage:
    gen-owl src/dgo.yaml | python3 scripts/fix_imports.py > dgo.owl.ttl
"""
import re
import sys

_URL = re.compile(r'https?://[^\s"\',\]]+')

# <indent>owl:imports "<body>" <trailing ; or .>
_LINE = re.compile(
    r'(?P<pre>^[ \t]*)owl:imports[ \t]+"(?P<body>[^"]*)"(?P<post>[ \t]*[;.])',
    re.MULTILINE,
)


def _repl(m: "re.Match[str]") -> str:
    urls = _URL.findall(m.group("body"))
    if not urls:
        return m.group(0)
    objs = ", ".join(f"<{u}>" for u in urls)
    return f"{m.group('pre')}owl:imports {objs}{m.group('post')}"


def main() -> None:
    sys.stdout.write(_LINE.sub(_repl, sys.stdin.read()))


if __name__ == "__main__":
    main()
