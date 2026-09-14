#!/usr/bin/env python3
"""Bounded common-pipeline search; engineering collection, not a benchmark."""
import argparse
import json
from pathlib import Path
import time

from controller_session import ROOT, Session, SessionEnded, export_observations, read_journal, sha, write_json
from collect_controller_examples import TARGETS, source_environment
from premise_cache import seed_from_episode
from premise_retrieval import CandidatePipeline

SOURCE_BYTES = Path(__file__).read_bytes()
SEARCH_TARGETS = TARGETS + [
    ('Mathlib/Data/Nat/Prime/Defs.lean', 'Nat.Prime.pos', 'theorem Prime.pos ', '\nend Nat\n'),
    ('Mathlib/Data/Finset/Basic.lean', 'Finset.disjoint_union_left', 'theorem disjoint_union_left ', '\nend Lattice\nend Finset\n'),
]


def explore(session, pipeline, depth=0, maximum_depth=6, visited=None):
    if not session.observe()['goals']:
        return True
    if visited is None:
        visited = set()
    signature = sha(session.observe()['goals'])
    if signature in visited:
        return False
    visited.add(signature)
    base = session.checkpoint_id
    candidates, metadata = pipeline.propose(session)
    for candidate in candidates:
        if session.attempts >= session.max_actions:
            session.stop('budget_exhausted')
            return False
        if session.checkpoint_id != base:
            session.restore(base)
        with session.measure('controller_inference'):
            chosen = candidate
        session.step(chosen['action'], candidates=candidates, generator=metadata, chosen_id=chosen['id'])
        response = session.history[-1]['response']
        if response['tactic_acceptance'] != 'accepted':
            continue
        if not session.observe()['goals']:
            return True
        if depth < maximum_depth and sha(session.observe()['goals']) != signature:
            if explore(session, pipeline, depth + 1, maximum_depth, visited):
                return True
            if session.terminal:
                return False
    if session.checkpoint_id != base:
        session.restore(base)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budgets', type=int, nargs='+', default=[16, 256])
    parser.add_argument('--targets', type=int, nargs='+', default=list(range(len(SEARCH_TARGETS))))
    parser.add_argument('--top-k', type=int, default=12)
    parser.add_argument('--seed-collection', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'collector.py').write_bytes(SOURCE_BYTES)
    cache_directory = ROOT / '.work/premise-cache'
    seeds = []
    if args.seed_collection:
        for source in sorted(args.seed_collection.glob('episode-*')):
            seeds.append({'source_environment_sha256': sha(json.loads((source / 'environment.json').read_text())),
                          'cache_key': seed_from_episode(source, cache_directory)})
    write_json(args.output / 'cache-seeds.json', seeds)
    report = []
    for target_index in args.targets:
        environment = source_environment(*SEARCH_TARGETS[target_index])
        pipeline = None
        for budget in args.budgets:
            name = f'episode-{target_index}-budget-{budget}'
            destination = args.output / name
            print('Collecting', environment['target'], 'budget', budget, flush=True)
            session = None
            try:
                session = Session(environment, destination, max_actions=budget, timeout_seconds=360,
                                  action_timeout_seconds=2, export_premises=True, inventory_cache=cache_directory)
                setup = time.monotonic_ns()
                if pipeline is None:
                    pipeline = CandidatePipeline(session.inventory_path, args.top_k)
                else:
                    assert sha(session.inventory_path.read_bytes()) == pipeline.index.inventory_sha256
                session._phase('retrieval_index_setup', time.monotonic_ns() - setup)
                explore(session, pipeline)
                if not session.terminal:
                    session.stop()
            except SessionEnded:
                pass
            except BaseException as error:
                if session is not None:
                    session._fail('process_error', str(error))
                raise
            export_observations(destination)
            episode = json.loads((destination / 'episode.json').read_text())
            rows = read_journal(destination / 'events.ndjson')
            feedback = [row for row in rows if row['kind'] == 'action_feedback']
            receipt_path = destination / 'verification.json'
            receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
            entry = {'directory': name, 'target': environment['target'], 'budget': budget,
                     'outcome': episode['outcome'], 'executed_actions': episode['executed_actions'],
                     'rejected_tactics': sum(row['tactic_acceptance'] == 'rejected' for row in feedback),
                     'winning_actions': receipt.get('winning_actions'),
                     'wall_elapsed_ns': episode['wall_elapsed_ns'],
                     'initialization_wall_ns': episode['initialization_wall_ns'],
                     'search_wall_ns': episode['search_wall_ns'],
                     'inventory_cache': episode['premise_inventory_cache']}
            report.append(entry)
            print(entry, flush=True)
            write_json(args.output / 'collection.json', {
                'schema': 'atlas-controller-collection/2', 'collector_sha256': sha(SOURCE_BYTES),
                'controller': 'deterministic depth-limited DFS with visible-state duplicate detection',
                'maximum_depth': 6, 'candidate_pipeline': 'available-premises-bm25/1 + apply-exact-rewrite/1',
                'top_k': args.top_k, 'split': 'engineering_demo', 'benchmark_ready': False,
                'selection_policy': 'Six explicit Nat/Finset source-prefix engineering targets; no frozen benchmark splits.',
                'uses_target_proof': False, 'proof_dependency_graph_used': False,
                'statement_association_graph_used': True, 'episodes': report})


if __name__ == '__main__':
    main()
