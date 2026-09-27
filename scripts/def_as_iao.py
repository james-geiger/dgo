#!/usr/bin/env python3
"""Rewrite `skos:definition` predicates to the OBO definition property IAO:0000115.

owlgen maps a LinkML element's `description` to `skos:definition` (its metamodel
`slot_uri`). For a BFO/OBO-aligned ontology the conventional textual-definition
property is `IAO:0000115` ("definition"). This filter rewrites the predicate
only (subjects and the literal objects are untouched), emitting a full IRI so it
does not depend on an `iao:`/`obo:` prefix being declared.

Run it after generation (default owlgen profile, i.e. NOT --metadata-profile rdfs):
    gen-owl src/dgo.yaml --no-use-native-uris \
        | python3 scripts/fix_imports.py \
        | python3 scripts/def_as_iao.py > dgo.owl.ttl
"""
import re
import sys

IAO_DEFINITION = "<http://purl.obolibrary.org/obo/IAO_0000115>"

# `skos:definition` only ever appears as a predicate in owlgen output.
_PRED = re.compile(r"\bskos:definition\b")


def main() -> None:
    sys.stdout.write(_PRED.sub(IAO_DEFINITION, sys.stdin.read()))


if __name__ == "__main__":
    main()
