# Interactive Lean controller prototype — version 2

This release fixes replay budget preservation, verifies chronological response
and controller-input histories, and connects a common candidate pipeline to the
available theorem premises. It includes **12 executed episodes on six Nat/Finset
targets: 418 tactic attempts, 393 rejected tactics, six proved episodes, five
budget-exhausted episodes and one abandoned episode**. Two distinct targets have
new four-action proofs assembled by search from retrieved lemmas.

`main_benchmark_ready` remains **false**. The targets and retrieval heuristics
were developed as engineering examples; this is not a blind evaluation. Repeated
attempts of the same target are not independent problems. Family review, frozen
splits, broad reviewed classification labels, sanitization of static Atlas graph
features, larger collection and BDH comparisons remain unfinished. No neural
controller is trained here. The original hand-authored `Nat.Prime.dvd_mul` pilot
remains unchanged under `dist/data/replay-pilot`.

## Files and verification scope

| Path | Purpose |
| --- | --- |
| `formal/AtlasSession.lean` | Incremental Lean recorder and saved tactic states; unchanged from version 1 |
| `scripts/controller_session.py` | Interaction, budgets, chronological replay and original controller memory |
| `scripts/premise_cache.py` | Inventories keyed by exact environment, source lock and exporter |
| `scripts/premise_retrieval.py` | Common statement-only retrieval and apply/exact/rewrite proposals |
| `scripts/collect_premise_search.py` | Fixed bounded DFS using that pipeline |
| `scripts/verify_controller_collection.py` | Offline regeneration and structural audit |
| `tests/test_controller_v2.py` | 17 regression tests, including real Lean execution |
| `controller-examples/` | Twelve search episodes, inventories, causal views and audit |
| `controller-tests/` | Integration records, checkpoints, failures and test receipt |
| `dist/data/replay-pilot/` | Original pilot and its original receipt |
| `CONTROLLER_MANIFEST.json`, `SHA256SUMS` | Provenance, limitations and payload hashes |

All 17 tests passed in this release. They cover the original nine integration
cases plus timeout preservation, explicit overrides, response divergence despite
an equal final state, altered feedback, candidate-input preservation through two
resumes, inventory reuse/corruption and rejection of legacy standalone
checkpoints. Some assertions are Python-only; the suite also starts fresh Lean
processes and checks completed proofs. `controller-tests/test-results.json`
records the exact test names.

Every newly proved episode has a standalone Lean check and axiom report. Imported
compiled `.olean` files are pinned and trusted; these runs do not repeat the
entire static corpus's kernel audit. The offline collection verifier does not
start Lean. Its report explicitly distinguishes structural checks from execution.
The static graph/expression dataset is a separate download.

## Set up and reproduce

Use Linux x86_64, Python 3.11+, Git, tar and zstd. The acquisition workflow uses
Lean 4.19.0 and exact mathlib/package revisions from `data/source-lock.json`.
Provisioning needs network access and several gigabytes of disk. GCC is needed
only for the included compatibility adapter in runtimes that require it.

```sh
python3 scripts/acquire_toolchain.py
python3 - <<'PY'
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path('scripts').resolve()))
from reproduce import ROOT, runtime_environment
subprocess.run(
    ['lake', 'exe', 'cache', 'get',
     'Mathlib/Data/Nat/Prime/Defs.lean', 'Mathlib/Data/Finset/Basic.lean'],
    cwd=ROOT / '.work/mathlib4', env=runtime_environment(), check=True,
)
PY
ATLAS_TEST_OUTPUT=.work/new-controller-tests python3 tests/test_controller_v2.py
python3 scripts/collect_premise_search.py \
  --output .work/new-premise-search --budgets 16 256
python3 scripts/verify_controller_collection.py .work/new-premise-search
```

Use fresh output directories. Cached inventories persist under
`.work/premise-cache`; subsequent attempts reuse them. Without an existing cache,
each new source-prefix environment exports its inventory once. Network/toolchain
provisioning is outside episode timing. Recorder compilation is guarded by a
process lock; generated runtime adapters and compiled recorder files are published
atomically so concurrent sessions cannot read partially written binaries.

For the shipped bundle, verify hashes and regenerate all candidate lists without
installing Lean:

```sh
sha256sum -c SHA256SUMS
python3 scripts/verify_controller_collection.py controller-examples
```

The verifier checks inventory identities/hashes and target absence; reproduces
retrieved premises, query expansions and candidates; compares online and causal
observations; checks chronological memory, ancestry and failed-tactic rollback;
and reconstructs the winning action sequence and standalone proof file. The
verification script writes `structural-audit.json`; regeneration does not change
the journal or proof files.

## Interact and pause with open goals

```sh
python3 scripts/controller_session.py \
  --environment data/controller-smoke.json --output .work/interactive-demo \
  --action-timeout 0.5
```

Send one JSON command per line after the initial response:

```json
{"op":"step","action":"constructor"}
{"op":"snapshot"}
{"op":"step","action":"assumption"}
{"op":"step","action":"trivial"}
{"op":"stop"}
```

The snapshot is saved as `checkpoint-1.json` with open goals. Reconstruct it in
a fresh process and accept new actions:

```sh
python3 scripts/controller_session.py \
  --checkpoint .work/interactive-demo/checkpoint-1.json --output .work/resumed-demo
```

`restore` accepts a current-session checkpoint such as
`{"op":"restore","checkpoint":"s0"}`. `stop` with `reason: "abandoned"`
preserves an open episode without admitting its goals. Python exposes `Session`,
`step`, `observe`, `snapshot`, `restore`, `stop`, `from_checkpoint` and
`from_episode`. Calls on one session are sequential. Use separate directories for
independent sessions and stop active sessions in a `finally` block.

## Replay budgets and chronological memory

Version 2 checkpoints store the per-action timeout explicitly. `from_episode`
reads `budget.action_wall_seconds` from the original start record. Both replay
paths preserve that timeout by default: **0.15 seconds remains 0.15 seconds**.
The action-count limit and remaining episode wall budget are also restored.
Replay/setup costs consume the remaining wall budget.

Explicit Python or CLI overrides are recorded under
`start.replay.explicit_budget_overrides`, including the recorded value, effective
value and whether it changed. Historical actions are replayed with their recorded
per-action timeouts; an explicit action-timeout override applies to continuation.
An action-count override cannot be smaller than the replayed action count.

A checkpoint includes:

- The environment, source lock, recorder hash and initial raw snapshot hash.
- Commands through `stop_after_event`, one response fingerprint per event, an
  aggregate history fingerprint and the final snapshot/checkpoint identity.
- Original controller inputs and feedback, their exact fingerprint and budgets.

Fresh-process reconstruction verifies the initial state, **every chronological
response**, final state and normalized controller-memory sequence. Response
fingerprinting excludes only `lean_action_ns`, `checkpoint_snapshot_ns` and
`lean_command_ns`. Errors, acceptance, actions, checkpoints and raw snapshots
remain covered. Controller-memory comparison ignores measured remaining wall time
and accounts for explicitly changed action-count limits. Original recorded input
values, including original remaining-time values, are then restored exactly.

`controller-memory.ndjson` and `memory_history()` preserve the original sequence:
initial observation; pre-action observation with offered candidates and generator;
and post-operation feedback with chosen action/error and ancestry. The chosen ID
is absent from the pre-action entry. The separate `replay-generated-memory.ndjson`
and `replay-generated-observations.ndjson` retain actual reconstruction inputs and
costs for inspection. A stateful controller can rebuild its memory by processing
the original sequence; no BDH hidden-state implementation is included.

For an intermediate event in a failed episode:

```python
from controller_session import Session

session = Session.from_episode(
    'controller-examples/episode-1-budget-16',
    stop_after_event=0,
    directory='.work/replayed-event',
)
try:
    original_history = session.memory_history()
    print(session.observe())
finally:
    session.stop()
```

Set `PYTHONPATH=scripts` when using the Python API. Event indices are zero-based
protocol operations, including snapshots and restores. `-1` selects the initial
state. An interrupted request without a complete response has no reconstructible
after-state. Version 1 standalone checkpoints lack enough information and are
rejected; the original version 1 episode journal can instead supply its timeout,
responses and exact pre-action observations. Its feedback is reconstructed
causally and explicitly marked as such.

## Environments, inventories and the common action pipeline

Each environment records `target`, `prefix`, `declaration`, `suffix` and
provenance. The driver checks the exact source-file hash and byte ranges against
the pinned checkout. The prefix ends before the target's assignment; it contains
no target proof body. Namespace/section endings are explicit. Lean guards that
the target is absent. The source range and scope endings still require selection
and execution checks; there is no general eligibility reviewer/source parser.

Instrumentation adds `AtlasSession`, importing `Lean`. The inventory contains all
theorems in that instrumented source-prefix environment, with only name, kind and
statement. It is independent of the target's successful proof and Atlas's selected
result dependency closures. The standalone winning proof uses the original prefix.

Cache identity includes the complete environment, pinned source lock, recorder
hash, exporter version and fixed Lean options. Every hit verifies the compressed
payload hash, schema, unique names, count and target absence. Each episode gets an
inventory copy and `premise-inventory.json`; a warm `ready` response has
`premise_count: null` because Lean did not export again. The count is then read
from the checked inventory metadata. Four pools in this collection were seeded
from the previous release; two were freshly exported here. Original export costs
remain in metadata and are not charged again on cache hits.

`CandidatePipeline` is the same interface for any future compared controller:

1. Build/reuse a BM25 index of allowed theorem names and statements.
2. Query with the current goal and accessible local types.
3. Expand queries with a small statement-association graph extracted from printed
   predicate equivalences, retaining each source theorem. These are retrieval
   hints, not unconditional equivalence assertions. A general equality heuristic
   also proposes available type extensionality lemmas.
4. Offer basic/local tactics and `apply`, `exact`, forward/reverse `rw`, and
   applicable equivalence directions for the retrieved lemmas.

The inventory hash, generator source hash/version, ranking tie policy, retrieved
premises, association provenance and all offered actions are recorded. The
pipeline never reads the stored target proof or proof dependency edges. It gives
an operational role to a **statement-derived association graph**; integration of
sanitized static Atlas graph embeddings remains pending.

The collector uses fixed depth-limited DFS with duplicate visible-state detection.
A candidate's untried outcome remains `null`. `tactic_acceptance` is separate from
proof completion; per-action value labels remain unassigned. The same offered
candidate set can recur after backtracking, with its identity scoped by environment.

## Collection and costs

| Target | Budget 16: outcome / attempts | Budget 256: outcome / attempts | Winning proof length |
| --- | --- | --- | ---: |
| `Nat.irreducible_iff_nat_prime` | Proved / 2 | Proved / 2 | 1 |
| `Nat.Prime.ne_zero` | Budget exhausted / 16 | Proved / 21 | 4 |
| `Finset.disjUnion_eq_union` | Budget exhausted / 16 | Proved / 31 | 4 |
| `Finset.erase_empty` | Proved / 2 | Proved / 2 | 1 |
| `Nat.Prime.pos` | Budget exhausted / 16 | Abandoned / 38 | — |
| `Finset.disjoint_union_left` | Budget exhausted / 16 | Budget exhausted / 256 | — |

The two multistep proofs are:

```lean
-- Nat.Prime.ne_zero
intro h_atlas_0
apply Nat.prime_zero_false
rw [← h_atlas_0]
assumption

-- Finset.disjUnion_eq_union
apply Finset.ext
intro h_atlas_0
rw [Finset.mem_disjUnion]
rw [Finset.mem_union]
```

Full source contexts and check receipts accompany each proved episode.

Costs include unsuccessful runs. `initialization_wall_ns` covers runtime setup,
inventory lookup/export/copy and the initial Lean state. `search_wall_ns` starts
there and runs through the stop response or failure; it includes retrieval index
setup and search but excludes standalone final checking. Host phases separately
record index construction, retrieval, candidate generation, fixed-controller
selection, Lean round trips, serialization and final checking. A reused in-memory
retrieval index is also recorded. No neural inference is measured.

For `Finset.disjoint_union_left`, the first attempt spent **125.878 seconds** in
initialization, including **123.749 seconds** exporting premises. The next
attempt reused that inventory and initialized in **1.682 seconds**. Their search
budgets differ; these are setup measurements, not a controlled proof-search
speedup claim. All total and component costs remain in the records. Nested Lean
timers must not be added again to the host phases containing them.

## Incremental failures and observation boundary

Requests are flushed and fsynced before execution; complete responses and terminal
records are retained. The Lean journal also flushes every event. Failed tactics
restore their saved tactic/term state. Terminal outcomes remain `proved`,
`budget_exhausted`, `abandoned`, `timeout`, `process_error` and
`final_proof_rejected`. Only `proved` receives `verification.json`; `sorryAx` is
rejected. A killed host may leave an unterminated complete journal prefix, which
is an interrupted attempt and never a successful receipt.

`previous_event_index` records time order; `parent_event_index` records search
ancestry. Opaque branch IDs change on restoration. `observe()` allows current
goals, non-implementation local names/types, past actions/errors, opaque IDs and
budgets. It excludes elaborator internals, future plans, expected outcomes and
target proof text. `observations.ndjson` records online decision inputs;
`observations-derived.ndjson` is rebuilt from causal event prefixes. Raw debugging
snapshots and completed proof files remain separate. On resumed episodes the
original memory ledger is authoritative for reconstructing the controller's past
inputs, while replay-generated records describe the new execution.
