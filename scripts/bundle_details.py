#!/usr/bin/env python3
"""Migrate a legacy unbundled export without rerunning Lean.

The formal records, graph edges, and original verification receipt are preserved.
Full reproductions embed records directly in build_graph.py. Current compressed
editions are verified here but left unchanged.
"""
import gzip
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'dist/data'


def bundle():
    compressed = DATA / 'atlas.json.gz'
    atlas = json.loads(gzip.decompress(compressed.read_bytes()) if compressed.exists()
                       else (DATA / 'atlas.json').read_bytes())
    records = {}
    for path in sorted((DATA / 'details').glob('*.json')):
        shard = json.loads(path.read_text())
        assert not records.keys() & shard.keys(), 'Duplicate declaration'
        records.update(shard)
    names = {n['id'] for n in atlas['nodes'] if n['kind'] not in ('proof', 'concept')}
    assert records.keys() == names
    assert len(records) == atlas['metadata']['declaration_count']
    assert all(name == record['name'] for name, record in records.items())
    if 'declarations' in atlas:
        assert atlas['declarations'] == records, 'Bundled records differ from the shards'

    archive = DATA / 'mathematics-atlas-dataset.zip'
    with zipfile.ZipFile(archive) as z:
        for filename in ['declarations.ndjson', 'expressions.ndjson']:
            digest = hashlib.sha256(z.read('raw/' + filename)).hexdigest()
            assert digest == atlas['metadata']['input_sha256']['.work/export/' + filename]

    if compressed.exists():
        assert atlas['schemaVersion'] in ('1.2.0', '1.3.0')
        print(f'Verified {len(records):,} already bundled declarations; no migration needed.')
        return

    atlas['schemaVersion'] = '1.1.0'
    atlas['declarations'] = records
    payload = (json.dumps(atlas, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    assert len(payload) < 25 * 1024 * 1024, 'Bundled payload exceeds the hosting asset limit'
    (DATA / 'atlas.json').write_bytes(payload)
    shutil.copyfile(ROOT / 'SCHEMA.md', DATA / 'SCHEMA.md')
    replacement = archive.with_suffix('.tmp.zip')
    with zipfile.ZipFile(archive) as old, zipfile.ZipFile(replacement, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as new:
        for info in old.infolist():
            value = payload if info.filename == 'atlas.json' else (ROOT / 'SCHEMA.md').read_bytes() if info.filename == 'SCHEMA.md' else old.read(info)
            new.writestr(info, value)
    replacement.replace(archive)
    print(f'Bundled {len(records):,} unchanged declarations ({len(payload):,} bytes).')


if __name__ == '__main__':
    bundle()
