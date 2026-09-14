# Replayable Lean proof pilot

The original pilot described here is retained as a regression fixture. For the
new interactive interface, open-goal checkpoints, incremental failure records
and collection commands, see [CONTROLLER.md](CONTROLLER.md). The original
`AtlasReplay.lean` and `collect_pilot.py` remain unchanged.

This is one executed engineering demonstration for `Nat.Prime.dvd_mul`, not a
BDH result, learned search policy, training corpus of sufficient size, or held-out
benchmark. Actions, including deliberate failures, were hand-authored using
knowledge of the existing proof. Candidate lists contain one proposed action;
they are not meaningful premise-ranking labels.

The source prefix stops immediately before the target declaration in the pinned
`Mathlib.Data.Nat.Prime.Defs` file. Lean executes this prefix, preserving its
namespaces, local variables, instances, notation, attributes and earlier commands.
The recorder checks that `Nat.Prime.dvd_mul` is absent from the environment.
It adds an `AtlasReplay` import (which imports `Lean`); this expanded instrumentation
environment is disclosed and pinned. A separate final proof is checked with the
original imports and no instrumentation.

The allowed premise file contains every theorem constant in the resulting
environment, with statement-only fields. It is built from that environment,
independently of the Atlas's selected-result dependency closures. It includes
generated helpers and may include equivalent prior results: it is an environment
inventory, not a reviewed benchmark target set. The target itself is absent.

## Download contents

| File | Role |
| --- | --- |
| `environments.ndjson` | Pinned snapshot, original imports, source position/prefix, instrumentation, options, premise-pool policy and replay command |
| `episodes.ndjson` | Target, engineering-demo split, budgets, outcomes and process costs |
| `states.ndjson` | All goals, structured local contexts, metavariable diagnostics and durable replay references |
| `transitions.ndjson` | Chronological event order, search parent, branch, chosen action, errors, elapsed time and remaining action budget |
| `plan.json` | The actual ordered action plan; `from: 0` selects the initial state, `from: k` selects the state after event `k-1` |
| `trace.json` | Direct recorder output used for fresh-process replay comparison |
| `source-prefix.lean` | Exact upstream text before the target; intentionally ends inside an open namespace |
| `Episode.lean`, `AtlasReplay.lean` | Instrumented executable episode and recorder source |
| `CheckedProof.lean` | Standalone winning proof with the original prefix and imports |
| `allowed-premises.ndjson.gz` | The actual environment's theorem names and types, without proof values, dependencies, labels or source snippets |
| `source-lock.json`, `verification.json` | Required upstream versions, hashes, check scope and results |

States retain diagnostic `Expr` representations, including free/metavariable
identifiers. These strings are not a portable expression serialization. A replay
reference resolves through the exact source prefix, recorder and ordered plan.
Reexecuting through its `stop_after_event` reconstructs the full Lean state,
including metavariable assignments. The successful ancestry list alone is only
an index: the ordered transcript and restores are authoritative.

`parent_event_id` identifies the event that produced the chosen source state.
`previous_event_id` identifies the preceding event in wall-clock collection
order. They differ at backtracking. An error records the thrown Lean message and
the restored after-state. The recorder restores the complete tactic/term state,
including diagnostic information; no failed action's partial assignments survive.

## Reproduce and verify

Use the Atlas **Extraction source** download as the project and retain this
pilot directory under `dist/data/replay-pilot`. On a compatible Linux environment:

```sh
python scripts/reproduce.py
python scripts/collect_pilot.py --verify-existing
```

The reproduction command acquires the pinned toolchain/dependencies if necessary,
rebuilds the graph, collects the pilot, verifies it in a second fresh Lean process,
checks the standalone proof and packages the downloads. Once dependencies are
present, `--verify-existing` works offline and leaves the saved pilot unchanged.
It checks that the recorded state/goal/metavariable snapshots and all outcomes
match fresh execution, ignoring only elapsed-time fields.

The pilot's Lean source elaboration checks the new proof. Imported `.olean` files
are pinned and trusted for this check; the pilot does not claim to recheck the
entire imported environment in a fresh kernel. The graph's separate export receipt
records its existing trust-zero closure replay. A deliberately mistyped replacement
proof must fail, and the final axiom report must contain no `sorryAx`.

Action timings include state-restore and snapshot overhead. Full process timings
also include prefix elaboration and premise export. Failed attempts consume the
recorded action budget. The wall-clock limit is a process guard; it is not a fair
per-theorem controller performance measurement.

## Scaling toward the controller experiment

Freeze a reviewed Nat/Finset target set with conservative family splits and one
explicit environment policy. Automate candidate proposals and collect successful,
failed, timed-out and abandoned attempts. Extend the episode recorder for process
timeouts/crashes and resource accounting before large-scale collection; this
pilot stops without a success receipt if its execution fails or times out.

For a held-out target, construct features **before** embeddings and retrieval:
exclude its proof node/value, proof-derived edges, dependencies, axiom summaries,
source proof, docstrings that expose proofs, and closure-derived labels. Restrict
premises to the actual allowed environment, and apply family exclusions before
training. Merely deleting proof-node edges from a precomputed embedding leaks
information. The raw Atlas and the demonstration trace intentionally store answers
and must not be supplied as a held-out inference input.

Then compare a conventional graph-conditioned controller, a BDH-inspired
controller and its memory-disabled ablation under identical candidate generation
and complete search budgets. This release implements the data/replay milestone;
it does not train or evaluate those controllers.
