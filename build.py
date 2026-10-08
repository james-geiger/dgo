# /// script
# requires-python = ">=3.11"
# dependencies = ["linkml"]
# ///
"""Build dist/dgo.owl.ttl and dist/dgo.yaml from the LinkML schema. Run: uv run build.py"""
import re, sys
from collections import defaultdict
from pathlib import Path
from linkml.generators.owlgen import OwlSchemaGenerator
from linkml_runtime import SchemaView
from linkml_runtime.dumpers import yaml_dumper
import rdflib

SCHEMA = "src/dgo.yaml"
OUT = "dist/dgo.owl.ttl"
DIST = "dist/dgo.yaml"
LINKML_TYPES = "https://w3id.org/linkml/types"
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

# owlgen writes a restriction from slot_usage on an inherited slot (has_output,
# narrowed by each lifecycle process) against dgo:<slot name> instead of the
# slot's slot_uri. Nothing declares that property, so the OWL API turns each
# such restriction into an "ErrorN" class. Point them at the real property.
schema_view = SchemaView(SCHEMA)
for slot in schema_view.all_slots().values():
    native = rdflib.URIRef("https://w3id.org/dgo/" + slot.name)
    real = rdflib.URIRef(schema_view.get_uri(slot, expand=True))
    if native != real:
        for r in list(g.subjects(OWL.onProperty, native)):
            g.remove((r, OWL.onProperty, native))
            g.add((r, OWL.onProperty, real))
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

# Imported terms (BFO, IAO, RO, SKOS, ...) are reused, never redefined. owlgen
# writes a reused class's slots as restrictions on it (data set is IAO_0001000
# itself), and a slot's range as a global rdfs:range on a reused property (the
# range of stored_in would make every carrier of RO "generically depends on" a
# data store). Drop both: the meaning stays in the restrictions on DGO classes,
# and LinkML still checks the slots on YAML.
def external(term):
    return isinstance(term, rdflib.URIRef) and not str(term).startswith("https://w3id.org/dgo/")
for cls in set(g.subjects(RDF.type, OWL.Class)):
    if external(cls):
        # its definition, label, scheme and place in the hierarchy are its own
        # ontology's (restating a LinkML is_a would give it a second parent
        # once that ontology moves it: IAO now puts data set under data
        # collection); and owlgen's exactMatch to the LinkML-native URI
        # (dgo:DataSet) names no real term
        g.remove((cls, IAO_DEF_P, None))
        g.remove((cls, RDFS.label, None))
        g.remove((cls, rdflib.SKOS.inScheme, None))
        g.remove((cls, rdflib.SKOS.exactMatch, None))
        for parent in [o for o in g.objects(cls, RDFS.subClassOf) if not isinstance(o, rdflib.BNode)]:
            g.remove((cls, RDFS.subClassOf, parent))
        for r in [o for o in g.objects(cls, RDFS.subClassOf) if isinstance(o, rdflib.BNode)]:
            g.remove((cls, RDFS.subClassOf, r))
            if not any(g.triples((None, None, r))):
                drop(r)
for prop in set(g.subjects(RDF.type, OWL.ObjectProperty)) | set(g.subjects(RDF.type, OWL.DatatypeProperty)):
    if external(prop):
        for axis in (RDFS.range, RDFS.domain):
            for o in list(g.objects(prop, axis)):
                g.remove((prop, axis, o))
                if isinstance(o, rdflib.BNode) and not any(g.triples((None, None, o))):
                    drop(o)

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

# Imported object properties (RO, BFO) get no cardinality restrictions either.
# OWL 2 DL forbids them on non-simple properties, and RO makes many of its
# properties non-simple through property chains (participates in, has
# participant, has output): with RO loaded, a reasoner refuses the ontology.
# Which ones are non-simple is RO's to decide and can change between releases,
# so none are restricted. A required slot keeps someValuesFrom with its range
# (or owl:Thing); LinkML still checks required and single-valued on YAML.
for prop in set(g.subjects(RDF.type, OWL.ObjectProperty)):
    if not external(prop) or (prop, RDF.type, OWL.TransitiveProperty) in g:
        continue
    for cls, r in [(c, r) for r in list(g.subjects(OWL.onProperty, prop))
                   for c in g.subjects(RDFS.subClassOf, r)]:
        if min_cardinality(r) > 0:
            filler = next((g.value(o, OWL.allValuesFrom)
                           for o in g.objects(cls, RDFS.subClassOf)
                           if (o, OWL.onProperty, prop) in g and (o, OWL.allValuesFrom, None) in g),
                          OWL.Thing)
            some = rdflib.BNode()
            g.add((some, RDF.type, OWL.Restriction))
            g.add((some, OWL.onProperty, prop))
            g.add((some, OWL.someValuesFrom, filler))
            g.add((cls, RDFS.subClassOf, some))
    drop_restrictions(prop, keep=lambda r: not any((r, c, None) in g for c in CARD))

# A restriction whose filler was the dropped `identifiable` mixin (e.g. the
# range of has_output) is left with no filler at all: not valid OWL. Drop it.
FILLERS = CARD | {OWL.allValuesFrom, OWL.someValuesFrom, OWL.hasValue}
for r in list(g.subjects(RDF.type, OWL.Restriction)):
    if not any((r, f, None) in g for f in FILLERS):
        g.remove((None, None, r))
        drop(r)

# gate: every restriction is on a declared property (an undeclared one becomes
# an "ErrorN" class in Protégé)
declared = {p for kind in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty)
            for p in g.subjects(RDF.type, kind)}
undeclared = {str(p) for p in g.objects(None, OWL.onProperty) if p not in declared}
if undeclared:
    print(f"✗ restrictions on undeclared properties: {', '.join(sorted(undeclared))}", file=sys.stderr)
    sys.exit(1)
ttl = g.serialize(format="turtle")
Path(OUT).parent.mkdir(exist_ok=True)
Path(OUT).write_text(ttl)
print(f"✓ wrote {OUT} ({len(where)} ids checked)")

# 4. single-file LinkML schema for downstream projects to import. The modules
# are merged into one file; LinkML's built-in types, which the merge copies in,
# are removed again and imported instead, so the file stays DGO-only.
# Downstream imports it without the extension (LinkML appends .yaml).
sv = SchemaView(SCHEMA)
sv.merge_imports()
merged = sv.schema
for name in [t for t, d in merged.types.items() if d.from_schema == LINKML_TYPES]:
    del merged.types[name]
merged.imports = ["linkml:types"]
yaml_dumper.dump(merged, DIST)
check = SchemaView(DIST)
print(f"✓ wrote {DIST} ({len(check.all_classes())} classes, {len(check.all_slots())} slots)")

sys.exit(0)