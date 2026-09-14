#!/usr/bin/env python3
"""Stage public assets without redundant copies already in the download bundles."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
source, target = ROOT / 'dist', ROOT / 'out'
target.mkdir(exist_ok=True)


def ignore(path, names):
    relative = Path(path).relative_to(source)
    if relative == Path('data'):
        return {'details'}
    if relative == Path('data/replay-pilot'):
        return set(names) - {'README.md', 'verification.json', 'CheckedProof.lean'}
    return set()


shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)
for duplicate in [target / 'data/details']:
    if duplicate.exists():
        shutil.rmtree(duplicate)
pilot = target / 'data/replay-pilot'
for p in pilot.iterdir():
    if p.name not in {'README.md', 'verification.json', 'CheckedProof.lean'}:
        p.unlink()
assert (target / 'index.html').is_file()
assert (target / 'data/mathematics-atlas-replay-pilot.zip').is_file()
print('Staged the explorer and complete download bundles.')
