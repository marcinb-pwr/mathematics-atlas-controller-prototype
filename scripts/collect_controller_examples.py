#!/usr/bin/env python3
"""Small, proof-blind collection exercise; no benchmark or learned controller."""
import argparse
import gzip
import json
from pathlib import Path

from controller_session import (ROOT, Session, SessionEnded, candidate_actions,
                                export_observations, read_journal, sha, write_json)

GENERATOR = {'name': 'visible-context-basic-tactics', 'version': '1.0.0',
             'uses_target_proof': False, 'uses_graph_dependency_closure': False}
COLLECTOR_SOURCE_BYTES = Path(__file__).read_bytes()

# These are explicit environment examples, not benchmark-approved targets.
# Source markers are used only to build byte ranges; the session driver consumes
# exact ranges and is independent of theorem names and namespace conventions.
TARGETS = [
    ('Mathlib/Data/Nat/Prime/Defs.lean', 'Nat.irreducible_iff_nat_prime',
     'theorem irreducible_iff_nat_prime ', '\nend Nat\n'),
    ('Mathlib/Data/Nat/Prime/Defs.lean', 'Nat.Prime.ne_zero',
     'theorem Prime.ne_zero ', '\nend Nat\n'),
    ('Mathlib/Data/Finset/Basic.lean', 'Finset.disjUnion_eq_union',
     'theorem disjUnion_eq_union ', '\nend Lattice\nend Finset\n'),
    ('Mathlib/Data/Finset/Basic.lean', 'Finset.erase_empty',
     'theorem erase_empty ', '\nend Erase\nend Finset\n'),
]


def source_environment(source_file, target, marker, suffix):
    source = (ROOT / '.work/mathlib4' / source_file).read_bytes()
    marker = marker.encode()
    if source.count(marker) != 1:
        raise ValueError('Ambiguous declaration start; provide reviewed byte offsets')
    start = source.index(marker)
    end = source.index(b':=', start)
    return {'schema': 'atlas-source-environment/1', 'target': target,
            'prefix': source[:start].decode(), 'declaration': source[start:end].decode().rstrip(),
            'suffix': suffix, 'provenance': {
                'kind': 'mathlib_source_prefix', 'source_file': source_file,
                'source_sha256': sha(source), 'declaration_start_byte': start,
                'assignment_start_byte': end,
                'context_review': 'explicit_suffix_and_source_range_checked_in_integration_tests',
                'family_review': 'pending', 'benchmark_approved': False}}


def explore(session, depth=0, maximum_depth=2):
    if not session.observe()['goals']:
        return True
    base = session.checkpoint_id
    with session.measure('candidate_generation'):
        candidates = candidate_actions(session.observe())
    for candidate in candidates:
        if session.attempts >= session.max_actions:
            session.stop('budget_exhausted')
            return False
        if session.checkpoint_id != base:
            session.restore(base)
        with session.measure('controller_inference'):
            chosen = candidate  # Fixed, deterministic DFS ordering; no learned model.
        session.step(chosen['action'], candidates=candidates, chosen_id=chosen['id'], generator=GENERATOR)
        response = session.history[-1]['response']
        if response['tactic_acceptance'] != 'accepted':
            continue
        if not session.observe()['goals']:
            return True
        if depth < maximum_depth and sha(response['before']) != sha(response['snapshot']):
            if explore(session, depth + 1, maximum_depth):
                return True
            if session.terminal:
                return False
    session.restore(base)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-actions', type=int, default=20)
    parser.add_argument('--export-premises', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'collector.py').write_bytes(COLLECTOR_SOURCE_BYTES)
    environments = args.output / 'environments'
    environments.mkdir()
    report = []
    for index, target in enumerate(TARGETS):
        environment = source_environment(*target)
        write_json(environments / f'{index}.json', environment)
        destination = args.output / f'episode-{index}'
        print('Collecting', environment['target'], flush=True)
        session = None
        try:
            session = Session(environment, destination, max_actions=args.max_actions,
                              timeout_seconds=300, export_premises=args.export_premises)
            explore(session)
            if not session.terminal:
                session.stop()
        except SessionEnded:
            pass
        except BaseException as error:
            if session is not None:
                session._fail('process_error', str(error))
            raise
        premise_path = destination / 'allowed-premises.ndjson'
        if premise_path.exists():
            with premise_path.open('rb') as source, gzip.GzipFile(str(premise_path) + '.gz', 'wb', mtime=0) as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    output.write(chunk)
            premise_path.unlink()
        episode = json.loads((destination / 'episode.json').read_text())
        export_observations(destination)
        rows = read_journal(destination / 'events.ndjson')
        feedback = [row for row in rows if row['kind'] == 'action_feedback']
        report.append({'target': environment['target'], 'outcome': episode['outcome'],
                       'executed_actions': episode['executed_actions'],
                       'rejected_tactics': sum(row['tactic_acceptance'] == 'rejected' for row in feedback),
                       'wall_elapsed_ns': episode['wall_elapsed_ns']})
        print(report[-1], flush=True)
    write_json(args.output / 'collection.json', {
        'schema': 'atlas-controller-collection/1', 'generator': GENERATOR,
        'collector_sha256': sha(COLLECTOR_SOURCE_BYTES),
        'controller': 'deterministic depth-limited DFS', 'maximum_depth': 2,
        'selection_policy': 'Four explicit source-prefix engineering cases; not representative sampling.',
        'proof_bodies_consulted_by_generator_or_controller': False,
        'benchmark_ready': False, 'split': 'engineering_demo', 'episodes': report})


if __name__ == '__main__':
    main()
