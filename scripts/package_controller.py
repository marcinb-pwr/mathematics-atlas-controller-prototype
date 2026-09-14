#!/usr/bin/env python3
"""Package source, executed controller examples and unchanged regression data."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from verify_controller_collection import verify

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--examples', type=Path, required=True)
    parser.add_argument('--tests', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    collection = json.loads((args.examples / 'collection.json').read_text())
    tests = json.loads((args.tests / 'test-results.json').read_text())
    audit = verify(args.examples)
    if audit != json.loads((args.examples / 'structural-audit.json').read_text()):
        raise SystemExit('Collection changed after its audit; regenerate structural-audit.json')
    if not tests['passed']:
        raise SystemExit('Refusing to package a failed test receipt')
    if not audit['passed'] or len(audit['episodes']) != len(collection['episodes']):
        raise SystemExit('Refusing to package a missing or incomplete collection audit')
    payload = {}
    roots = ['CONTROLLER.md', 'REPLAY.md', 'SCHEMA.md', 'LICENSE', 'NOTICE.md', '.gitignore',
             'data/corpus.json', 'data/pilot-plan.json', 'data/source-lock.json',
             'data/verification.json', 'data/controller-smoke.json',
             'dist/app.mjs', 'dist/graph-core.mjs', 'dist/load-atlas.mjs', 'dist/index.html', 'dist/styles.css']
    for name in roots:
        payload[name] = (ROOT / name).read_bytes()
    project_readme = ROOT / 'PROJECT_README.md'
    payload['PROJECT_README.md'] = (project_readme if project_readme.exists() else ROOT / 'README.md').read_bytes()
    for folder in ['formal', 'scripts', 'tests', 'dist/data/replay-pilot']:
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                payload[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for source, prefix in [(args.examples, 'controller-examples'), (args.tests, 'controller-tests')]:
        for path in source.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix not in {'.pyc', '.fifo'}:
                payload[prefix + '/' + path.relative_to(source).as_posix()] = path.read_bytes()
    payload['README.md'] = (
        '# Mathematics Atlas controller prototype — version 2\n\n'
        'Start with [CONTROLLER.md](CONTROLLER.md) for setup, interaction, replay,\n'
        'record schemas, verification scope and collection commands.\n\n'
        f"This bundle contains the source, {len(collection['episodes'])} executed Nat/Finset engineering\n"
        f"episodes, {tests['tests_run']} passed regression tests and the original pilot fixture.\n"
        'Replay preserves action timeouts and verifies chronological feedback;\n'
        'the common action generator retrieves available theorem premises.\n'
        'Benchmark readiness remains false. The static graph and expression table\n'
        'are supplied in the separate Mathematics Atlas dataset download.\n\n'
        'Verify file integrity with `sha256sum -c SHA256SUMS` after extraction.\n'
    ).encode()
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        previous = ROOT / 'CONTROLLER_MANIFEST.json'
        commit = json.loads(previous.read_text())['source_base_commit'] if previous.exists() else None
    outcomes = {}
    for episode in collection['episodes']:
        outcomes[episode['outcome']] = outcomes.get(episode['outcome'], 0) + 1
    manifest = {
        'schema': 'atlas-controller-bundle/2', 'source_base_commit': commit,
        'source_revision_policy': 'Recovered base commit plus updated source bytes identified by payload_sha256; this artifact has no new Git commit.',
        'main_benchmark_ready': False, 'controller_trained': False,
        'lean_reexecuted': tests['lean_reexecuted'], 'integration_tests': tests,
        'additional_execution_checks': {
            'retrieved_premise_history_and_continuation': 'controller-tests/retrieved-prefix-resumed/collection-replay-check.json',
            'version_1_episode_migration': 'controller-tests/legacy-episode-replay/migration-check.json'},
        'collection': {'episodes': len(collection['episodes']), 'outcomes': outcomes,
                       'distinct_targets': len({e['target'] for e in collection['episodes']}),
                       'executed_actions': sum(e['executed_actions'] for e in collection['episodes']),
                       'rejected_tactics': sum(e['rejected_tactics'] for e in collection['episodes']),
                       'multistep_proved_episodes': sum(len(e.get('winning_actions') or []) > 1 for e in collection['episodes']),
                       'candidate_size_range': audit['candidate_size_range'],
                       'split': 'engineering_demo'},
        'collection_audit': {'passed': audit['passed'], 'lean_reexecuted_by_this_audit': False,
                             'candidate_sets_regenerated': audit['candidate_sets_regenerated']},
        'replay': {'action_timeout_preserved': True, 'budget_overrides_recorded': True,
                   'chronological_response_fingerprints': True, 'original_controller_memory_preserved': True},
        'retrieval': {'available_environment_premises': True, 'inventory_cache': True,
                      'statement_association_graph': True, 'static_atlas_proof_graph_features': False},
        'verification_scope': 'New proof and replay integration checks with pinned, trusted compiled imports. No repeat of the entire static-corpus kernel audit.',
        'original_pilot_preserved': True,
        'not_included': ['Lean/toolchain binaries and caches', 'separate static graph/expression dataset'],
        'pending': ['representative target and family review', 'reviewed classification coverage', 'frozen benchmark splits',
                    'complete static-graph feature sanitization', 'large-scale search collection', 'BDH training and evaluation'],
        'payload_sha256': {name: digest(data) for name, data in sorted(payload.items())},
    }
    payload['CONTROLLER_MANIFEST.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    payload['SHA256SUMS'] = ''.join(f'{digest(data)}  {name}\n' for name, data in sorted(payload.items())).encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(payload.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(args.output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('ZIP integrity check failed')
        for name, data in payload.items():
            if archive.read(name) != data:
                raise RuntimeError(f'ZIP content mismatch: {name}')
        receipt = json.loads(archive.read('dist/data/replay-pilot/verification.json'))
        for name, expected in receipt['sha256'].items():
            if digest(archive.read('dist/data/replay-pilot/' + name)) != expected:
                raise RuntimeError(f'Original regression fixture changed: {name}')
    print(json.dumps({'file': str(args.output.resolve()), 'bytes': args.output.stat().st_size,
                      'files': len(payload), 'sha256': digest(args.output.read_bytes()),
                      'collection': manifest['collection'], 'tests_passed': tests['tests_run']}))


if __name__ == '__main__':
    main()
