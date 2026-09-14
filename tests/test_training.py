"""Integration contracts for packaged data and the executed proof episode."""
import gzip
import hashlib
import json
import unittest
import zipfile
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / 'dist/data'


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


class TrainingPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.atlas = json.loads(gzip.decompress((DATA / 'atlas.json.gz').read_bytes()))

    def test_expression_roots_resolve_to_the_shipped_table(self):
        contract = self.atlas['expression_table']
        compressed = (DATA / contract['path']).read_bytes()
        raw = gzip.decompress(compressed)
        self.assertEqual(hashlib.sha256(compressed).hexdigest(), contract['sha256_gzip'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), contract['sha256_uncompressed'])
        count = raw.count(b'\n')
        self.assertEqual(count, contract['count'])
        with zipfile.ZipFile(DATA / 'mathematics-atlas-dataset.zip') as z:
            self.assertEqual(z.read(contract['archive_path']), raw)
        for d in self.atlas['declarations'].values():
            for key in ('type_expression', 'value_expression'):
                if d[key] is not None:
                    self.assertTrue(0 <= d[key] < count, (d['name'], key))

    def test_subject_labels_and_candidate_flags_have_distinct_meanings(self):
        records = self.atlas['declarations']
        addition = records['Nat.add_comm']
        self.assertEqual(addition['subject_labels'], ['addition', 'arithmetic'])
        self.assertIn('probability', addition['used_in_branches'])
        self.assertNotIn('probability', addition['subject_labels'])
        self.assertFalse(any(d['eligible_as_target'] for d in records.values()))
        nodes = {n['id']: n for n in self.atlas['nodes']}
        self.assertTrue(all(n['kind'] == 'theorem' for n in nodes.values() if n.get('target_candidate')))
        for d in records.values():
            if '_proof_' in d['name']:
                self.assertTrue(d['generated'])
                self.assertFalse(d['target_candidate'])
                self.assertTrue(nodes[d['name']]['technical'])
            self.assertEqual(nodes[d['name']]['used_in_branches'], d['used_in_branches'])

    def test_replay_branches_failures_and_feature_allowlist(self):
        p = DATA / 'replay-pilot'
        episode, = rows(p / 'episodes.ndjson')
        environment, = rows(p / 'environments.ndjson')
        states = {s['id']: s for s in rows(p / 'states.ndjson')}
        transitions = rows(p / 'transitions.ndjson')
        self.assertEqual(episode['split'], 'engineering_demo')
        self.assertTrue(environment['target_absent'])
        self.assertEqual(len(transitions), episode['action_count'])
        self.assertFalse(states[episode['final_state_id']]['goals'])
        self.assertTrue(any(len(s['goals']) > 1 for s in states.values()))
        self.assertTrue(any(t['parent_event_id'] != t['previous_event_id'] for t in transitions))
        for i, t in enumerate(transitions):
            self.assertEqual(t['event_index'], i)
            self.assertEqual(t['remaining_action_budget'], episode['initial_budget']['max_executed_actions'] - i - 1)
            before, after = states[t['before_state_id']], states[t['after_state_id']]
            if t['outcome'] == 'error':
                self.assertTrue(t['error'])
                self.assertEqual(before['goals'], after['goals'])
                self.assertEqual(before['metavariables'], after['metavariables'])
        with gzip.open(p / 'allowed-premises.ndjson.gz', 'rt') as f:
            premises = [json.loads(line) for line in f]
        self.assertEqual(len(premises), environment['allowed_theorem_count'])
        self.assertNotIn(episode['target'], {d['name'] for d in premises})
        for d in premises:
            self.assertEqual(set(d), {'name', 'kind', 'statement'})
        manifest = json.loads((DATA / 'training-manifest.json').read_text())
        self.assertFalse(manifest['main_benchmark_ready'])


if __name__ == '__main__':
    unittest.main()
