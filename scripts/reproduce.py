#!/usr/bin/env python3
"""Reproduce the pinned corpus on Linux x86_64, using only Python's stdlib."""
import json
import hashlib
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from sync_corpus import sync

ROOT = Path(__file__).resolve().parents[1]
LEAN = ROOT / '.work/lean-4.19.0-linux'
MODULES = [m.replace('.', '/') + '.lean' for m in json.loads((ROOT / 'data/corpus.json').read_text())['modules']]


def runtime_environment():
    env = os.environ.copy()
    env.update(PATH=str(LEAN / 'bin') + ':' + env['PATH'], LEAN_SYSROOT=str(LEAN),
               ATLAS_OUTPUT_DIR=str(ROOT / '.work/export'), TAR_OPTIONS='--no-same-owner')
    # Some managed Linux runtimes expose the caller's executable only through
    # /proc/self/exe. Adapt that self-reference; do not change Lean or its kernel.
    try:
        os.readlink(f'/proc/{os.getpid()}/exe')
    except PermissionError:
        os.readlink('/proc/self/exe')
        source = ROOT / 'scripts/proc_self_compat.c'
        fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        shim = ROOT / ('.work/proc_self_compat_' + fingerprint + '.so')
        if not shim.exists():
            with tempfile.TemporaryDirectory(dir=ROOT / '.work') as temporary:
                built = Path(temporary) / 'adapter.so'
                subprocess.run(['gcc', '-shared', '-fPIC', str(source), '-o', str(built), '-ldl'], check=True)
                built.replace(shim)
        env['LD_PRELOAD'] = str(shim) + (':' + env['LD_PRELOAD'] if env.get('LD_PRELOAD') else '')
        print('Enabled the self-executable path compatibility adapter.', flush=True)
    return env


def main():
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64'):
        raise SystemExit('This acquisition script uses the pinned Linux x86_64 release. See README for other systems.')
    sync()
    subprocess.run([sys.executable, str(ROOT / 'scripts/acquire_toolchain.py')], check=True)
    env = runtime_environment()
    lock = json.loads((ROOT / 'data/source-lock.json').read_text())
    subprocess.run(['lean', '--version'], env=env, check=True)
    commit = subprocess.check_output(['lean', '--githash'], env=env, text=True).strip()
    assert commit == lock['lean_commit'], 'Lean build differs from the pinned source'
    cwd = ROOT / '.work/mathlib4'
    subprocess.run(['lake', 'exe', 'cache', 'get', *MODULES], cwd=cwd, env=env, check=True)
    manifest = json.loads((cwd / 'lake-manifest.json').read_text())
    actual = {p['name']: p['rev'] for p in manifest['packages']}
    assert actual == {p['name']: p['rev'] for p in lock['packages']}, 'Package lock differs'
    subprocess.run(['lake', 'env', 'lean', str(ROOT / 'formal/AtlasExport.lean')], cwd=cwd, env=env, check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/build_graph.py')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/collect_pilot.py')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'tests/test_training.py')], check=True)
    subprocess.run(['node', '--test', str(ROOT / 'tests/test_graph.mjs'), str(ROOT / 'tests/test_loading.mjs')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/package_source.py')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/stage_public.py')], check=True)


if __name__ == '__main__':
    main()
