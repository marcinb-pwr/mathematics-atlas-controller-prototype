#!/usr/bin/env python3
"""Incremental Lean 4.19.0 interaction. No target proof is read by this driver.

The raw journal is for debugging/replay. observe() is the model-facing boundary.
Only a separately checked final proof receives a successful receipt.
"""
from __future__ import annotations

import argparse
import fcntl
from contextlib import contextmanager, redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import select
import signal
import subprocess
import sys
import threading
import time
import tempfile

from reproduce import ROOT, runtime_environment
from premise_cache import PremiseCache, cache_key

SCHEMA = 'atlas-controller-session/2'
DRIVER_SOURCE_BYTES = Path(__file__).read_bytes()
TERMINAL = {'proved', 'budget_exhausted', 'abandoned', 'timeout',
            'process_error', 'final_proof_rejected'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value):
    if not isinstance(value, bytes):
        value = encoded(value).encode()
    return hashlib.sha256(value).hexdigest()


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


def read_journal(path):
    """Retain every complete line after interruption; reject internal corruption."""
    lines = Path(path).read_bytes().splitlines(keepends=True)
    return [json.loads(line) for line in lines if line.endswith(b'\n')]


RESPONSE_TIME_FIELDS = {'lean_action_ns', 'checkpoint_snapshot_ns', 'lean_command_ns'}


def response_fingerprint(response):
    return sha({key: value for key, value in response.items() if key not in RESPONSE_TIME_FIELDS})


def history_fingerprint(commands, responses):
    return sha({'commands': commands, 'response_fingerprints': responses})


def canonical_memory(value, budget_delta=0):
    """Exclude only measured wall time; adjust a declared replay budget override."""
    if isinstance(value, list):
        return [canonical_memory(item, budget_delta) for item in value]
    if isinstance(value, dict):
        return {key: (item + budget_delta if key == 'remaining_actions' else canonical_memory(item, budget_delta))
                for key, item in value.items() if key != 'remaining_wall_seconds'}
    return value


def offered_candidates(candidates):
    if candidates is None:
        return None
    return [{'id': row['id'], 'action': row['action'], 'premise': row.get('premise'), 'outcome': None}
            for row in candidates]


def feedback_memory(index, response, observation, parent, decision=None):
    feedback = {'operation': response['kind'], 'parent_event_index': parent,
                'previous_event_index': index - 1 if index else None}
    if response['kind'] == 'step':
        feedback.update(action=response['action'], tactic_acceptance=response['tactic_acceptance'], error=response['error'])
        feedback['chosen_id'] = decision['chosen_id'] if decision else None
    return {'phase': 'feedback', 'event_index': index, 'feedback': feedback, 'observation': observation}


def model_observation(state, history, checkpoint_id, branch, remaining_actions,
                      remaining_wall_seconds, terminal=None):
    """Explicit allowlist shared by online interaction and causal export."""
    goals = []
    for goal in (state or {}).get('goals', []):
        hypotheses = [{'name': local['name'], 'type': local['type'], 'binder_info': local['binder_info']}
                      for local in goal['local_context'] if not local['implementation_detail']]
        goals.append({'target': goal['target'], 'hypotheses': hypotheses})
    visible_history = []
    for index, item in enumerate(history):
        response = item['response']
        entry = {'event_index': index, 'operation': item['command']['op']}
        if response['kind'] == 'step':
            entry.update(action=response['action'], tactic_acceptance=response['tactic_acceptance'],
                         error=response['error'])
        if response['kind'] == 'restore':
            entry['restored_checkpoint'] = f"s{response['checkpoint']}"
        visible_history.append(entry)
    return {'schema': 'atlas-observation/1', 'goals': goals, 'history': visible_history,
            'state_id': f's{checkpoint_id}', 'branch_id': f'b{branch:04d}',
            'remaining_actions': remaining_actions,
            'remaining_wall_seconds': max(0, remaining_wall_seconds), 'terminal_outcome': terminal}


def export_observations(recorded_directory):
    """Build an input-only view of an existing trace using its chronological prefix.

    Outcomes/targets for supervised learning must be joined separately. This
    function does not read the target proof, candidate choices or later rows
    when constructing a decision's observation.
    """
    recorded_directory = Path(recorded_directory)
    rows = read_journal(recorded_directory / 'events.ndjson')
    state, history, requests = None, [], {}
    checkpoint, branch, attempts = 0, 0, 0
    views = []
    for row in rows:
        if row['kind'] == 'start':
            budget = row['budget']
        elif row['kind'] == 'ready':
            state = row['response']['snapshot']
        elif row['kind'] == 'request':
            requests[row['event_index']] = row['command']
            if row['command']['op'] == 'step':
                observation = model_observation(state, history, checkpoint, branch,
                    budget['max_executed_actions'] - attempts,
                    budget['episode_wall_seconds'] - row['host_elapsed_ns'] / 1e9)
                views.append({'event_index': row['event_index'], 'observation': observation,
                              'observation_sha256': sha(observation), 'origin': 'causal_event_prefix'})
                attempts += 1
        elif row['kind'] == 'response':
            response = row['response']
            history.append({'command': requests[row['event_index']], 'response': response})
            if 'snapshot' in response:
                state, checkpoint = response['snapshot'], response['checkpoint']
            if response['kind'] == 'restore':
                branch += 1
    path = recorded_directory / 'observations-derived.ndjson'
    with path.open('w') as handle:
        for view in views:
            handle.write(encoded(view) + '\n')
    return path


class SessionEnded(RuntimeError):
    pass


def prepare_runtime(environment):
    with redirect_stdout(sys.stderr):
        env = runtime_environment()
    lock = json.loads((ROOT / 'data/source-lock.json').read_text())
    actual = subprocess.check_output(['lean', '--githash'], env=env, text=True).strip()
    if actual != lock['lean_commit']:
        raise RuntimeError('Lean binary differs from the pinned source lock')
    cwd = ROOT
    if environment['provenance']['kind'] == 'mathlib_source_prefix':
        cwd = ROOT / '.work/mathlib4'
        checkouts = [(cwd, lock['mathlib_commit'])] + [
            (cwd / '.lake/packages' / p['name'], p['rev']) for p in lock['packages']]
        for checkout, revision in checkouts:
            if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout,
                                       text=True).strip() != revision:
                raise RuntimeError(f'Wrong source revision: {checkout.name}')
            subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--'], cwd=checkout,
                           capture_output=True, check=True)
        env['LEAN_PATH'] = subprocess.check_output(
            ['lake', 'env', 'printenv', 'LEAN_PATH'], cwd=cwd, env=env, text=True).strip()
        provenance = environment['provenance']
        source = (cwd / provenance['source_file']).read_bytes()
        if sha(source) != provenance['source_sha256']:
            raise RuntimeError('Source file differs from the reviewed environment')
        start, end = provenance['declaration_start_byte'], provenance['assignment_start_byte']
        if source[:start].decode() != environment['prefix'] or source[start:end].decode().rstrip() != environment['declaration']:
            raise RuntimeError('Environment does not match its exact source range')
        if source[end:end + 2] != b':=':
            raise RuntimeError('Declaration range does not end at its assignment')
    elif environment['provenance']['kind'] != 'synthetic_regression':
        raise ValueError('Unsupported environment provenance')
    build = ROOT / '.work/controller'
    build.mkdir(parents=True, exist_ok=True)
    recorder = ROOT / 'formal/AtlasSession.lean'
    fingerprint = sha(recorder.read_bytes())
    stamp = build / 'recorder.sha256'
    with (build / 'compile.lock').open('a') as compile_lock:
        fcntl.flock(compile_lock, fcntl.LOCK_EX)
        if not (build / 'AtlasSession.olean').exists() or not stamp.exists() or stamp.read_text() != fingerprint:
            with tempfile.TemporaryDirectory(dir=build) as temporary:
                compiled = Path(temporary) / 'AtlasSession.olean'
                subprocess.run(['lean', '-R', str(ROOT / 'formal'), '-o', str(compiled),
                                str(recorder)], cwd=cwd, env=env, check=True, capture_output=True)
                compiled.replace(build / 'AtlasSession.olean')
                stamp.write_text(fingerprint)
    env['LEAN_PATH'] = str(build) + os.pathsep + env.get('LEAN_PATH', '')
    return env, cwd, fingerprint, lock


class Session:
    def __init__(self, environment, directory, *, max_actions=64, timeout_seconds=240,
                 action_timeout_seconds=30, export_premises=False, replay_context=None,
                 inventory_cache=None):
        if max_actions < 0 or timeout_seconds <= 0 or action_timeout_seconds <= 0:
            raise ValueError('Invalid episode budget')
        self.environment = json.loads(encoded(environment))
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False)
        self.started_ns = time.monotonic_ns()
        self.deadline = time.monotonic() + timeout_seconds
        self.max_actions = max_actions
        self.action_timeout_seconds = action_timeout_seconds
        self.attempts = 0
        self.history = []
        self.controller_memory = []
        self.state = None
        self.checkpoint_id = 0
        self.branch = 0
        self.producers = {0: None}
        self.terminal = None
        self.proc = None
        self.lock = threading.RLock()
        self.phases = {}
        self.journal = self.directory / 'events.ndjson'
        self.timer = threading.Timer(timeout_seconds, lambda: self._fail('timeout', 'episode_wall_clock_limit'))
        self.timer.daemon = True
        write_json(self.directory / 'environment.json', environment)
        (self.directory / 'controller_session.py').write_bytes(DRIVER_SOURCE_BYTES)
        self._record({'kind': 'start', 'schema': SCHEMA, 'environment_sha256': sha(environment),
                      'driver_sha256': sha(DRIVER_SOURCE_BYTES),
                      'budget': {'max_executed_actions': max_actions, 'episode_wall_seconds': timeout_seconds,
                                 'action_wall_seconds': action_timeout_seconds},
                      'replay': replay_context,
                      'split': 'engineering_demo', 'main_benchmark_ready': False})
        self.timer.start()
        try:
            setup_started = time.monotonic_ns()
            self.env, self.cwd, self.recorder_hash, self.source_lock = prepare_runtime(environment)
            self._phase('runtime_setup', time.monotonic_ns() - setup_started)
            self._ensure_active()
            self.inventory_path = None
            self.inventory_metadata = None
            cache, cached = None, None
            self.inventory_status = 'not_requested'
            if export_premises:
                cache_started = time.monotonic_ns()
                cache = PremiseCache(inventory_cache or ROOT / '.work/premise-cache',
                                     cache_key(environment, self.source_lock, self.recorder_hash), environment['target'])
                cached = cache.lookup()
                self.inventory_status = 'hit' if cached else 'miss'
                if cached:
                    self.inventory_path = cache.copy_to(self.directory, cached)
                    self.inventory_metadata = cached
                self._phase('premise_cache_lookup', time.monotonic_ns() - cache_started)
            write_json(self.directory / 'source-lock.json', self.source_lock)
            (self.directory / 'AtlasSession.lean').write_bytes((ROOT / 'formal/AtlasSession.lean').read_bytes())
            source = 'import AtlasSession\n' + environment['prefix'] + '\n' + environment['declaration'] + ' := by\n  atlas_session\n' + environment['suffix']
            (self.directory / 'Episode.lean').write_text(source)
            self.env.update(ATLAS_SESSION_TARGET=environment['target'],
                            ATLAS_SESSION_JOURNAL=str(self.directory / 'lean-events.ndjson'),
                            ATLAS_SESSION_COMMANDS=str(self.directory / 'commands.fifo'),
                            ATLAS_SESSION_RESPONSES=str(self.directory / 'responses.fifo'))
            if export_premises and not cached:
                self.env['ATLAS_SESSION_PREMISES'] = str(self.directory / 'allowed-premises.ndjson')
            else:
                self.env.pop('ATLAS_SESSION_PREMISES', None)
            self.responses = queue.Queue()
            self.log = (self.directory / 'process.log').open('w')
            for name in ['commands.fifo', 'responses.fifo']:
                os.mkfifo(self.directory / name, 0o600)
            self.commands = os.fdopen(os.open(self.directory / 'commands.fifo', os.O_RDWR | os.O_CLOEXEC), 'w')
            self.response_fd = os.open(self.directory / 'responses.fifo', os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            initial_started = time.monotonic_ns()
            self.proc = subprocess.Popen(self._lean_command(self.directory / 'Episode.lean'),
                                         cwd=self.cwd, env=self.env, stdin=subprocess.DEVNULL,
                                         stdout=self.log, stderr=self.log, text=True,
                                         bufsize=1, start_new_session=True)
            self.reader = threading.Thread(target=self._read_output, daemon=True)
            self.reader.start()
            ready = self._receive(max(0.001, self.deadline - time.monotonic()))
            if ready.get('kind') != 'ready' or ready.get('target_absent') is not True:
                raise RuntimeError(f'Invalid initial response: {ready}')
            self._phase('environment_initialization', time.monotonic_ns() - initial_started)
            self._record({'kind': 'ready', 'response': ready,
                          'premise_policy': 'all_theorems_in_source_prefix_environment' if export_premises else 'not_exported',
                          'inventory_cache': self.inventory_status,
                          'instrumentation_import': 'AtlasSession -> Lean'})
            if export_premises and not cached:
                cache_started = time.monotonic_ns()
                source = self.directory / 'allowed-premises.ndjson'
                metadata = cache.publish(source, ready['premise_count'], ready['premise_export_ns'])
                self.inventory_path = cache.copy_to(self.directory, metadata)
                self.inventory_metadata = metadata
                source.unlink()
                self._phase('premise_cache_publish', time.monotonic_ns() - cache_started)
            if self.inventory_metadata:
                self._record({'kind': 'premise_inventory', 'cache_status': self.inventory_status,
                              'inventory': self.inventory_metadata})
            self.state = ready['snapshot']
            self.initial_snapshot_sha256 = sha(self.state)
            self._remember({'phase': 'initial', 'event_index': -1, 'observation': self.observe()})
            self.initialized_ns = time.monotonic_ns()
            self._write_episode(self.terminal or 'running', getattr(self, 'terminal_error', None))
        except Exception as error:
            self._fail('process_error', str(error))
            raise SessionEnded(f'{self.terminal}: {error}; records: {self.directory}') from error

    def _lean_command(self, path):
        return ['lean', '-R', str(self.directory), '-j1', '-DmaxHeartbeats=20000000', str(path)]

    def _record(self, row):
        with self.lock:
            row = {**row, 'host_elapsed_ns': time.monotonic_ns() - self.started_ns}
            started = time.monotonic_ns()
            with self.journal.open('a') as handle:
                handle.write(encoded(row) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            self.phases['journal_serialization'] = self.phases.get('journal_serialization', 0) + time.monotonic_ns() - started

    def _remember(self, entry):
        entry = {**entry, 'budget': {'max_actions': self.max_actions,
                                    'action_timeout_seconds': self.action_timeout_seconds}}
        self.controller_memory.append(json.loads(encoded(entry)))
        started = time.monotonic_ns()
        with (self.directory / 'controller-memory.ndjson').open('a') as handle:
            handle.write(encoded(entry) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        self._phase('controller_memory_serialization', time.monotonic_ns() - started)

    def memory_history(self):
        """Original chronological model inputs and feedback, including after replay."""
        return json.loads(encoded(self.controller_memory))

    def _phase(self, name, duration_ns):
        self.phases[name] = self.phases.get(name, 0) + duration_ns
        self._record({'kind': 'cost', 'phase': name, 'duration_ns': duration_ns})
        if self.terminal:
            self._write_episode(self.terminal, getattr(self, 'terminal_error', None))

    @contextmanager
    def measure(self, phase):
        if phase not in {'premise_retrieval', 'candidate_generation', 'controller_inference'}:
            raise ValueError('Unknown external phase')
        started = time.monotonic_ns()
        try:
            yield
        finally:
            self._phase(phase, time.monotonic_ns() - started)

    def _read_output(self):
        pending = b''
        try:
            while True:
                ready, _, _ = select.select([self.response_fd], [], [], 0.1)
                chunk = os.read(self.response_fd, 65536) if ready else b''
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    if not line.startswith(b'ATLAS_SESSION '):
                        raise ValueError('Invalid protocol prefix')
                    self.responses.put(json.loads(line[len(b'ATLAS_SESSION '):]))
                if not chunk and self.proc.poll() is not None:
                    break
        except Exception as error:
            self.responses.put({'kind': 'fatal', 'error': str(error)})
        finally:
            os.close(self.response_fd)
            self.responses.put(None)

    def _receive(self, timeout):
        try:
            result = self.responses.get(timeout=min(timeout, max(0.001, self.deadline - time.monotonic())))
        except queue.Empty:
            self._fail('timeout', 'response_deadline')
            raise SessionEnded('timeout')
        if result is None or result.get('kind') == 'fatal':
            self._fail('process_error', str(result))
            raise SessionEnded(self.terminal)
        self._ensure_active()
        return result

    def _ensure_active(self):
        if self.terminal:
            raise SessionEnded(self.terminal)

    def _write_episode(self, outcome, error=None):
        elapsed = time.monotonic_ns() - self.started_ns
        initialized = getattr(self, 'initialized_ns', None)
        search_end = getattr(self, 'search_ended_ns', time.monotonic_ns())
        phases = dict(self.phases)
        measured = sum(phases.values())
        write_json(self.directory / 'episode.json', {
            'schema': SCHEMA, 'target': self.environment['target'], 'outcome': outcome,
            'error': error, 'split': 'engineering_demo', 'main_benchmark_ready': False,
            'executed_actions': self.attempts, 'remaining_actions': self.max_actions - self.attempts,
            'wall_elapsed_ns': elapsed, 'host_phase_ns': phases,
            'initialization_wall_ns': initialized - self.started_ns if initialized else elapsed,
            'search_wall_ns': max(0, search_end - initialized) if initialized else 0,
            'search_timing_policy': 'After initialization through the stop response or failure; excludes standalone final checking.',
            'premise_inventory_cache': getattr(self, 'inventory_status', 'not_requested'),
            'external_phase_status': {key: 'measured' if key in phases else 'not_instrumented'
                                      for key in ['premise_retrieval', 'candidate_generation', 'controller_inference']},
            'unattributed_wall_ns': max(0, elapsed - measured),
            'cost_policy': 'Host phases are sequential. Lean timers are subcomponents of round-trip time, never added again. Wall time includes setup, waits, failed attempts and final checks.',
            'successful_verification_receipt': outcome == 'proved'})

    def _kill(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=5)
            except ProcessLookupError:
                pass
        if hasattr(self, 'commands') and not self.commands.closed:
            self.commands.close()
        if hasattr(self, 'reader') and threading.current_thread() is not self.reader:
            self.reader.join(timeout=1)
        if hasattr(self, 'log'):
            self.log.close()
        for name in ['commands.fifo', 'responses.fifo']:
            (self.directory / name).unlink(missing_ok=True)

    def _fail(self, outcome, error=None):
        with self.lock:
            if self.terminal:
                return
            self.terminal = outcome
            if not hasattr(self, 'search_ended_ns'):
                self.search_ended_ns = time.monotonic_ns()
            self.terminal_error = error
            self.timer.cancel()
            self._kill()
            if outcome != 'proved':
                (self.directory / 'verification.json').unlink(missing_ok=True)
            self._record({'kind': 'terminal', 'outcome': outcome, 'error': error})
            self._write_episode(outcome, error)

    def _request(self, command, decision=None):
        self._ensure_active()
        index = len(self.history)
        self._record({'kind': 'request', 'event_index': index, 'command': command, 'decision': decision})
        started = time.monotonic_ns()
        try:
            self.commands.write(encoded(command) + '\n')
            self.commands.flush()
            result = self._receive(self.action_timeout_seconds)
        except (BrokenPipeError, OSError) as error:
            self._fail('process_error', str(error))
            raise SessionEnded(self.terminal) from error
        finally:
            self._phase('lean_round_trip', time.monotonic_ns() - started)
        if result.get('seq') != index:
            self._fail('process_error', 'Protocol sequence diverged')
            raise SessionEnded(self.terminal)
        if result.get('kind') not in {command['op'], 'protocol_error'}:
            self._fail('process_error', 'Unexpected protocol response')
            raise SessionEnded(self.terminal)
        if command['op'] == 'restore' and result['kind'] == 'restore':
            self.branch += 1
        row = {'kind': 'response', 'event_index': index, 'previous_event_index': index - 1 if index else None,
               'parent_event_index': self.producers.get(result.get('source_checkpoint', result.get('checkpoint'))),
               'branch_id': f'b{self.branch:04d}', 'response': result,
               'replay_reference': {'history_file': 'events.ndjson', 'stop_after_event': index,
                                    'environment_sha256': sha(self.environment)}}
        self._record(row)
        self.history.append({'command': command, 'response': result})
        if 'snapshot' in result:
            self.state = result['snapshot']
            self.checkpoint_id = result['checkpoint']
        if result['kind'] == 'step':
            self.producers[result['checkpoint']] = index
        self._remember(feedback_memory(index, result, self.observe(), row['parent_event_index'], decision))
        self._write_episode(self.terminal or 'running', getattr(self, 'terminal_error', None))
        if result['kind'] == 'protocol_error':
            raise ValueError(result['error'])
        return result

    def observe(self):
        """Allowlist only. No raw ppGoal, metavariables, proof, plan or expected labels."""
        return model_observation(self.state, self.history, self.checkpoint_id, self.branch,
                                 self.max_actions - self.attempts,
                                 self.deadline - time.monotonic(), self.terminal)

    def step(self, action, *, candidates=None, generator=None, chosen_id=None, expected=None):
        self._ensure_active()
        if self.attempts >= self.max_actions:
            self.stop('budget_exhausted')
            raise SessionEnded(self.terminal)
        if not isinstance(action, str) or not action.strip():
            raise ValueError('Action must be nonempty tactic text')
        observation = self.observe()
        decision = None
        if candidates is not None:
            ids = [c['id'] for c in candidates]
            if len(ids) != len(set(ids)) or chosen_id not in ids or not generator:
                raise ValueError('Candidate IDs, chosen ID and generator are required')
            chosen = next(c for c in candidates if c['id'] == chosen_id)
            if chosen['action'] != action:
                raise ValueError('Chosen candidate does not match action')
            decision = {'generator': generator, 'chosen_id': chosen_id,
                        'candidates': [{'id': c['id'], 'action': c['action'], 'premise': c.get('premise'), 'outcome': None} for c in candidates],
                        'candidate_set_id': sha({'environment_sha256': sha(self.environment),
                                                 'generator': generator, 'goals': observation['goals'],
                                                 'candidates': [{'id': c['id'], 'action': c['action']} for c in candidates]}),
                        'observation_sha256': sha(observation)}
        serialization_started = time.monotonic_ns()
        with (self.directory / 'observations.ndjson').open('a') as handle:
            handle.write(encoded({'event_index': len(self.history), 'observation': observation,
                                  'observation_sha256': sha(observation)}) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        self._phase('observation_serialization', time.monotonic_ns() - serialization_started)
        self._remember({'phase': 'before_action', 'event_index': len(self.history), 'observation': observation,
                        'candidates': offered_candidates(candidates), 'generator': generator})
        self.attempts += 1
        command = {'op': 'step', 'action': action}
        if expected is not None:
            command['expected'] = expected
        result = self._request(command, decision)
        before, after = result.get('before'), result.get('snapshot')
        if result['kind'] == 'step':
            self._record({'kind': 'action_feedback', 'event_index': len(self.history) - 1,
                          'chosen_id': chosen_id, 'tactic_acceptance': result['tactic_acceptance'],
                          'goals_before': len(before['goals']), 'goals_after': len(after['goals']),
                          'snapshot_changed': sha(before) != sha(after),
                          'value_target': None, 'future_proof_outcome': None})
        return self.observe()

    def snapshot(self, path=None):
        self._request({'op': 'snapshot'})
        commands = [entry['command'] for entry in self.history]
        responses = [response_fingerprint(entry['response']) for entry in self.history]
        checkpoint = {'schema': SCHEMA, 'environment': self.environment, 'source_lock': self.source_lock,
                      'recorder_sha256': self.recorder_hash, 'stop_after_event': len(self.history) - 1,
                      'commands': commands, 'response_fingerprints': responses,
                      'history_sha256': history_fingerprint(commands, responses),
                      'initial_snapshot_sha256': self.initial_snapshot_sha256,
                      'controller_memory': self.memory_history(),
                      'controller_memory_sha256': sha(self.controller_memory),
                      'snapshot_sha256': sha(self.state), 'checkpoint_id': self.checkpoint_id,
                      'max_actions': self.max_actions, 'actions_already_executed': self.attempts,
                      'action_timeout_seconds': self.action_timeout_seconds}
        checkpoint['remaining_wall_seconds'] = max(0.001, self.deadline - time.monotonic())
        write_json(path or self.directory / f'checkpoint-{len(self.history) - 1}.json', checkpoint)
        return checkpoint

    def restore(self, checkpoint):
        if isinstance(checkpoint, str) and re.fullmatch(r's\d+', checkpoint):
            checkpoint = int(checkpoint[1:])
        self._request({'op': 'restore', 'checkpoint': checkpoint})
        return self.observe()

    @classmethod
    def from_checkpoint(cls, checkpoint, directory, **options):
        if not isinstance(checkpoint, dict):
            checkpoint = json.loads(Path(checkpoint).read_text())
        if checkpoint.get('schema') != SCHEMA:
            raise ValueError('Legacy checkpoint lacks verified budgets/history. Reconstruct from its original episode journal instead.')
        if sha((ROOT / 'formal/AtlasSession.lean').read_bytes()) != checkpoint['recorder_sha256']:
            raise ValueError('Checkpoint recorder/version mismatch')
        if checkpoint['source_lock'] != json.loads((ROOT / 'data/source-lock.json').read_text()):
            raise ValueError('Checkpoint source lock mismatch')
        commands, fingerprints = checkpoint['commands'], checkpoint['response_fingerprints']
        if len(commands) != checkpoint['stop_after_event'] + 1 or len(fingerprints) != len(commands):
            raise ValueError('Checkpoint chronological cut point is inconsistent')
        if history_fingerprint(commands, fingerprints) != checkpoint['history_sha256']:
            raise ValueError('Checkpoint history fingerprint is invalid')
        if sha(checkpoint['controller_memory']) != checkpoint['controller_memory_sha256']:
            raise ValueError('Checkpoint controller-memory fingerprint is invalid')
        recorded = {'max_actions': checkpoint['max_actions'],
                    'timeout_seconds': checkpoint['remaining_wall_seconds'],
                    'action_timeout_seconds': checkpoint['action_timeout_seconds']}
        overrides = {key: {'recorded': value, 'effective': options[key], 'changed': options[key] != value}
                     for key, value in recorded.items() if key in options}
        for key, value in recorded.items():
            options.setdefault(key, value)
        if options['max_actions'] < sum(command['op'] == 'step' for command in commands):
            raise ValueError('Action override is smaller than the recorded replay prefix')
        options['replay_context'] = {'checkpoint_sha256': sha(checkpoint), 'recorded_budget': recorded,
                                     'explicit_budget_overrides': overrides,
                                     'history_verification': 'every_response_and_controller_memory'}
        session = cls(checkpoint['environment'], directory, **options)
        try:
            if session.initial_snapshot_sha256 != checkpoint['initial_snapshot_sha256']:
                raise ValueError('Fresh-process initial snapshot diverged')
            recorded_memory = checkpoint['controller_memory']
            feedback_by_event = {entry['event_index']: entry for entry in recorded_memory if entry['phase'] == 'feedback'}
            before_by_event = {entry['event_index']: entry for entry in recorded_memory if entry['phase'] == 'before_action'}
            continuation_timeout = session.action_timeout_seconds
            for index, command in enumerate(commands):
                session.action_timeout_seconds = feedback_by_event[index]['budget']['action_timeout_seconds']
                if command['op'] == 'step':
                    original = before_by_event[index]
                    session.step(command['action'], expected=command.get('expected'),
                                 candidates=original['candidates'], generator=original['generator'],
                                 chosen_id=feedback_by_event[index]['feedback']['chosen_id'])
                elif command['op'] in {'restore', 'snapshot'}:
                    session._request(command)
                else:
                    raise ValueError('Checkpoint contains a terminal or unknown operation')
                if response_fingerprint(session.history[-1]['response']) != fingerprints[index]:
                    raise ValueError(f'Fresh-process chronological response diverged at event {index}')
            session.action_timeout_seconds = continuation_timeout
            if sha(session.state) != checkpoint['snapshot_sha256'] or session.checkpoint_id != checkpoint['checkpoint_id']:
                raise ValueError('Fresh-process checkpoint state diverged')
            if len(session.controller_memory) != len(recorded_memory):
                raise ValueError('Fresh-process controller history length diverged')
            for actual, recorded_entry in zip(session.controller_memory, recorded_memory):
                # Resuming under an explicit new action-count limit does not
                # change the limits that the controller saw in its past inputs.
                delta = recorded_entry['budget']['max_actions'] - actual['budget']['max_actions']
                adjusted = canonical_memory(actual, delta)
                adjusted['budget'] = recorded_entry['budget']
                if adjusted != canonical_memory(recorded_entry):
                    raise ValueError('Fresh-process controller observation/feedback history diverged')
            # Keep actual replay costs/inputs inspectable, and expose the original
            # chronological sequence for restoring a stateful controller's memory.
            (session.directory / 'controller-memory.ndjson').replace(session.directory / 'replay-generated-memory.ndjson')
            observations = session.directory / 'observations.ndjson'
            if observations.exists():
                observations.replace(session.directory / 'replay-generated-observations.ndjson')
            session.controller_memory = json.loads(encoded(checkpoint['controller_memory']))
            with (session.directory / 'controller-memory.ndjson').open('w') as memory_file, observations.open('w') as inputs:
                for entry in session.controller_memory:
                    memory_file.write(encoded(entry) + '\n')
                    if entry['phase'] == 'before_action':
                        observation = entry['observation']
                        inputs.write(encoded({'event_index': entry['event_index'], 'observation': observation,
                                              'observation_sha256': sha(observation), 'origin': 'original_replayed_input'}) + '\n')
                for handle in [memory_file, inputs]:
                    handle.flush()
                    os.fsync(handle.fileno())
            session._record({'kind': 'checkpoint_reconstructed', 'checkpoint_sha256': sha(checkpoint),
                             'stop_after_event': checkpoint['stop_after_event'],
                             'verified_response_fingerprints': fingerprints,
                             'original_controller_memory_sha256': sha(session.controller_memory),
                             'explicit_budget_overrides': overrides})
            return session
        except Exception as error:
            session._fail('process_error', str(error))
            raise

    @classmethod
    def from_episode(cls, recorded_directory, stop_after_event, directory, **options):
        """Reconstruct any completed nonterminal event, including in a failed run."""
        recorded_directory = Path(recorded_directory)
        rows = read_journal(recorded_directory / 'events.ndjson')
        start = next(row for row in rows if row['kind'] == 'start')
        ready = next(row for row in rows if row['kind'] == 'ready')
        responses = {row['event_index']: row for row in rows if row['kind'] == 'response'}
        requests = {row['event_index']: row for row in rows if row['kind'] == 'request'}
        if stop_after_event < -1 or (stop_after_event >= 0 and stop_after_event not in responses):
            raise ValueError('No complete event exists at the requested cut point')
        selected = ready if stop_after_event == -1 else responses[stop_after_event]
        if selected['response']['kind'] not in {'ready', 'step', 'snapshot', 'restore'}:
            raise ValueError('Checkpoint cut point must describe an active Lean state')
        commands = [requests[index]['command'] for index in range(stop_after_event + 1)]
        fingerprints = [response_fingerprint(responses[index]['response']) for index in range(stop_after_event + 1)]
        memory_path = recorded_directory / 'controller-memory.ndjson'
        if memory_path.exists():
            memory = [entry for entry in read_journal(memory_path) if entry['event_index'] <= stop_after_event]
            memory_origin = 'original_controller_memory'
        else:
            # Version 1 saved exact decision observations, but no combined
            # memory ledger. Preserve those and reconstruct feedback causally.
            inputs = {row['event_index']: row['observation'] for row in read_journal(recorded_directory / 'observations.ndjson')}
            budget = {'max_actions': start['budget']['max_executed_actions'],
                      'action_timeout_seconds': start['budget']['action_wall_seconds']}
            state = ready['response']['snapshot']
            history, attempts, branch = [], 0, 0
            initial = model_observation(state, history, 0, branch, budget['max_actions'],
                                         start['budget']['episode_wall_seconds'] - ready['host_elapsed_ns'] / 1e9)
            memory = [{'phase': 'initial', 'event_index': -1, 'observation': initial, 'budget': budget}]
            for index, command in enumerate(commands):
                if command['op'] == 'step':
                    memory.append({'phase': 'before_action', 'event_index': index,
                                   'observation': inputs[index], 'budget': budget,
                                   'candidates': offered_candidates(requests[index]['decision']['candidates']) if requests[index]['decision'] else None,
                                   'generator': requests[index]['decision']['generator'] if requests[index]['decision'] else None})
                    attempts += 1
                row = responses[index]
                response = row['response']
                history.append({'command': command, 'response': response})
                if response['kind'] == 'restore':
                    branch += 1
                observation = model_observation(response['snapshot'], history, response['checkpoint'], branch,
                    budget['max_actions'] - attempts, start['budget']['episode_wall_seconds'] - row['host_elapsed_ns'] / 1e9)
                memory.append({**feedback_memory(index, response, observation, row['parent_event_index'], requests[index]['decision']), 'budget': budget})
            memory_origin = 'original_decision_inputs_and_causally_reconstructed_feedback'
        checkpoint = {'schema': SCHEMA,
                      'environment': json.loads((recorded_directory / 'environment.json').read_text()),
                      'source_lock': json.loads((recorded_directory / 'source-lock.json').read_text()),
                      'recorder_sha256': sha((recorded_directory / 'AtlasSession.lean').read_bytes()),
                      'stop_after_event': stop_after_event, 'commands': commands,
                      'response_fingerprints': fingerprints,
                      'history_sha256': history_fingerprint(commands, fingerprints),
                      'initial_snapshot_sha256': sha(ready['response']['snapshot']),
                      'controller_memory': memory, 'controller_memory_sha256': sha(memory),
                      'memory_origin': memory_origin,
                      'snapshot_sha256': sha(selected['response']['snapshot']),
                      'checkpoint_id': selected['response']['checkpoint'],
                      'max_actions': start['budget']['max_executed_actions'],
                      'action_timeout_seconds': start['budget']['action_wall_seconds'],
                      'remaining_wall_seconds': max(0.001, start['budget']['episode_wall_seconds'] - selected['host_elapsed_ns'] / 1e9)}
        return cls.from_checkpoint(checkpoint, directory, **options)

    def _winning_actions(self):
        actions = []
        checkpoint = self.checkpoint_id
        while checkpoint:
            index = self.producers[checkpoint]
            response = self.history[index]['response']
            if response['tactic_acceptance'] == 'accepted':
                actions.append(response['action'])
            checkpoint = response['source_checkpoint']
        return list(reversed(actions))

    def stop(self, reason='abandoned'):
        if reason not in {'abandoned', 'budget_exhausted'}:
            raise ValueError('Explicit stop reason must be abandoned or budget_exhausted')
        if self.terminal:
            return self.terminal
        result = self._request({'op': 'stop'})
        self.search_ended_ns = time.monotonic_ns()
        if not result['proof_closed']:
            self._fail(reason)
            return self.terminal
        started = time.monotonic_ns()
        try:
            self.commands.close()
            returncode = self.proc.wait(timeout=min(self.action_timeout_seconds, max(0.001, self.deadline - time.monotonic())))
            if returncode:
                raise RuntimeError('Instrumented declaration was rejected after the tactic finished')
            actions = self._winning_actions()
            source = self.environment['prefix'] + '\n' + self.environment['declaration'] + ' := by\n'
            source += ''.join('  ' + a.replace('\n', '\n  ') + '\n' for a in actions)
            source += self.environment['suffix'] + '\n#print axioms ' + self.environment['target'] + '\n'
            path = self.directory / 'CheckedProof.lean'
            path.write_text(source)
            final = subprocess.run(self._lean_command(path), cwd=self.cwd, env=self.env, text=True,
                                   capture_output=True, timeout=max(0.001, self.deadline - time.monotonic()))
            (self.directory / 'final-check.log').write_text(final.stdout + final.stderr)
            axiom_reported = any(
                line.startswith("'" + self.environment['target'] + "'") and
                ('depends on axioms:' in line or 'does not depend on any axioms' in line)
                for line in final.stdout.splitlines())
            if final.returncode or 'sorryAx' in final.stdout or not axiom_reported:
                raise RuntimeError('Standalone final proof was rejected or depends on sorryAx')
            self._phase('final_proof_check', time.monotonic_ns() - started)
            with self.lock:
                self._ensure_active()
                write_json(self.directory / 'verification.json', {
                    'outcome': 'proved', 'standalone_check_exit': final.returncode,
                    'axioms_output': final.stdout, 'proof_sha256': sha(path.read_bytes()),
                    'winning_actions': actions, 'winning_action_count': len(actions),
                    'scope': 'New proof elaborated with original imports; pinned compiled imports are trusted.',
                    'main_benchmark_ready': False})
                self._fail('proved')
        except subprocess.TimeoutExpired:
            self._phase('final_proof_check', time.monotonic_ns() - started)
            self._fail('timeout', 'final_proof_check_deadline')
        except Exception as error:
            self._phase('final_proof_check', time.monotonic_ns() - started)
            self._fail('final_proof_rejected', str(error))
        return self.terminal


def candidate_actions(observation):
    """Fixed baseline proposals; consumes only the present, filtered observation."""
    actions = ['assumption', 'rfl', 'simp only []', 'constructor', 'intro', 'left', 'right']
    if observation['goals']:
        for hypothesis in observation['goals'][0]['hypotheses']:
            name = hypothesis['name']
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_\u0027]*', name):
                actions.extend([f'exact {name}', f'apply {name}'])
    return [{'id': f'c{i:03d}', 'action': action} for i, action in enumerate(dict.fromkeys(actions))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', type=Path)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-actions', type=int)
    parser.add_argument('--timeout', type=float)
    parser.add_argument('--action-timeout', type=float)
    parser.add_argument('--export-premises', action='store_true')
    args = parser.parse_args()
    options = dict(export_premises=args.export_premises)
    if args.max_actions is not None:
        options['max_actions'] = args.max_actions
    if args.timeout is not None:
        options['timeout_seconds'] = args.timeout
    if args.action_timeout is not None:
        options['action_timeout_seconds'] = args.action_timeout
    if args.checkpoint:
        session = Session.from_checkpoint(args.checkpoint, args.output, **options)
    elif args.environment:
        session = Session(json.loads(args.environment.read_text()), args.output, **options)
    else:
        parser.error('Provide --environment or --checkpoint')
    print(encoded({'operation': 'start', 'observation': session.observe()}), flush=True)
    try:
        for line in sys.stdin:
            request = json.loads(line)
            op = request.pop('op')
            if op == 'step':
                result = session.step(**request)
            elif op == 'snapshot':
                checkpoint = session.snapshot()
                result = {'stop_after_event': checkpoint['stop_after_event'], 'state_id': session.observe()['state_id']}
            elif op == 'restore':
                result = session.restore(request['checkpoint'])
            elif op == 'stop':
                result = session.stop(request.get('reason', 'abandoned'))
            else:
                raise ValueError('Unknown operation')
            print(encoded({'operation': op, 'result': result}), flush=True)
            if session.terminal:
                break
    except SessionEnded:
        print(encoded({'terminal_outcome': session.terminal}), flush=True)
    except BaseException as error:
        session._fail('process_error', str(error))
        raise
    finally:
        if not session.terminal:
            session.stop('abandoned')


if __name__ == '__main__':
    main()
