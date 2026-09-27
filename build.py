# /// script
# requires-python = ">=3.11"
# dependencies = ["linkml"]
# ///
"""Build dgo.owl.ttl and dist/dgo-template.yaml, then validate examples/. Run: uv run build.py"""
import re, sys
from collections import defaultdict
from pathlib import Path
from linkml.generators.owlgen import OwlSchemaGenerator
import rdflib

SCHEMA = "src/dgo.yaml"
OUT = "dgo.owl.ttl"
TEMPLATE = "src/dgo-template.yaml"
DIST = "dist/dgo-template.yaml"
ID = re.compile(r"DGO_[0-9]+")
IAO_DEF = "<http://purl.obolibrary.org/obo/IAO_0000115>"

# 1. gate: no duplicate minted ids
where = defaultdict(list)
for f in Path("src").rglob("*.yaml"):
    for n, line in enumerate(f.read_text().splitlines(), 1):
        for m in ID.finditer(line):
            where[m.group(0)].append(f"{f}:{n}")
dupes = {i: locs for i, locs in where.items() if len(locs) > 1}
if dupes:
    for i, locs in sorted(dupes.items()):
        print(f"✗ {i}: {', '.join(locs)}", file=sys.stderr)
    sys.exit(1)

# 2. generate (default profile -> skos:definition), 3. rewrite imports + definitions
ttl = OwlSchemaGenerator(SCHEMA, use_native_uris=False).serialize()
ttl = re.sub(r'(^[ \t]*)owl:imports[ \t]+"([^"]*)"([ \t]*[;.])',
             lambda m: m[1] + "owl:imports " +
                       ", ".join(f"<{u}>" for u in re.findall(r'https?://[^\s",\]]+', m[2])) + m[3],
             ttl, flags=re.M)
ttl = re.sub(r"\bskos:definition\b", IAO_DEF, ttl)

g = rdflib.Graph().parse(data=ttl, format="turtle")
# drop the `identifiable` mixin (data scaffolding): owlgen camelCases it to
# dgo:Identifiable. Remove its node, its restriction bnodes, and inbound edges.
ident = rdflib.URIRef("https://w3id.org/dgo/Identifiable")
def drop(node):
    for _, _, o in list(g.triples((node, None, None))):
        g.remove((node, None, o))
        if isinstance(o, rdflib.BNode) and not any(g.triples((None, None, o))):
            drop(o)
drop(ident)
g.remove((None, None, ident))

sys.exit(0)