#!/usr/bin/env python3
"""Integration gates: real Lean processes, partial replay and terminal failures."""
import json
import os
from pathlib import Path
import signal
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from controller_session import (Session, SessionEnded, candidate_actions, export_observations,
                                read_journal, sha, write_json)
from collect_controller_examples import source_environment


class ControllerSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output = Path(os.environ.get('ATLAS_TEST_OUTPUT', str(ROOT / '.work' / f'session-tests-{time.time_ns()}')))
        cls.output.mkdir(parents=True, exist_ok=False)

    def environment(self, statement='(p : Prop) (h : p) : p ∧ True'):
        return {'target': 'AtlasRegression.demo', 'prefix': 'import Lean\nnamespace AtlasRegression\n',
                'declaration': 'theorem demo ' + statement, 'suffix': '\nend AtlasRegression\n',
                'provenance': {'kind': 'synthetic_regression'}}

    def session(self, suffix='', **options):
        directory = self.output / (self._testMethodName + suffix)
        session = Session(self.environment(), directory, timeout_seconds=60, **options)
        self.addCleanup(lambda: session.stop() if not session.terminal else None)
        return session

    def assert_failed_receipt_absent(self, session, outcome):
        self.assertEqual(session.terminal, outcome)
        self.assertFalse((session.directory / 'verification.json').exists())
        episode = json.loads((session.directory / 'episode.json').read_text())
        self.assertEqual(episode['outcome'], outcome)
        self.assertGreater(episode['wall_elapsed_ns'], 0)

    def test_intermediate_reconstruction_and_new_action(self):
        session = self.session()
        initial = sha(session.state)
        session.step('exact True.intro', expected='accepted')
        self.assertEqual(sha(session.state), initial)
        self.assertFalse(session.history[-1]['response']['expected_matches'])
        session.step('constructor')
        checkpoint = session.snapshot()
        self.assertEqual(len(session.observe()['goals']), 2)
        session.stop()
        resumed = Session.from_checkpoint(checkpoint, self.output / 'resumed', timeout_seconds=60)
        self.addCleanup(lambda: resumed.stop() if not resumed.terminal else None)
        self.assertEqual(sha(resumed.state), checkpoint['snapshot_sha256'])
        resumed.step('assumption')
        resumed.step('trivial')
        self.assertEqual(resumed.stop(), 'proved')
        self.assertTrue((resumed.directory / 'verification.json').exists())

    def test_restore_branches_and_observation_boundary(self):
        session = self.session()
        initial = sha(session.state)
        raw = session.state['goals'][0]['local_context']
        self.assertTrue(any(item['implementation_detail'] for item in raw))
        self.assertNotIn('implementation_detail', json.dumps(session.observe()))
        self.assertNotIn('demo', json.dumps(session.observe()))
        session.step('constructor')
        session.restore(0)
        self.assertEqual(sha(session.state), initial)
        candidates = candidate_actions(session.observe())
        self.assertGreater(len(candidates), 1)
        session.step('constructor', candidates=candidates, generator={'name': 'test', 'version': '1'}, chosen_id='c003')
        rows = read_journal(session.journal)
        event = [r for r in rows if r['kind'] == 'response'][-1]
        self.assertIsNone(event['parent_event_index'])
        self.assertNotEqual(event['branch_id'], 'b0000')
        decision = [r['decision'] for r in rows if r['kind'] == 'request' and r.get('decision')][-1]
        self.assertTrue(all(c['outcome'] is None for c in decision['candidates']))
        feedback = [r for r in rows if r['kind'] == 'action_feedback'][-1]
        self.assertEqual(feedback['tactic_acceptance'], 'accepted')
        self.assertIsNone(feedback['value_target'])
        self.assertNotIn('expected', json.dumps(session.observe()))
        session.stop()
        self.assert_failed_receipt_absent(session, 'abandoned')

    def test_replay_cut_point_in_failed_episode_and_causal_view(self):
        session = self.session()
        session.step('constructor')
        cut_point = sha(session.state)
        session.step('exact True.intro', expected='accepted')
        session.stop()
        view = [json.loads(line) for line in export_observations(session.directory).read_text().splitlines()]
        self.assertEqual(len(view), 2)
        self.assertEqual(view[0]['observation']['history'], [])
        self.assertEqual(len(view[1]['observation']['history']), 1)
        self.assertNotIn('True.intro', json.dumps(view))
        self.assertNotIn('expected', json.dumps(view))
        resumed = Session.from_episode(session.directory, 0, self.output / 'cut-point-resumed')
        self.addCleanup(lambda: resumed.stop() if not resumed.terminal else None)
        self.assertEqual(sha(resumed.state), cut_point)
        self.assertEqual(resumed.attempts, 1)
        resumed.step('assumption')
        resumed.step('trivial')
        self.assertEqual(resumed.stop(), 'proved')

    def test_budget_exhaustion_retains_failed_action(self):
        session = self.session(max_actions=1)
        session.step('exact True.intro')
        with self.assertRaises(SessionEnded):
            session.step('constructor')
        self.assert_failed_receipt_absent(session, 'budget_exhausted')
        self.assertEqual(session.attempts, 1)
        self.assertTrue(any(row['kind'] == 'action_feedback' for row in read_journal(session.journal)))

    def test_timeout_retains_request_and_prefix(self):
        session = self.session(action_timeout_seconds=0.15)
        session.step('constructor')
        with self.assertRaises(SessionEnded):
            session.step('sleep 5000')
        self.assert_failed_receipt_absent(session, 'timeout')
        rows = read_journal(session.journal)
        self.assertEqual(len([row for row in rows if row['kind'] == 'request']), 2)
        self.assertEqual(len([row for row in rows if row['kind'] == 'response']), 1)
        self.assertGreater(json.loads((session.directory / 'episode.json').read_text())['host_phase_ns']['lean_round_trip'], 0)

    def test_process_error_retains_previous_events(self):
        session = self.session()
        session.step('constructor')
        os.killpg(session.proc.pid, signal.SIGKILL)
        with self.assertRaises(SessionEnded):
            session.step('assumption')
        self.assert_failed_receipt_absent(session, 'process_error')
        self.assertTrue(any(row['kind'] == 'action_feedback' for row in read_journal(session.journal)))

    def test_admitted_final_proof_is_rejected(self):
        session = self.session()
        session.step('sorry')
        self.assertEqual(session.observe()['goals'], [])
        session.stop()
        self.assert_failed_receipt_absent(session, 'final_proof_rejected')

    def test_target_guard_and_parameterized_environment(self):
        env = self.environment()
        env['target'] = 'Nat.add_comm'
        destination = self.output / self._testMethodName
        with self.assertRaises(SessionEnded):
            Session(env, destination, timeout_seconds=60)
        self.assertFalse((destination / 'verification.json').exists())
        self.assertIn('target_already_available', (destination / 'lean-events.ndjson').read_text())

    def test_original_pilot_actions_as_regression_fixture(self):
        fixture = ROOT / 'dist/data/replay-pilot'
        plan = json.loads((fixture / 'plan.json').read_text())
        environment = source_environment('Mathlib/Data/Nat/Prime/Defs.lean', plan['target'],
                                         'theorem Prime.dvd_mul ', '\nend Nat\n')
        session = Session(environment, self.output / self._testMethodName, timeout_seconds=120)
        self.addCleanup(lambda: session.stop() if not session.terminal else None)
        trace = json.loads((fixture / 'trace.json').read_text())
        for index, step in enumerate(plan['actions']):
            if session.checkpoint_id != step['from']:
                session.restore(step['from'])
            session.step(step['action'])
            actual = session.history[-1]['response']
            self.assertEqual(actual['tactic_acceptance'], 'accepted' if step['expect'] == 'success' else 'rejected')
            self.assertEqual([g['target'] for g in actual['snapshot']['goals']],
                             [g['target'] for g in trace['events'][index]['after']['goals']])
        self.assertEqual(session.attempts, 14)
        self.assertEqual(len(session._winning_actions()), 9)
        self.assertEqual(session.stop(), 'proved')
        receipt = json.loads((fixture / 'verification.json').read_text())
        for name, digest in receipt['sha256'].items():
            self.assertEqual(sha((fixture / name).read_bytes()), digest)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ControllerSessionTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    write_json(ControllerSessionTests.output / 'test-results.json', {
        'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'lean_reexecuted': True, 'passed': result.wasSuccessful(),
        'test_names': unittest.defaultTestLoader.getTestCaseNames(ControllerSessionTests)})
    raise SystemExit(not result.wasSuccessful())
