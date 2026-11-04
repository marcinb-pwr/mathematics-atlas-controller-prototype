# Mathematics Atlas dataset schema 1.3.0

## Identity and files

A declaration’s durable identity is the pair `(snapshot_id, qualified_name)`.
Within `atlas.json.gz`, the declaration node ID is its qualified name. Its proof node
ID is `proof:<qualified_name>`. Concept IDs begin `concept:`. Names are also
stored as typed string/numeric segments in the raw declarations, preserving
Lean’s name structure. This edition’s snapshot ID pins mathlib and the seed set;
`source-lock.json` records the exact Lean build and all Lake package revisions.

The complete ZIP includes:

| File | Contents |
| --- | --- |
| `atlas.json.gz` | Gzip-compressed JSON: searchable nodes, typed directed edges, seed IDs, branches, topics, verification metadata, and all full declaration records under `declarations` |
| `raw/declarations.ndjson` | One exported kernel declaration per line, sorted by qualified name |
| `raw/expressions.ndjson` | Shared exact expression DAG, one array per line; the zero-based line index is the expression ID |
| `raw/export-report.json` | Lean exporter’s verification report |
| `verification.json` | Kernel report plus independent audits, counts, scope limits, and input hashes |
| `source-lock.json` | Exact upstream versions, revisions, and Lean release archive hash |
| `corpus.json` | Selected declarations, module imports, editorial branches, topics, and notation |
| `training-manifest.json` | Graph hash, expression sidecar contract and explicit benchmark-readiness status |

The full dataset ZIP already included `raw/expressions.ndjson` in schema 1.2.
The standalone `atlas.json.gz` has expression **references**, not that table.
Schema 1.3 makes this contract explicit in top-level `expression_table`, with
the direct `expressions.ndjson.gz` download, record count, schema and SHA256
for both compressed and uncompressed bytes. Resolve paths relative to `dist/data`
or the download URL's directory. Inside the full ZIP, use `archive_path` instead.
The separate replay-pilot ZIP is described in `REPLAY.md` in the source download.

Schema 1.1 added `declarations`, keyed by qualified name, to the graph payload.
These records are enriched with pinned source links and available source excerpts,
and are identical to the standalone detail shards. The explorer reads
this bundled map so that selection, proof inspection, and tab changes make no
additional data requests. The inspector downloads a selected declaration directly
from its bundled record. Legacy `details/00.json` … `details/63.json` copies
remain in the source checkout for regression checks. They are omitted from the
public publication and complete ZIP; data clients use the `declarations` map.
The legacy `detailShard` index field is retained but is not a public download URL.
Schema 1.2 adds branch metadata and gzip delivery. The loader also accepts a
response already decompressed by the host. All declarations are available after
one successful initial request. The original raw export and kernel report retain
their 1.0.0 schema; packaging does not change formal declarations or proof terms.

The top-level `branches` list gives each branch’s `id`, `label`, `seed_count`,
and `declaration_count`. Counts include shared dependencies and must not be
summed to obtain the global total. A node’s `branches` lists every selected
branch whose closure reaches it; `branch` is the primary editorial branch for
an entry point and its proof (otherwise null). The same exact qualified name always refers to
the same node and declaration record, regardless of the branch being explored.
Overloaded printed notation alone is not sufficient to identify a declaration.
Concept nodes have their own editorial branch and are not kernel declarations.
`shared_declaration_count` counts formal declarations reached by more than one
branch. The separately represented proof of a shared theorem is also reused.

## Classification and target metadata (1.3)

Enriched declaration records expose the following fields. Index nodes retain
the labels, usage lists, generated/parent flags, candidate/eligibility flags and
split-family ID; full provenance and hashes live in `declarations`. The raw
kernel declaration export remains unchanged.

| Field | Meaning |
| --- | --- |
| `used_in_branches`, `used_in_topics` | Dependency-closure use, derived from the selected results |
| `subject_labels` | What the statement concerns; empty means unassigned, not a negative label |
| `label_provenance` | Separate method, source and review status for subject and usage labels |
| `generated` | `true`: generated-name heuristic; `false`: curated entry point; `null`: unknown |
| `generated_from` | Existing parent declaration inferred from a generated-name prefix, or null |
| `generation_provenance` | Heuristic/editorial origin and review status; no compiler provenance is invented |
| `target_candidate` | Human-facing theorem source screening; not final eligibility |
| `eligible_as_target`, `target_eligibility` | Main-benchmark decision and reason; no target is approved in this release |
| `statement_text_sha256` | Exact printed-statement hash; not semantic equality |
| `split_family_id`, `family_provenance` | Conservative union of repeated statements, same source ranges and inferred generated parents |

Existing curated result and concept-definition mappings seed subject labels with
`editorial_unreviewed` status. `Nat.add_comm` was reviewed as arithmetic/addition.
Legacy `branches` and `topics` fields remain for explorer compatibility; use the
explicit new fields in training. `technical` is still a presentation filter and
now also hides suspected generated proof helpers. The pilot's target is allowed
only in its explicit `engineering_demo` episode; it is not benchmark approved.

Family grouping intentionally errs toward keeping potentially related entries
together. It is not proof of semantic equivalence, and groups may change when
the corpus expands. Frozen experiments must retain their exact family manifest.

Index fields such as `label`, `summary`, `formula`, `topics`, and `technical` are
editorial presentation metadata. Topic propagation means reachable from an entry
point in that topic; it does not assert that a shared foundational lemma belongs
exclusively to that area. Source IDs and formal dependencies are machine-derived.

## Node kinds

| Kind | Meaning |
| --- | --- |
| `theorem` | A proved proposition’s statement; mathematical lemma/corollary roles are not separate logical kinds |
| `proof` | The particular elaborated value proving one theorem declaration |
| `definition`, `opaque` | A constant with a defining value; opacity affects reduction |
| `axiom` | A checked type whose truth or inhabitance is assumed; it has no proof value |
| `inductive` | An inductive datatype or inductive proposition |
| `constructor`, `recursor` | Kernel-generated introduction or elimination/computation machinery |
| `quotient_primitive` | A declaration supplied by Lean’s built-in quotient support |
| `concept` | An editorial mathematical topic associated with definitions and results |

## Edge direction and evidence

| Relation | Stored direction | Evidence |
| --- | --- | --- |
| `type_ref` | Declaration → constant referenced by its type | `type_expression` root |
| `uses` | Proof node → constant referenced by its proof value | `value_expression` root |
| `definition_ref` | Definition/opaque node → referenced constant in its value | `value_expression` root |
| `proves` | Proof node → its theorem statement | Checked value/type association |
| `kernel_requires` | Declaration → additional required structural constant | Exported `kernel_dependencies`, derived from inductive blocks, constructors, recursor rules, and projection tags |
| `maps_to` | Editorial concept → definition | Explicit editorial provenance |
| `about_result` | Editorial concept → selected statement | Explicit editorial provenance |

An edge identifies a direct named reference in an elaborated expression, not
necessarily a human-level lemma invocation or a minimal logical dependence.
Unused apparent source citations and tactic names do not become proof edges.
Proof terms can contain named references that are inessential after reduction.

In the explorer, formal arrows are rendered **from support toward supported
object**. This reverses the display of reference edges; `proves` already has
that direction. Editorial arrows retain stored direction. Tooltips and the
inspector state each relationship. View bounds and filters never invent a
replacement transitive edge. Cyclic structural associations can occur, so the
whole graph is not assumed to be a DAG; the shared expression representation is.

Every non-editorial edge has `evidence.kind = kernel_declaration`, the source
declaration name, its record field, and an expression root where applicable.
`kernel_requires` edges point to metadata-derived dependencies rather than
pretending to be ordinary proof-term references.

## Declaration records

Each raw record includes a name, name segments, module, kind, universe parameter
names, a readable `statement`, readable `value` when applicable, local context,
upstream docstring, source range, direct type/value references, extra structural
dependencies, and the named axioms reported by `Lean.collectAxioms`.

The local context lists the outer binders and conclusion from Lean’s elaborated
type. A binder is classified as a `hypothesis` when its type is a proposition,
otherwise as a `parameter`; its explicit/implicit/instance binder mode is retained.
Nested quantifiers inside the conclusion stay in the type expression.

`type_expression` and `value_expression` reference the exact DAG. Inductive and
recursor metadata preserve original constructor/mutual-type ordering, counts,
and computation-rule roots. Safe declarations alone are admitted into this
export. Source ranges are 1-based lines, 0-based Unicode columns, with an
exclusive end. Generated declarations can share their parent’s range or have no
range. A missing source excerpt never substitutes for an exact expression.

## Exact expressions

| Tag | Array elements after tag |
| --- | --- |
| `bvar` | De Bruijn bound-variable index |
| `sort` | Universe level |
| `const` | Qualified constant name, list of universe levels |
| `app` | Function expression ID, argument expression ID |
| `lambda`, `forall` | Binder name segments, type ID, body ID, binder mode |
| `let` | Name segments, type ID, value ID, body ID, nondependent flag |
| `nat_literal` | Decimal **string**, preserving arbitrary precision |
| `string_literal` | String value |
| `projection` | Structure name, field index, body expression ID |

Universe levels are arrays tagged `zero`, `succ`, `max`, `imax`, or `param`.
Parameters contain typed name segments. No unresolved expression/universe
metavariables or free variables are permitted. Children precede their parent,
allowing streaming validation. Kernel-irrelevant `Expr.mdata` annotations are
omitted. Pretty-printed strings are conveniences; the DAG is authoritative.
Lean’s constant-reference traversal ignores the name tag on a projection; the
export retains that name in the DAG and adds any extra structural dependency.

## Assumptions and verification

`axioms` means the set computed by Lean’s named-axiom collector for the whole
declaration, including its type and proof/definition. It is not the theorem’s
list of local hypotheses. For a proof index node, it is inherited from its
theorem. Concept nodes have no formal axiom claim; their empty list is a
presentation placeholder and assumption filters exclude them.

The independent Python audit follows Lean’s collector: axioms terminate
with themselves; definitions, theorems, and opaque constants traverse type and
value; inductives traverse type and constructors; constructors/recursors traverse
their type; quotient primitives contribute no additional named axioms. A reverse
fixed-point computation propagates these finite axiom sets through the relevant
dependencies, sharing work across branches and handling cycles.

The receipt reports fresh-kernel replay at trust level zero, a negative control
that rejects a mistyped proof, exact reference agreement between expressions
and declaration lists, independent axiom agreement, resolved edges, and absence
of `sorryAx`. It is a reproducible verification record, not a cryptographically
signed certificate or a consistency proof for the foundation.
