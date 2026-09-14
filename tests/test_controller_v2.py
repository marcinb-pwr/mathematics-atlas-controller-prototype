#!/usr/bin/env python3
"""Real Lean regressions for replay budgets, history and inventory reuse."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from controller_session import (Session, SessionEnded, history_fingerprint,
                                read_journal, response_fingerprint, sha, write_json)
from premise_cache import PremiseCache, cache_key
from test_controller_session import ControllerSessionTests


class ControllerV2Tests(ControllerSessionTests):
    def test_candidate_inputs_survive_replay_and_a_second_resume(self):
        session = self.session(action_timeout_seconds=0.5)
        candidates = [{'id': 'c0', 'action': 'constructor', 'premise': None},
                      {'id': 'c1', 'action': 'rfl', 'premise': None}]
        session.step('constructor', candidates=candidates,
                     generator={'name': 'regression-candidates/1'}, chosen_id='c0')
        checkpoint = session.snapshot()
        session.stop()
        resumed = Session.from_checkpoint(checkpoint, self.output / 'candidate-memory-resumed',
                                          max_actions=99, action_timeout_seconds=0.75)
        self.addCleanup(lambda: resumed.stop() if not resumed.terminal else None)
        self.assertEqual(resumed.memory_history(), checkpoint['controller_memory'])
        before = next(row for row in resumed.memory_history() if row['phase'] == 'before_action')
        self.assertNotIn('chosen_id', before)
        self.assertEqual([c['action'] for c in before['candidates']], ['constructor', 'rfl'])
        resumed.step('assumption')
        second_checkpoint = resumed.snapshot()
        resumed.stop()
        again = Session.from_checkpoint(second_checkpoint, self.output / 'candidate-memory-resumed-again')
        self.addCleanup(lambda: again.stop() if not again.terminal else None)
        self.assertEqual(again.action_timeout_seconds, 0.75)
        self.assertEqual(again.memory_history(), second_checkpoint['controller_memory'])
        again.step('trivial')
        self.assertEqual(again.stop(), 'proved')

    def test_action_timeout_checkpoint_and_original_memory(self):
        session = self.session(action_timeout_seconds=0.15)
        session.step('constructor')
        checkpoint = session.snapshot()
        self.assertEqual(checkpoint['action_timeout_seconds'], 0.15)
        session.stop()
        resumed = Session.from_checkpoint(checkpoint, self.output / 'timeout-resumed')
        self.addCleanup(lambda: resumed.stop() if not resumed.terminal else None)
        self.assertEqual(resumed.action_timeout_seconds, 0.15)
        self.assertEqual(resumed.memory_history(), checkpoint['controller_memory'])
        with self.assertRaises(SessionEnded):
            resumed.step('sleep 500')
        self.assert_failed_receipt_absent(resumed, 'timeout')

    def test_episode_timeout_and_explicit_overrides(self):
        session = self.session(action_timeout_seconds=0.15)
        session.step('constructor')
        session.stop()
        resumed = Session.from_episode(session.directory, 0, self.output / 'episode-override',
                                       action_timeout_seconds=0.25, max_actions=99)
        self.addCleanup(lambda: resumed.stop() if not resumed.terminal else None)
        self.assertEqual(resumed.action_timeout_seconds, 0.25)
        start = next(row for row in read_journal(resumed.journal) if row['kind'] == 'start')
        override = start['replay']['explicit_budget_overrides']['action_timeout_seconds']
        self.assertEqual(override, {'recorded': 0.15, 'effective': 0.25, 'changed': True})
        self.assertEqual(resumed.memory_history(), session.memory_history()[:3])
        preserved = Session.from_episode(session.directory, 0, self.output / 'episode-timeout-default')
        self.addCleanup(lambda: preserved.stop() if not preserved.terminal else None)
        self.assertEqual(preserved.action_timeout_seconds, 0.15)

    def test_chronological_divergence_with_equal_final_state_is_rejected(self):
        session = self.session()
        initial = sha(session.state)
        session.step('fail "recorded failure"')
        session.step('fail "second failure"')
        self.assertEqual(sha(session.state), initial)
        checkpoint = session.snapshot()
        session.stop()
        tampered = copy.deepcopy(checkpoint)
        tampered['response_fingerprints'][0] = '0' * 64
        tampered['history_sha256'] = history_fingerprint(tampered['commands'], tampered['response_fingerprints'])
        with self.assertRaisesRegex(ValueError, 'chronological response diverged at event 0'):
            Session.from_checkpoint(tampered, self.output / 'history-divergence')
        self.assertFalse((self.output / 'history-divergence/verification.json').exists())

    def test_changed_controller_feedback_is_rejected(self):
        session = self.session()
        session.step('fail "recorded failure"')
        checkpoint = session.snapshot()
        session.stop()
        tampered = copy.deepcopy(checkpoint)
        feedback = next(row for row in tampered['controller_memory'] if row['phase'] == 'feedback')
        feedback['feedback']['error'] = 'a different failure'
        tampered['controller_memory_sha256'] = sha(tampered['controller_memory'])
        with self.assertRaisesRegex(ValueError, 'observation/feedback history diverged'):
            Session.from_checkpoint(tampered, self.output / 'memory-divergence')

    def test_response_fingerprint_excludes_only_measured_timings(self):
        first = {'kind': 'step', 'tactic_acceptance': 'rejected', 'error': 'same error',
                 'snapshot': {'goals': ['P']}, 'lean_action_ns': 1, 'lean_command_ns': 2,
                 'checkpoint_snapshot_ns': 3}
        changed = {**first, 'lean_action_ns': 100, 'lean_command_ns': 200, 'checkpoint_snapshot_ns': 300}
        self.assertEqual(response_fingerprint(first), response_fingerprint(changed))
        changed['error'] = 'different error'
        self.assertNotEqual(response_fingerprint(first), response_fingerprint(changed))

    def test_inventory_cache_hit_and_corruption_detection(self):
        cache_directory = self.output / 'inventory-cache'
        first = Session(self.environment(), self.output / 'cache-cold', timeout_seconds=180,
                        export_premises=True, inventory_cache=cache_directory)
        self.addCleanup(lambda: first.stop() if not first.terminal else None)
        self.assertEqual(first.inventory_status, 'miss')
        first.stop()
        second = self.session('-warm', export_premises=True, inventory_cache=cache_directory)
        self.assertEqual(second.inventory_status, 'hit')
        self.assertEqual(first.inventory_path.read_bytes(), second.inventory_path.read_bytes())
        ready = next(row['response'] for row in read_journal(second.journal) if row['kind'] == 'ready')
        self.assertIsNone(ready['premise_count'])  # Lean did not export the pool again.
        second.stop()
        payload = next(cache_directory.glob('*.ndjson.gz'))
        payload.write_bytes(b'corrupted')
        with self.assertRaisesRegex(SessionEnded, 'payload hash mismatch'):
            self.session('-corrupt', export_premises=True, inventory_cache=cache_directory)

    def test_old_checkpoint_cannot_silently_supply_default_timeout(self):
        with self.assertRaisesRegex(ValueError, 'Legacy checkpoint'):
            Session.from_checkpoint({'schema': 'atlas-controller-session/1'}, self.output / 'old-checkpoint')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ControllerV2Tests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    write_json(ControllerV2Tests.output / 'test-results.json', {
        'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'lean_reexecuted': True, 'passed': result.wasSuccessful(),
        'test_names': unittest.defaultTestLoader.getTestCaseNames(ControllerV2Tests)})
    raise SystemExit(not result.wasSuccessful())
