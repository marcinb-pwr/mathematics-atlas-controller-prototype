# Mathematics Atlas

A reproducible mathematical knowledge graph and a static explorer. This edition
contains 33 selected results across eight branches, and the full transitive
closure of their elaborated types, values, and required inductive/recursor
structures. Exact counts and verification results are in `data/verification.json`.

The branches are number theory, logic and sets, algebra, linear algebra,
analysis, topology, probability, and combinatorics. Entry points include
Euclid’s theorem, rank–nullity, the intermediate value theorem, preservation of
compactness, probability normalization, and finite-set counting.

Every Lean declaration has one canonical node. Shared definitions, statements,
and proofs are reused across branches; branch labels record which selected
results reach a declaration. Editorial branch membership does not duplicate or
change formal dependencies.

The proof-controller prototype adds live Lean sessions, intermediate-state
replay, incremental failure records and a small Nat/Finset search collection.
See [CONTROLLER.md](CONTROLLER.md) for its interface, tests and scope. Benchmark
readiness remains disabled.

## Explore

Search by a qualified Lean name or a concept. Filter the selected results by
branch, topic, and named axiom usage. Click a graph node to inspect it; double-click
or choose **Explore this node** to focus its neighborhood. Switch among
dependencies, reverse uses, recorded axiom paths, and the editorial concept map.
The **Kernel details** option reveals generated and foundational infrastructure.
It is a presentation filter, not a change to the stored corpus. All direct
references remain accessible in the inspector.

Declaration details are bundled with the graph in one compressed payload and
validated before it opens. The browser uses its native gzip decompressor.
After the initial download, inspecting any node or proof requires no additional
data request. Initial transient download failures are retried up to twice;
access failures prompt a reload instead of repeatedly requesting protected data.

The interface shows one step initially and caps a neighborhood at 70 nodes.
Increase depth, zoom, pan, or focus a neighbor to explore further. Counts disclose
hidden or capped links. Downloads contain the full graph, regardless of the view.

**Statement**, **Proof**, **Assumptions**, and **Source** expose the exact Lean
type, source declaration, readable elaborated term, local hypotheses, named
axioms, source revision, and expression roots. The notation for the 33 entry
points is a manually reviewed paraphrase. Exact Lean statements take precedence.

## Reproduce

The source lock selects Lean 4.19.0 and mathlib commit
`c44e0c8ee63ca166450922a373c7409c5d26b00b`, together with the upstream Lake
package revisions. This intentionally fixed edition does not claim to use the
latest mathlib.

On Linux x86_64, install Python 3.11+, Git, tar with zstd support, and Node.js
with `node:test` support. GCC is needed only for the managed-runtime compatibility
adapter described below. Allow several GB of disk for Lean and compiled mathlib.
From the project directory, run:

```sh
python scripts/reproduce.py
```

`data/corpus.json` is the entry-point, branch, topic, and module manifest.
The script synchronizes the exporter’s seeds and imports from this manifest,
downloads the pinned release, verifies its SHA256 and Git identity,
checks mathlib and package revisions, retrieves the selected compiled module
cache, runs the exporter and kernel replay, independently audits the result,
runs graph behavior checks, and regenerates both download archives.
Repeated runs may differ in timestamps, verification duration, and ZIP metadata.
The corpus and snapshot identities remain pinned.

On other platforms, install the same Lean toolchain and mathlib commit, obtain
the imported modules’ caches listed in `data/corpus.json`, and run `lake env lean` on
`formal/AtlasExport.lean` from the mathlib checkout, setting `ATLAS_OUTPUT_DIR`
to this project’s `.work/export`. The provided acquisition and source-resolution
scripts assume the Linux release’s directory layout and need corresponding path
adjustments. The exporter itself is Lean source.

To serve the already built explorer locally, use an HTTP static server:

```sh
python -m http.server 8000 --directory dist
```

The application uses no framework, remote fonts, analytics, or runtime service.
`python scripts/stage_public.py` prepares the public `out` directory. It includes
all complete downloads and the bundled graph, omitting redundant legacy detail
shards and unpacked episode files already contained in the pilot ZIP. The source
checkout retains those files for local inspection and regression checks.
Its source archive omits the large data files; either run the reproduction
pipeline or unpack the dataset ZIP into `dist/data` before serving it.

## Verification and trust

The exporter traverses actual elaborated constants, rather than interpreting
tactic text. It rejects unsafe/partial declarations and open proof terms, then
replays the closure into a fresh, trust-level-zero kernel environment. It uses
the stock `Kernel.Environment.addDeclCore`; generated constructors and recursors
are compared with the kernel’s regenerated values. A deliberately mistyped proof
must also be rejected. Only then is a success record written.

The replay scheduler adapts Lean’s Apache-licensed `Lean/Replay.lean`. It works
directly with the kernel environment because the elaborator environment’s
private-name normalization can collide across imported modules. This does not
change the kernel or bypass checking. A separate optional `LD_PRELOAD` adapter
aliases the process’s own executable path to `/proc/self/exe` on managed Linux
runtimes that restrict the equivalent numeric `/proc` path. It changes no proof
checking code and grants no access to another process.

The Python audit independently checks expression references, graph closure,
proof availability, and named-axiom sets against Lean’s own collector. A reverse
fixed-point computation shares axiom-audit work across common dependencies and
handles structural cycles.
The receipt and input hashes are shipped with the dataset. The trusted computing
base still includes the Lean kernel, runtime, exporter, and supporting software.
Kernel checks are relative to the accepted axioms; they do not prove consistency.

The named axioms reached in this edition are `propext`, `Classical.choice`, and
`Quot.sound`. Some selected proofs reach none. This describes these recorded
formalizations; it is not a claim that every axiom listed is mathematically
necessary, or that axiom-free entries require no underlying logical rules.

## Proof-controller data pilot

Schema 1.3 separates `used_in_branches` and `used_in_topics` from `subject_labels`,
with label provenance/review status. Generated-name evidence, candidate screening
and conservative split families are explicit. No main-benchmark target is approved
solely from these heuristics. Existing canonical node identities remain unchanged.

The full dataset ZIP already includes `raw/expressions.ndjson`. A graph-only
download contains references to that file. The direct `expressions.ndjson.gz`
download and `training-manifest.json` now expose the table, schema and hashes.

`REPLAY.md` describes an executed proof episode for `Nat.Prime.dvd_mul`: exact
source prefix, actual allowed theorem pool, every goal/local context, failures,
branch restoration and a standalone final proof. Two fresh Lean processes must
agree on all recorded states/outcomes. The final proof is checked without the
recorder, and a deliberately mistyped variant must fail. The source-prefix guard
rejects an environment that already contains the target theorem.

The pilot is hand-authored and belongs to the engineering-demo split. It neither
trains a BDH controller nor measures held-out theorem-proving performance.
`python scripts/collect_pilot.py --verify-existing` verifies the saved pilot
offline once the pinned dependencies are present. Full reproduction also rebuilds
and packages the pilot.

## Scope and next extension

This is complete for the specified dependency closure, not for mathematics as a
whole. Lemma, proposition, corollary, and theorem are mathematical roles; Lean
represents proved propositions uniformly. This edition calls them statements and
stores one existing proof per declaration. Future editions can add more seed
modules, alternate proofs, cross-foundation correspondences, and source-backed
concept curation without conflating those relations with proof dependencies.

See `SCHEMA.md` for exact formats and `NOTICE.md` for attribution. The native
WebMCP interface is optional and feature-detected. Browser/WebMCP validation was
unavailable under this environment’s permitted preview workflow; graph logic,
static assets, exports, and the Lean kernel checks were validated independently.
