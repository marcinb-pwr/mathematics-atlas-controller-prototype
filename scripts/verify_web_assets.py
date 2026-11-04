#!/usr/bin/env python3
"""Check the self-contained Atlas website against its pinned publication/export."""
import gzip
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    manifest = json.loads((ROOT / 'WEB_ASSETS.json').read_text())
    for item in manifest['files']:
        payload = (DIST / item['path']).read_bytes()
        require(len(payload) == item['bytes'], 'Wrong size: ' + item['path'])
        require(hashlib.sha256(payload).hexdigest() == item['sha256'],
                'Wrong SHA256: ' + item['path'])

    atlas_path = DIST / 'data/atlas.json.gz'
    with gzip.open(atlas_path, 'rt', encoding='utf-8') as handle:
        atlas = json.load(handle)
    require(len(atlas['declarations']) == atlas['metadata']['declaration_count'],
            'Declaration count mismatch')
    require('Nat.exists_infinite_primes' in atlas['declarations'],
            'Missing default theorem')

    dataset = DIST / 'data/mathematics-atlas-dataset.zip'
    checked = set()
    with zipfile.ZipFile(dataset) as archive:
        require(archive.testzip() is None, 'Corrupted dataset ZIP')
        require(archive.read('atlas.json.gz') == atlas_path.read_bytes(),
                'Website and dataset contain different graphs')
        with archive.open('raw/declarations.ndjson') as rows:
            for line in rows:
                original = json.loads(line)
                bundled = atlas['declarations'][original['name']]
                for field in ['name', 'kind', 'statement', 'type_expression',
                              'value_expression', 'value']:
                    require(bundled.get(field) == original.get(field),
                            'Changed exported field: ' + original['name'] + '.' + field)
                require(original['name'] not in checked, 'Duplicate exported declaration')
                checked.add(original['name'])
        require(checked == set(atlas['declarations']), 'Declaration membership mismatch')
        expected_expression_hash = atlas['metadata']['input_sha256']['.work/export/expressions.ndjson']
        with archive.open('raw/expressions.ndjson') as expressions:
            require(hashlib.file_digest(expressions, 'sha256').hexdigest() == expected_expression_hash,
                    'Dataset expression table differs from the pinned export')
    with gzip.open(DIST / 'data/expressions.ndjson.gz', 'rb') as expressions:
        require(hashlib.file_digest(expressions, 'sha256').hexdigest() == expected_expression_hash,
                'Standalone expression download differs from the dataset')
    for name in ['mathematics-atlas-source.zip', 'mathematics-atlas-replay-pilot.zip']:
        with zipfile.ZipFile(DIST / 'data' / name) as archive:
            require(archive.testzip() is None, 'Corrupted download: ' + name)

    print(json.dumps({'passed': True, 'published_assets': len(manifest['files']),
                      'declarations_checked': len(checked), 'nodes': len(atlas['nodes']),
                      'edges': len(atlas['edges']), 'branches': len(atlas['branches']),
                      'expression_table_sha256': expected_expression_hash}))


if __name__ == '__main__':
    main()
