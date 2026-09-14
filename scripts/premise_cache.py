"""Content-checked theorem inventories for one exact pinned environment."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def cache_key(environment, source_lock, recorder_hash):
    payload = {'environment': environment, 'source_lock': source_lock,
               'recorder_sha256': recorder_hash,
               'exporter': 'AtlasSession/theorem-statements/v1', 'lean_options': {'maxHeartbeats': 20000000}}
    return digest(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())


def inventory_rows(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            yield json.loads(line)


class PremiseCache:
    def __init__(self, directory, key, target):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.key, self.target = key, target
        self.payload = self.directory / (key + '.ndjson.gz')
        self.manifest = self.directory / (key + '.json')

    def lookup(self):
        if not self.manifest.exists():
            return None
        metadata = json.loads(self.manifest.read_text())
        if metadata['cache_key'] != self.key or digest(self.payload.read_bytes()) != metadata['compressed_sha256']:
            raise ValueError('Premise cache identity or payload hash mismatch')
        names = set()
        for row in inventory_rows(self.payload):
            if set(row) != {'name', 'kind', 'statement'} or row['kind'] != 'theorem' or row['name'] in names:
                raise ValueError('Invalid premise cache row')
            names.add(row['name'])
        if len(names) != metadata['theorem_count'] or self.target in names:
            raise ValueError('Premise cache membership mismatch')
        return metadata

    def publish(self, source, theorem_count, export_ns):
        # Atomic per-file publication; the manifest is written last. Concurrent
        # first exporters may do duplicate work, but never publish partial files.
        with tempfile.TemporaryDirectory(dir=self.directory) as temporary:
            temp = Path(temporary) / 'inventory.ndjson.gz'
            with open(source, 'rb') as src, gzip.GzipFile(filename=str(temp), mode='wb', mtime=0) as dst:
                shutil.copyfileobj(src, dst)
            metadata = {'schema': 'atlas-premise-cache/1', 'cache_key': self.key,
                        'compressed_sha256': digest(temp.read_bytes()), 'theorem_count': theorem_count,
                        'origin': 'Lean_environment_export', 'original_export_ns': export_ns}
            meta = Path(temporary) / 'manifest.json'
            meta.write_text(json.dumps(metadata, sort_keys=True, indent=2) + '\n')
            temp.replace(self.payload)
            meta.replace(self.manifest)
        return self.lookup()

    def copy_to(self, directory, metadata):
        destination = Path(directory) / 'allowed-premises.ndjson.gz'
        shutil.copyfile(self.payload, destination)
        (Path(directory) / 'premise-inventory.json').write_text(json.dumps(metadata, indent=2) + '\n')
        return destination


def seed_from_episode(episode, cache_directory):
    """Reuse an explicitly supplied, previously verified recorded environment export."""
    episode = Path(episode)
    environment = json.loads((episode / 'environment.json').read_text())
    lock = json.loads((episode / 'source-lock.json').read_text())
    recorder_hash = digest((episode / 'AtlasSession.lean').read_bytes())
    events = [json.loads(line) for line in (episode / 'events.ndjson').read_text().splitlines()]
    ready = next(row['response'] for row in events if row['kind'] == 'ready')
    if not ready['target_absent'] or ready['premise_count'] is None:
        raise ValueError('Source episode did not export a complete premise inventory')
    cache = PremiseCache(cache_directory, cache_key(environment, lock, recorder_hash), environment['target'])
    if cache.lookup():
        return cache.key
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / 'inventory.ndjson'
        with gzip.open(episode / 'allowed-premises.ndjson.gz', 'rb') as src, source.open('wb') as dst:
            shutil.copyfileobj(src, dst)
        cache.publish(source, ready['premise_count'], ready['premise_export_ns'])
    return cache.key
