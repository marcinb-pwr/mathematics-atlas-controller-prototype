"""Acquire the exact upstream Lean release and mathlib source used by this project."""
import concurrent.futures
import hashlib
import json
import pathlib
import subprocess
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / '.work'
VERSION = 'v4.19.0'
LOCK = json.loads((ROOT / 'data/source-lock.json').read_text())
WORK.mkdir(exist_ok=True)

def download_lean():
    url = 'https://github.com/leanprover/lean4/releases/download/v4.19.0/lean-4.19.0-linux.tar.zst'
    dest = WORK / 'lean-4.19.0-linux.tar.zst'
    if not dest.exists():
        temp = dest.with_suffix('.download')
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=90) as response, temp.open('wb') as output:
            total = 0
            checkpoint = 0
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                if total - checkpoint >= 64 * 1024 * 1024:
                    print(f'Lean download: {total // 2**20} MiB', flush=True)
                    checkpoint = total
        temp.replace(dest)
        print('Lean archive SHA256:', digest.hexdigest(), flush=True)
    digest = hashlib.file_digest(dest.open('rb'), 'sha256').hexdigest()
    if digest != LOCK['lean_archive_sha256']:
        raise RuntimeError('Lean archive does not match the pinned SHA256')
    if not (WORK / 'lean-4.19.0-linux/bin/lean').exists():
        subprocess.run(['tar', '--zstd', '--no-same-owner', '-xf', str(dest), '-C', str(WORK)], check=True)

def download_mathlib():
    dest = WORK / 'mathlib4'
    if not (dest / '.git').exists():
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', VERSION,
                        'https://github.com/leanprover-community/mathlib4.git', str(dest)], check=True)
    commit = subprocess.check_output(['git', '-C', str(dest), 'rev-parse', 'HEAD'], text=True).strip()
    assert commit == LOCK['mathlib_commit'], 'Mathlib commit differs from the source lock'
    assert (dest / 'lean-toolchain').read_text().strip() == 'leanprover/lean4:v4.19.0'
    print('Pinned mathlib commit:', commit, flush=True)

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(download_lean), pool.submit(download_mathlib)]
        for future in futures:
            future.result()
