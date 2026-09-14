#!/usr/bin/env python3
"""Keep the Lean import and seed lists aligned with the corpus manifest."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sync():
    corpus = json.loads((ROOT / 'data/corpus.json').read_text())
    names = [s['name'] for s in corpus['seeds']]
    branches = [b['id'] for b in corpus['branches']]
    assert len(names) == len(set(names)), 'Duplicate entry points'
    assert len(branches) == len(set(branches)), 'Duplicate branches'
    assert all(s['branch'] in branches for s in corpus['seeds'])
    qualified = r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*"
    assert all(re.fullmatch(qualified, n) for n in names + corpus['modules'])
    path = ROOT / 'formal/AtlasExport.lean'
    original = path.read_text()
    imports = ''.join(f'import {m}\n' for m in corpus['modules'])
    text = imports + original[original.index('import Lean.Replay'):]
    start = text.index('def seeds : Array Name := #[')
    end = text.index('\n]', start) + 2
    seeds = 'def seeds : Array Name := #[\n' + ',\n'.join(f'  ``{n}' for n in names) + '\n]'
    text = text[:start] + seeds + text[end:]
    if text != original:
        path.write_text(text)


if __name__ == '__main__':
    sync()
