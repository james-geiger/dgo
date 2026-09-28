# /// script
# requires-python = ">=3.11"
# dependencies = ["linkml"]
# ///
"""Build dgo.owl.ttl from the LinkML schema. Run: uv run build.py"""
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
ttl = OwlSchemaGenerator(
    SCHEMA,
    use_native_uris=False,
    # The Python class defaults both of these to True; the gen-owl CLI defaults
    # them to False. True would model LinkML types as classes (dates as things,
    # not values) and type every class/slot as an instance of
    # linkml:ClassDefinition / linkml:SlotDefinition (OWL punning).
    type_objects=False,
    metaclasses=False,
).serialize()
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

RDF, RDFS, OWL = rdflib.RDF, rdflib.RDFS, rdflib.OWL
IAO_DEF_P = rdflib.URIRef("http://purl.obolibrary.org/obo/IAO_0000115")
def drop_restrictions(prop, keep=lambda r: False):
    for r in list(g.subjects(OWL.onProperty, prop)):
        if not keep(r):
            g.remove((None, RDFS.subClassOf, r))
            drop(r)

# rdf:type is reserved RDF vocabulary. The `type` slot only lets instance data
# name a subclass (designates_type); owlgen still declares it as a property and
# restricts it, so remove both.
drop(RDF.type)
drop_restrictions(RDF.type)

# Annotation properties. A slot marked `implements: [owl:AnnotationProperty]`
# is declared as one by owlgen, but owlgen still writes class restrictions,
# blank-node ranges/domains and property characteristics for it (see add_slot
# and the per-slot loop in owlgen). OWL 2 allows none of those on an annotation
# property; the OWL API turns each such restriction into an "ErrorN" class.
# Also treated as annotations whatever owlgen says:
#  - OWL/RDFS built-in annotation properties (rdfs:label, ...). Their owlgen
#    declarations are removed entirely: the ontology must not redefine them.
#  - IAO:0000115, which this build uses for every class definition.
BUILTIN_ANNOTATIONS = {RDFS.label, RDFS.comment, RDFS.seeAlso, RDFS.isDefinedBy,
                       OWL.deprecated, OWL.versionInfo, OWL.priorVersion,
                       OWL.backwardCompatibleWith, OWL.incompatibleWith}
NOT_FOR_ANNOTATIONS = {OWL.ObjectProperty, OWL.DatatypeProperty, OWL.FunctionalProperty,
                       OWL.InverseFunctionalProperty, OWL.TransitiveProperty,
                       OWL.SymmetricProperty, OWL.AsymmetricProperty,
                       OWL.ReflexiveProperty, OWL.IrreflexiveProperty}
annotation_props = (set(g.subjects(RDF.type, OWL.AnnotationProperty))
                    | BUILTIN_ANNOTATIONS | {IAO_DEF_P})
for prop in annotation_props:
    drop_restrictions(prop)
    if prop in BUILTIN_ANNOTATIONS:
        drop(prop)
        continue
    for kind in NOT_FOR_ANNOTATIONS:
        g.remove((prop, RDF.type, kind))
    for axis in (RDFS.range, RDFS.domain):
        for o in list(g.objects(prop, axis)):
            if isinstance(o, rdflib.BNode):   # e.g. a union from any_of
                g.remove((prop, axis, o))
                drop(o)
    g.remove((prop, OWL.inverseOf, None))
    g.add((prop, RDF.type, OWL.AnnotationProperty))

# Transitive properties (part_of) need their restrictions rewritten:
#  - owlgen writes a slot's range as allValuesFrom ("everything this term is part
#    of is a subject area"). Through transitivity that also covers the domain the
#    subject area is in, so a reasoner would conclude the domain is a subject area.
#    Widen the filler to its nearest BFO/IAO ancestor instead ("a term is part of
#    only information content entities"): true all the way up the chain, and it
#    still catches category errors such as a term being part of a process.
#  - Required slots also get someValuesFrom with the original range ("every term
#    is part of some subject area").
#  - OWL 2 DL forbids cardinality restrictions on transitive properties.
# LinkML still checks the exact range, required and single-valued on YAML.
CARD = {rdflib.OWL.minCardinality, rdflib.OWL.maxCardinality, rdflib.OWL.cardinality,
        rdflib.OWL.minQualifiedCardinality, rdflib.OWL.maxQualifiedCardinality,
        rdflib.OWL.qualifiedCardinality}
UPPER = ("http://purl.obolibrary.org/obo/BFO_", "http://purl.obolibrary.org/obo/IAO_")
def upper_ancestor(cls):
    seen, queue = set(), [cls]
    while queue:
        c = queue.pop(0)
        if str(c).startswith(UPPER):
            return c
        if c in seen:
            continue
        seen.add(c)
        queue += [p for p in g.objects(c, RDFS.subClassOf) if not isinstance(p, rdflib.BNode)]
    return None
def min_cardinality(restriction) -> int:
    """The restriction's owl:minCardinality, or 0 if it has none."""
    value = g.value(restriction, OWL.minCardinality)
    return int(value.toPython()) if isinstance(value, rdflib.Literal) else 0

for prop in set(g.subjects(rdflib.RDF.type, OWL.TransitiveProperty)):
    for r in list(g.subjects(OWL.onProperty, prop)):
        filler = g.value(r, OWL.allValuesFrom)
        if filler is None:
            continue
        for cls in list(g.subjects(RDFS.subClassOf, r)):
            required = any(
                (cls, RDFS.subClassOf, m) in g and min_cardinality(m) > 0
                for m in g.subjects(OWL.onProperty, prop))
            if required:
                some = rdflib.BNode()
                g.add((some, rdflib.RDF.type, OWL.Restriction))
                g.add((some, OWL.onProperty, prop))
                g.add((some, OWL.someValuesFrom, filler))
                g.add((cls, RDFS.subClassOf, some))
        top = upper_ancestor(filler)
        g.remove((r, OWL.allValuesFrom, filler))
        if top is not None:
            g.add((r, OWL.allValuesFrom, top))
    drop_restrictions(prop, keep=lambda r: not any((r, c, None) in g for c in CARD)
                      and ((r, OWL.someValuesFrom, None) in g or (r, OWL.allValuesFrom, None) in g))
ttl = g.serialize(format="turtle")
Path(OUT).write_text(ttl)
print(f"✓ wrote {OUT} ({len(where)} ids checked)")

sys.exit(0)