#!/usr/bin/env python3
"""Regenerate retrieval/candidates and audit causal records without starting Lean."""
import argparse
import json
from pathlib import Path

from controller_session import (canonical_memory, feedback_memory, model_observation,
                                offered_candidates, read_journal, sha, write_json)
from premise_cache import cache_key
from premise_retrieval import PremiseIndex, candidate_actions


def check_observation(observation):
    assert set(observation) == {'schema', 'goals', 'history', 'state_id', 'branch_id',
                                'remaining_actions', 'remaining_wall_seconds', 'terminal_outcome'}
    for goal in observation['goals']:
        assert set(goal) == {'target', 'hypotheses'}
        assert all(set(local) == {'name', 'type', 'binder_info'} for local in goal['hypotheses'])


def verify(root):
    root = Path(root)
    collection = json.loads((root / 'collection.json').read_text())
    assert collection['schema'] == 'atlas-controller-collection/2'
    assert not collection['benchmark_ready'] and not collection['uses_target_proof']
    assert sha((root / 'collector.py').read_bytes()) == collection['collector_sha256']
    inventory_indexes, summary = {}, []
    records = {entry['directory']: entry for entry in collection['episodes']}
    candidate_sizes, regenerated_states, proposed_sets = [], 0, 0
    for folder in sorted(root.glob('episode-*')):
        environment = json.loads((folder / 'environment.json').read_text())
        lock = json.loads((folder / 'source-lock.json').read_text())
        episode = json.loads((folder / 'episode.json').read_text())
        rows = read_journal(folder / 'events.ndjson')
        start = next(row for row in rows if row['kind'] == 'start')
        ready = next(row['response'] for row in rows if row['kind'] == 'ready')
        assert start['driver_sha256'] == sha((folder / 'controller_session.py').read_bytes())
        assert start['environment_sha256'] == sha(environment) and ready['target_absent']
        assert start['replay'] is None  # These are fresh search episodes.
        budget = start['budget']
        memory_budget = {'max_actions': budget['max_executed_actions'],
                         'action_timeout_seconds': budget['action_wall_seconds']}
        inventory_path = folder / 'allowed-premises.ndjson.gz'
        metadata = json.loads((folder / 'premise-inventory.json').read_text())
        inventory_hash = sha(inventory_path.read_bytes())
        assert metadata['compressed_sha256'] == inventory_hash
        assert metadata['cache_key'] == cache_key(environment, lock, sha((folder / 'AtlasSession.lean').read_bytes()))
        if inventory_hash not in inventory_indexes:
            inventory_indexes[inventory_hash] = PremiseIndex(inventory_path)
        index = inventory_indexes[inventory_hash]
        assert len(index.rows) == metadata['theorem_count']
        assert all(row['kind'] == 'theorem' for row in index.rows)
        assert environment['target'] not in index.by_name
        assert ready['premise_count'] == (None if episode['premise_inventory_cache'] == 'hit' else len(index.rows))
        generator = {**index.metadata(), 'candidate_version': 'apply-exact-rewrite/1', 'top_k': collection['top_k']}

        online = read_journal(folder / 'observations.ndjson')
        derived = read_journal(folder / 'observations-derived.ndjson')
        assert len(online) == len(derived) == episode['executed_actions']
        inputs = {}
        for a, b in zip(online, derived):
            assert a['event_index'] == b['event_index']
            assert canonical_memory(a['observation']) == canonical_memory(b['observation'])
            assert sha(a['observation']) == a['observation_sha256']
            check_observation(a['observation'])
            inputs[a['event_index']] = a
        memory = read_journal(folder / 'controller-memory.ndjson')
        assert memory[0]['phase'] == 'initial' and memory[0]['event_index'] == -1
        assert all(entry['budget'] == memory_budget for entry in memory)
        before_inputs = {entry['event_index']: entry for entry in memory if entry['phase'] == 'before_action'}
        feedback_inputs = {entry['event_index']: entry for entry in memory if entry['phase'] == 'feedback'}
        assert len(before_inputs) == len(inputs)
        history, requests, generated, proposals = [], {}, {}, set()
        producers, checkpoint, branch, attempts = {0: None}, 0, 0, 0
        state = ready['snapshot']
        initial = model_observation(state, [], checkpoint, branch, budget['max_executed_actions'], 0)
        assert canonical_memory(memory[0]['observation']) == canonical_memory(initial)

        def regenerate(observation):
            signature = sha(observation['goals'])
            if signature not in generated:
                retrieved = index.retrieve(observation, collection['top_k'])
                generated[signature] = (retrieved, candidate_actions(observation, retrieved), index.last_query_expansions)
            return generated[signature]

        for row in rows:
            if row['kind'] == 'candidate_proposals':
                observation = model_observation(state, history, checkpoint, branch,
                    budget['max_executed_actions'] - attempts, 0)
                assert canonical_memory(row['observation']) == canonical_memory(observation)
                assert row['generator'] == generator
                retrieved, candidates, expansions = regenerate(row['observation'])
                assert row['retrieved_premises'] == retrieved
                assert row['candidates'] == candidates and row['query_expansions'] == expansions
                assert all(item['source_theorem'] in index.by_name for item in expansions)
                proposals.add(sha({'goals': observation['goals'], 'candidates': candidates}))
                proposed_sets += 1
            elif row['kind'] == 'request':
                event = row['event_index']
                assert event == len(history)
                requests[event] = row
                if row['command']['op'] != 'step':
                    continue
                decision, view = row['decision'], inputs[event]
                assert decision['generator'] == generator
                assert decision['observation_sha256'] == view['observation_sha256']
                retrieved, candidates, _ = regenerate(view['observation'])
                assert sha({'goals': view['observation']['goals'], 'candidates': candidates}) in proposals
                assert decision['candidates'] == offered_candidates(candidates)
                assert all(c['premise'] is None or c['premise'] in index.by_name for c in candidates)
                chosen = next(c for c in candidates if c['id'] == decision['chosen_id'])
                assert chosen['action'] == row['command']['action']
                assert decision['candidate_set_id'] == sha({
                    'environment_sha256': sha(environment), 'generator': generator,
                    'goals': view['observation']['goals'],
                    'candidates': [{'id': c['id'], 'action': c['action']} for c in candidates]})
                assert before_inputs[event] == {
                    'phase': 'before_action', 'event_index': event, 'budget': memory_budget,
                    'observation': view['observation'], 'candidates': offered_candidates(candidates), 'generator': generator}
                candidate_sizes.append(len(candidates))
                attempts += 1
            elif row['kind'] == 'response':
                event, response = row['event_index'], row['response']
                request = requests[event]
                assert response['seq'] == event == len(history)
                assert response['kind'] == request['command']['op']
                assert row['previous_event_index'] == (event - 1 if event else None)
                parent = producers.get(response.get('source_checkpoint', response.get('checkpoint')))
                assert row['parent_event_index'] == parent
                if response['kind'] == 'step':
                    assert response['before'] == state
                    assert response['action'] == request['command']['action']
                    if response['tactic_acceptance'] == 'rejected':
                        assert response['before'] == response['snapshot']
                    producers[response['checkpoint']] = event
                if response['kind'] == 'restore':
                    branch += 1
                assert row['branch_id'] == f'b{branch:04d}'
                history.append({'command': request['command'], 'response': response})
                if 'snapshot' in response:
                    state, checkpoint = response['snapshot'], response['checkpoint']
                observation = model_observation(state, history, checkpoint, branch,
                    budget['max_executed_actions'] - attempts, 0)
                expected = {**feedback_memory(event, response, observation, parent, request['decision']),
                            'budget': memory_budget}
                assert canonical_memory(feedback_inputs[event]) == canonical_memory(expected)
            elif row['kind'] == 'action_feedback':
                response = history[row['event_index']]['response']
                assert row['tactic_acceptance'] == response['tactic_acceptance']
                assert row['goals_before'] == len(response['before']['goals'])
                assert row['goals_after'] == len(response['snapshot']['goals'])
                assert row['snapshot_changed'] == (sha(response['before']) != sha(response['snapshot']))
                assert row['value_target'] is None and row['future_proof_outcome'] is None
        assert len(feedback_inputs) == len(history)
        assert len(memory) == 1 + attempts + len(history)
        assert attempts == episode['executed_actions']
        regenerated_states += len(generated)
        assert (folder / 'verification.json').exists() == (episode['outcome'] == 'proved')
        winning = None
        if episode['outcome'] == 'proved':
            receipt = json.loads((folder / 'verification.json').read_text())
            winning, cursor = [], checkpoint
            while cursor:
                response = history[producers[cursor]]['response']
                if response['tactic_acceptance'] == 'accepted':
                    winning.append(response['action'])
                cursor = response['source_checkpoint']
            winning.reverse()
            assert winning == receipt['winning_actions'] and len(winning) == receipt['winning_action_count']
            proof = environment['prefix'] + '\n' + environment['declaration'] + ' := by\n'
            proof += ''.join('  ' + action.replace('\n', '\n  ') + '\n' for action in winning)
            proof += environment['suffix'] + '\n#print axioms ' + environment['target'] + '\n'
            assert (folder / 'CheckedProof.lean').read_text() == proof
            assert receipt['proof_sha256'] == sha(proof.encode())
            assert receipt['standalone_check_exit'] == 0 and 'sorryAx' not in receipt['axioms_output']
        record = records[folder.name]
        for field in ['outcome', 'executed_actions', 'wall_elapsed_ns', 'initialization_wall_ns', 'search_wall_ns']:
            assert record[field] == episode[field]
        rejected = sum(item['response'].get('tactic_acceptance') == 'rejected' for item in history)
        assert record['rejected_tactics'] == rejected and record['winning_actions'] == winning
        summary.append({'directory': folder.name, 'target': environment['target'], 'outcome': episode['outcome'],
                        'actions': attempts, 'rejected_tactics': rejected, 'allowed_premises': len(index.rows),
                        'winning_action_count': len(winning) if winning is not None else None,
                        'inventory_cache': episode['premise_inventory_cache']})
    assert len(summary) == len(records)
    return {'passed': True, 'lean_reexecuted_by_this_audit': False,
            'checks': ['producer_source_hashes', 'inventory_identity_hash_and_target_absence',
                       'retrieval_and_candidate_regeneration', 'statement_association_provenance',
                       'online_and_causal_observation_agreement', 'original_controller_inputs_and_feedback',
                       'chronological_and_branch_ancestry', 'null_untried_and_value_labels',
                       'failed_tactic_rollback', 'winning_ancestry_and_standalone_proof_receipt'],
            'candidate_sets_regenerated': proposed_sets, 'distinct_episode_states_regenerated': regenerated_states,
            'candidate_size_range': [min(candidate_sizes), max(candidate_sizes)], 'episodes': summary}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    report = verify(args.directory)
    write_json(args.directory / 'structural-audit.json', report)
    print(json.dumps(report))
