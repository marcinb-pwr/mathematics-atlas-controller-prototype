#!/usr/bin/env python3
"""Execute, replay and kernel-check one pinned source-prefix proof episode.

Requires the toolchain and caches acquired by scripts/reproduce.py.
No network is used by this script. Failure never produces a success receipt.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from reproduce import ROOT, runtime_environment

WORK = ROOT / '.work/replay'
OUT = ROOT / 'dist/data/replay-pilot'
MATHLIB = ROOT / '.work/mathlib4'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def ndjson(path, values):
    path.write_text(''.join(json.dumps(v, ensure_ascii=False, separators=(',', ':')) + '\n' for v in values))


def run(args, env, *, timeout=180, expect_success=True):
    start = time.monotonic()
    proc = subprocess.run(args, cwd=MATHLIB, env=env, capture_output=True, text=True, timeout=timeout)
    if (proc.returncode == 0) != expect_success:
        raise RuntimeError(f'Unexpected Lean exit {proc.returncode}:\n{proc.stdout}\n{proc.stderr}')
    return {'elapsed_ms': round((time.monotonic() - start) * 1000),
            'exit_code': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr}


def normalized_trace(trace):
    return {**trace, 'events': [{k: v for k, v in e.items() if k != 'elapsed_ms'} for e in trace['events']]}


def winning_actions(events):
    result, state = [], len(events)
    while state:
        e = events[state - 1]
        if e['outcome'] == 'success':
            result.append(e['action'])
        state = e['source_state']
    return list(reversed(result))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-existing', action='store_true',
                        help='Verify the shipped replay trace in fresh Lean processes without replacing it.')
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    lock = json.loads((ROOT / 'data/source-lock.json').read_text())
    plan = json.loads((ROOT / 'data/pilot-plan.json').read_text())
    assert len(plan['actions']) <= plan['budget']['max_executed_actions']
    env = runtime_environment()
    actual = subprocess.check_output(['lean', '--githash'], env=env, text=True).strip()
    assert actual == lock['lean_commit'], 'Wrong Lean binary'
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=MATHLIB, text=True).strip()
    assert actual == lock['mathlib_commit'], 'Wrong mathlib checkout'
    packages = json.loads((MATHLIB / 'lake-manifest.json').read_text())['packages']
    assert {p['name']: p['rev'] for p in packages} == {p['name']: p['rev'] for p in lock['packages']}
    for p in packages:
        actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=MATHLIB / '.lake/packages' / p['name'], text=True).strip()
        assert actual == p['rev'], f'Wrong package revision: {p["name"]}'
    # Refuse changed tracked sources; ignored compiled caches are expected.
    for checkout in [MATHLIB] + [MATHLIB / '.lake/packages' / p['name'] for p in packages]:
        subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--'], cwd=checkout, check=True, capture_output=True)

    source = MATHLIB / (plan['module'].replace('.', '/') + '.lean')
    original = source.read_text()
    marker = 'theorem Prime.dvd_mul '
    assert original.count(marker) == 1
    offset = original.index(marker)
    prefix = original[:offset]
    statement = original[offset:original.index(':=', offset)].rstrip()
    imports = re.findall(r'^import (\S+)', prefix, flags=re.M)
    assert plan['module'] not in imports
    (WORK / 'source-prefix.lean').write_text(prefix)
    compile_result = run(['lake', 'env', 'lean', '-R', str(ROOT / 'formal'), '-o',
                         str(WORK / 'AtlasReplay.olean'), str(ROOT / 'formal/AtlasReplay.lean')], env)
    search_paths = subprocess.check_output(['lake', 'env', 'printenv', 'LEAN_PATH'], cwd=MATHLIB, env=env, text=True).strip()
    env['LEAN_PATH'] = str(WORK) + os.pathsep + search_paths
    env['ATLAS_REPLAY_PLAN'] = str(ROOT / 'data/pilot-plan.json')
    env['ATLAS_PREMISE_OUTPUT'] = str(WORK / 'allowed-premises.ndjson')
    env['ATLAS_REPLAY_OUTPUT'] = str(WORK / 'trace.json')
    instrumented = 'import AtlasReplay\n' + prefix + '\natlas_available\n' + statement + ' := by\n  atlas_pilot\n\nend Nat\n'
    (WORK / 'Episode.lean').write_text(instrumented)
    command = ['lean', '-R', str(WORK), '-j1', '-DmaxHeartbeats=' + str(plan['budget']['max_heartbeats']), str(WORK / 'Episode.lean')]
    print('Executing the source-prefix episode and exporting the allowed theorem pool.', flush=True)
    first = run(command, env, timeout=plan['budget']['process_timeout_seconds'])
    trace = json.loads((WORK / 'trace.json').read_text())
    premise_hash = sha(WORK / 'allowed-premises.ndjson')
    print('Replaying every action, failed attempt and branch restore in a fresh Lean process.', flush=True)
    second = run(command, env, timeout=plan['budget']['process_timeout_seconds'])
    replay = json.loads((WORK / 'trace.json').read_text())
    assert normalized_trace(trace) == normalized_trace(replay), 'Replay diverged'
    assert premise_hash == sha(WORK / 'allowed-premises.ndjson'), 'Allowed environment changed'
    assert trace['target_absent_at_start'] is True
    assert not trace['events'][-1]['after']['goals']
    names = []
    for line in (WORK / 'allowed-premises.ndjson').open():
        row = json.loads(line)
        assert set(row) == {'name', 'kind', 'statement'}
        names.append(row['name'])
    assert plan['target'] not in names
    assert len(names) == len(set(names))
    assert any(e['outcome'] == 'error' for e in trace['events'])
    assert any(e['restored_from_other_branch'] for e in trace['events'])
    for e in trace['events']:
        source_view = trace['initial'] if not e['source_state'] else trace['events'][e['source_state'] - 1]['after']
        assert e['before'] == source_view, 'State reference does not reproduce its source snapshot'
        if e['outcome'] == 'error':
            assert e['before'] == e['after'], 'Failed tactic contaminated the restored state'

    actions = winning_actions(trace['events'])
    proof = 'by\n' + ''.join('  ' + a + '\n' for a in actions)
    # This source has no recorder import and contains only the successful branch.
    checked = prefix + statement + ' := ' + proof + '\nend Nat\n\n#print axioms Nat.Prime.dvd_mul\n'
    (WORK / 'CheckedProof.lean').write_text(checked)
    print('Checking the standalone final proof and rejecting a deliberately mistyped variant.', flush=True)
    checked_result = run(['lean', '-R', str(WORK), '-j1', str(WORK / 'CheckedProof.lean')], env)
    assert 'sorryAx' not in checked_result['stdout']
    assert 'depends on axioms:' in checked_result['stdout']
    invalid = prefix + statement + ' := by\n  exact True.intro\n\nend Nat\n'
    (WORK / 'NegativeControl.lean').write_text(invalid)
    negative = run(['lean', '-R', str(WORK), '-j1', str(WORK / 'NegativeControl.lean')], env, expect_success=False)
    assert 'type mismatch' in negative['stdout']

    if args.verify_existing:
        saved = json.loads((OUT / 'trace.json').read_text())
        assert normalized_trace(saved) == normalized_trace(trace), 'Shipped episode differs from execution'
        receipt = json.loads((OUT / 'verification.json').read_text())
        for name, digest in receipt['sha256'].items():
            assert sha(OUT / name) == digest, f'Artifact changed: {name}'
        assert receipt['allowed_premises_sha256_uncompressed'] == premise_hash
        print('Shipped pilot replay and final proof verified.', flush=True)
        return

    OUT.mkdir(parents=True, exist_ok=True)
    for name in ['source-prefix.lean', 'Episode.lean', 'CheckedProof.lean']:
        shutil.copyfile(WORK / name, OUT / name)
    shutil.copyfile(ROOT / 'formal/AtlasReplay.lean', OUT / 'AtlasReplay.lean')
    shutil.copyfile(ROOT / 'data/pilot-plan.json', OUT / 'plan.json')
    shutil.copyfile(ROOT / 'data/source-lock.json', OUT / 'source-lock.json')
    dump(OUT / 'trace.json', trace)
    (OUT / 'allowed-premises.ndjson.gz').write_bytes(gzip.compress((WORK / 'allowed-premises.ndjson').read_bytes(), mtime=0))
    environment_id = 'env:' + hashlib.sha256((lock['snapshot_id'] + sha(WORK / 'source-prefix.lean') + sha(ROOT / 'formal/AtlasReplay.lean')).encode()).hexdigest()[:24]
    episode_id = 'episode:nat-prime-dvd-mul:pilot01'
    environment = {'id': environment_id, 'snapshot_id': lock['snapshot_id'], 'module': plan['module'],
        'source_position': {'line': prefix.count('\n') + 1, 'column': 0},
        'source_path': str(source.relative_to(MATHLIB)), 'source_sha256': sha(source),
        'source_prefix': 'source-prefix.lean', 'source_prefix_sha256': sha(WORK / 'source-prefix.lean'),
        'original_imports': imports, 'instrumentation_imports': ['AtlasReplay'],
        'scope': 'Reexecute the entire byte-exact prefix, including namespaces, variables, notation, attributes and options.',
        'target_absent': True, 'allowed_premises': 'allowed-premises.ndjson.gz',
        'premise_pool_policy': 'Every theorem constant in the actual environment after the source prefix and recorder import; independent of the atlas selected-result closure.',
        'allowed_theorem_count': len(names), 'options': {'maxHeartbeats': plan['budget']['max_heartbeats'], 'threads': 1},
        'replay': {'command': 'python scripts/collect_pilot.py --verify-existing',
                   'source': 'Episode.lean', 'plan': 'plan.json', 'requires': 'Pinned source checkout, package revisions and Lean toolchain in source-lock.json'},
        'instrumentation_note': 'Recorder imports Lean, expanding the original import environment. The standalone final proof also passes with the original imports alone.'}
    ndjson(OUT / 'environments.ndjson', [environment])
    states, transitions = [], []
    histories = [[]]
    views = [trace['initial']] + [e['after'] for e in trace['events']]
    for index, view in enumerate(views):
        if index:
            e = trace['events'][index - 1]
            histories.append(histories[e['source_state']] + ([e['index']] if e['outcome'] == 'success' else []))
        states.append({'id': f's{index}', 'episode_id': episode_id, 'environment_id': environment_id,
            **view, 'replay_reference': {'source': 'Episode.lean', 'plan': 'plan.json',
                'stop_after_event': index - 1 if index else None, 'successful_ancestry_events': histories[index],
                'method': 'Run ordered plan through stop_after_event with recorded restores; the full transcript is authoritative.'}})
    total_ms = 0
    for e, step in zip(trace['events'], plan['actions'], strict=True):
        i = e['index']
        total_ms += e['elapsed_ms']
        source_index = e['source_state']
        transitions.append({'id': f't{i}', 'episode_id': episode_id, 'event_index': i,
            'previous_event_id': f't{i-1}' if i else None,
            'parent_event_id': f't{source_index-1}' if source_index else None,
            'branch_id': step['branch'], 'before_state_id': f's{source_index}', 'after_state_id': f's{i+1}',
            'restored_from_other_branch': e['restored_from_other_branch'],
            'candidates': [{'id': f'a{i}', 'action': e['action'], 'generator': 'hand-authored pilot plan'}],
            'candidate_set_note': 'Singleton executed proposal. This demonstration does not supervise ranking among alternatives.',
            'chosen_action_id': f'a{i}', 'action': e['action'], 'outcome': e['outcome'], 'error': e['error'],
            'elapsed_ms': e['elapsed_ms'], 'cumulative_action_ms': total_ms,
            'remaining_action_budget': plan['budget']['max_executed_actions'] - i - 1})
    ndjson(OUT / 'states.ndjson', states)
    ndjson(OUT / 'transitions.ndjson', transitions)
    ndjson(OUT / 'episodes.ndjson', [{'id': episode_id, 'environment_id': environment_id,
        'target': plan['target'], 'split': 'engineering_demo', 'eligible_for_benchmark': False,
        'collection_policy': plan['collection_policy'], 'initial_budget': plan['budget'],
        'outcome': 'proved', 'final_state_id': f's{len(trace["events"])}',
        'final_proof': 'CheckedProof.lean', 'action_count': len(trace['events']),
        'failed_action_count': sum(e['outcome'] == 'error' for e in trace['events']),
        'branch_restore_count': sum(e['restored_from_other_branch'] for e in trace['events']),
        'recording_process_ms': first['elapsed_ms'], 'replay_process_ms': second['elapsed_ms'],
        'timing_note': 'Process timings include prefix elaboration and premise export. Event times include restore/action/snapshot overhead; no speed comparison is made.'}])
    dump(OUT / 'verification.json', {'schema_version': '1.0.0', 'target': plan['target'],
        'fresh_process_replay': 'passed', 'all_recorded_states_match': True, 'target_absent_at_start': True,
        'failed_actions_restore_full_state': True, 'standalone_proof': 'passed',
        'kernel_check_scope': 'Lean source elaboration checks this new proof; imported oleans are pinned and trusted. This pilot does not claim a fresh kernel replay of all imported declarations.',
        'negative_control': 'mistyped proof rejected', 'checked_proof_output': checked_result['stdout'],
        'allowed_premises_sha256_uncompressed': premise_hash,
        'compiler_ms': compile_result['elapsed_ms'], 'proof_check_ms': checked_result['elapsed_ms'],
        'sha256': {p.name: sha(p) for p in sorted(OUT.iterdir()) if p.is_file() and p.name not in ('verification.json', 'README.md')}})
    shutil.copyfile(ROOT / 'REPLAY.md', OUT / 'README.md')
    with zipfile.ZipFile(ROOT / 'dist/data/mathematics-atlas-replay-pilot.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for p in sorted(OUT.iterdir()):
            if p.is_file():
                archive.write(p, p.name)
        for filename in ['LICENSE', 'NOTICE.md']:
            archive.write(ROOT / filename, filename)
    print(json.dumps({'target': plan['target'], 'actions': len(trace['events']),
                      'failures': sum(e['outcome'] == 'error' for e in trace['events']),
                      'states': len(states), 'available_theorems': len(names), 'verified': True}), flush=True)


if __name__ == '__main__':
    main()
