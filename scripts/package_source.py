#!/usr/bin/env python3
"""Package the reproducible project without binaries, caches, or credentials."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / 'dist/data/mathematics-atlas-source.zip'
paths = [ROOT / p for p in ['README.md', 'SCHEMA.md', 'REPLAY.md', 'CONTROLLER.md', 'LICENSE', 'NOTICE.md', '.gitignore',
                          'dist/index.html', 'dist/styles.css', 'dist/app.mjs', 'dist/graph-core.mjs', 'dist/load-atlas.mjs',
                          'data/source-lock.json', 'data/verification.json', 'data/corpus.json', 'data/pilot-plan.json',
                          'data/controller-smoke.json']]
for directory in ['formal', 'scripts', 'tests']:
    paths.extend(p for p in (ROOT / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for path in sorted(paths):
        z.write(path, str(path.relative_to(ROOT)))
print(f'Packaged {len(paths)} project files ({target.stat().st_size:,} bytes).')
