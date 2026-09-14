#!/usr/bin/env python3
"""Build a browser index and independently audit a Lean declaration export."""
from __future__ import annotations

import hashlib
import gzip
import json
import re
import shutil
import sys
import zipfile
from collections import Counter, defaultdict, deque
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from training_metadata import annotate, metadata_fields

ROOT = Path(__file__).resolve().parent.parent
EXPORT = ROOT / '.work/export'
OUT = ROOT / 'dist/data'
MATHLIB = ROOT / '.work/mathlib4'
LEAN = ROOT / '.work/lean-4.19.0-linux'

CORPUS = json.loads((ROOT / 'data/corpus.json').read_text())
TOPICS = CORPUS['topics']
BRANCHES = CORPUS['branches']
SEED_INFO = {seed['name']: seed for seed in CORPUS['seeds']}
CURATED = {name: (s['label'], s['summary'], s['formula'], s['topics']) for name, s in SEED_INFO.items()}
AXIOM_DESCRIPTIONS = {
    'propext': ('Propositional extensionality', 'Equivalent propositions are equal. This is a foundational axiom of this Lean environment.'),
    'Classical.choice': ('Choice', 'Select an element of a nonempty type. This is Lean’s type-theoretic choice axiom.'),
    'Quot.sound': ('Quotient soundness', 'Related representatives determine equal quotient elements. This accompanies Lean’s primitive quotient support.'),
}


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n')


def audit(records, expressions, report):
    by_name = {r['name']: r for r in records}
    assert len(by_name) == len(records), 'Duplicate declarations'
    assert report['kernel_replay'] == 'passed' and report['kernel_trust_level'] == 0
    assert report['kernel_negative_control'] == 'invalid proof rejected with type mismatch'
    assert len(records) == report['declaration_count']
    assert len(expressions) == report['expression_count']
    assert set(report['seeds']) == set(CURATED), 'Unexpected seed scope'
    assert not any('sorryAx' == n or n.endswith('.sorryAx') for n in by_name), 'Admitted proof found'

    def child_ids(e):
        return {'app': e[1:3], 'lambda': e[2:4], 'forall': e[2:4],
                'let': e[2:5], 'projection': e[3:4]}.get(e[0], [])

    # Validate the DAG once; compute references per declaration with bounded
    # caching instead of retaining a transitive set for every expression node.
    valid = {'bvar', 'sort', 'const', 'app', 'lambda', 'forall', 'let', 'nat_literal', 'string_literal', 'projection'}
    for i, expr in enumerate(expressions):
        assert expr[0] in valid, f'Unsupported or open expression {i}'
        for child in child_ids(expr):
            assert type(child) is int and 0 <= child < i, f'Invalid expression reference at {i}'
        if expr[0] in ('const', 'projection'):
            assert expr[1] in by_name, f'Unresolved expression constant {expr[1]}'
        if expr[0] == 'sort':
            assert 'mvar' not in json.dumps(expr[1]), 'Unresolved universe'

    @lru_cache(maxsize=512)
    def expression_references(root):
        refs, projections, visited, todo = set(), set(), set(), [root]
        while todo:
            i = todo.pop()
            if i in visited:
                continue
            visited.add(i)
            e = expressions[i]
            if e[0] == 'const':
                refs.add(e[1])
            elif e[0] == 'projection':
                projections.add(e[1])
            todo.extend(child_ids(e))
        return frozenset(refs), frozenset(projections)

    for r in records:
        assert r['kernel_rechecked'] is True
        for field, rootfield in [('type_dependencies', 'type_expression'), ('value_dependencies', 'value_expression')]:
            assert set(r[field]) <= by_name.keys(), f'Unresolved {field}: {r["name"]}'
            root = r[rootfield]
            if root is None:
                assert not r[field]
            else:
                assert type(root) is int and 0 <= root < len(expressions)
                assert expression_references(root)[0] == set(r[field]), f'Expression/reference mismatch: {r["name"]} {field}'
        assert set(r['kernel_dependencies']) <= by_name.keys()
        assert set(r['axioms']) <= by_name.keys()
        if r['kind'] == 'theorem':
            assert r['value_expression'] is not None, f'Missing proof {r["name"]}'
        for rule in r['recursor_rules']:
            assert rule['constructor'] in by_name
            assert 0 <= rule['expression'] < len(expressions)
        structural = set(expression_references(r['type_expression'])[1])
        if r['value_expression'] is not None:
            structural.update(expression_references(r['value_expression'])[1])
        if r['kind'] == 'inductive':
            structural.update(r['kernel_metadata']['all'])
            structural.update(r['kernel_metadata']['constructors'])
        if r['kind'] == 'constructor':
            structural.update([r['name'], r['kernel_metadata']['inductive']])
        if r['kind'] == 'recursor':
            structural.update(r['kernel_metadata']['all'])
            for rule in r['recursor_rules']:
                structural.add(rule['constructor'])
                structural.update(expression_references(rule['expression'])[0])
                structural.update(expression_references(rule['expression'])[1])
        structural.difference_update(r['type_dependencies'] + r['value_dependencies'])
        assert structural == set(r['kernel_dependencies']), f'Structural reference mismatch: {r["name"]}'

    reachable, todo = set(), list(report['seeds'])
    while todo:
        n = todo.pop()
        if n in reachable:
            continue
        reachable.add(n)
        r = by_name[n]
        todo.extend(r['type_dependencies'] + r['value_dependencies'] + r['kernel_dependencies'])
    assert reachable == by_name.keys(), 'Export contains declarations outside the claimed closure'

    # Independently reproduce Lean.Util.CollectAxioms with a reverse fixed point.
    # The same equations apply, including stopping at axioms/quotient primitives.
    # This shares work across branches and also handles cycles correctly.
    users, found = defaultdict(set), {n: set() for n in by_name}
    pending = deque()
    for n, r in by_name.items():
        if r['kind'] == 'axiom':
            found[n].add(n)
            pending.append(n)
            continue
        if r['kind'] == 'quotient_primitive':
            continue
        dependencies = list(r['type_dependencies'])
        if r['kind'] in ('theorem', 'definition', 'opaque'):
            dependencies.extend(r['value_dependencies'])
        if r['kind'] == 'inductive':
            dependencies.extend(r['kernel_metadata']['constructors'])
        for dependency in dependencies:
            users[dependency].add(n)
    while pending:
        dependency = pending.popleft()
        for n in users[dependency]:
            additional = found[dependency] - found[n]
            if additional:
                found[n].update(additional)
                pending.append(n)
    for name, record in by_name.items():
        assert found[name] == set(record['axioms']), f'Axiom audit failed: {name}'
    return {'unresolved_references': 0, 'admitted_proofs': 0, 'expression_reference_audit': 'passed',
            'independent_axiom_audit': 'passed', 'audited_declarations': len(records)}


def source_resolver(lock):
    packages = json.loads((MATHLIB / 'lake-manifest.json').read_text())['packages']
    origins = [(MATHLIB, lock['mathlib_repository'], lock['mathlib_commit'], '')]
    origins.append((LEAN / 'src/lean', 'https://github.com/leanprover/lean4', lock['lean_commit'], 'src/'))
    for p in packages:
        origins.append((MATHLIB / '.lake/packages' / p['name'], p['url'].removesuffix('.git'), p['rev'], ''))
    file_cache, headers = {}, {}

    def resolve(record):
        module = record['module']
        relative = module.replace('.', '/') + '.lean'
        for base, repository, revision, prefix in origins:
            path = base / relative
            if not path.is_file():
                continue
            key = str(path)
            if key not in file_cache:
                original = path.read_text()
                file_cache[key] = original.splitlines()
                header = re.match(r'\s*(/-.*?-\/)', original, re.S)
                headers[f'{repository}@{revision}:{prefix}{relative}'] = {
                    'url': f'{repository}/blob/{revision}/{prefix}{relative}',
                    'header': header.group(1) if header else 'No leading source comment; see repository license.'}
            lines = file_cache[key]
            rg = record['source_range']
            link = f'{repository}/blob/{revision}/{prefix}{relative}'
            snippet = None
            if rg:
                start, end = rg['start_line'], rg['end_line']
                assert 1 <= start <= end <= len(lines), f'Invalid source range: {record["name"]}'
                selected = lines[start - 1:end]
                selected[-1] = selected[-1][:rg['end_column']]
                selected[0] = selected[0][rg['start_column']:]
                snippet = '\n'.join(selected)
                link += f'#L{start}-L{end}'
            return {'repository': repository, 'revision': revision, 'path': prefix + relative,
                    'url': link, 'range': rg, 'snippet': snippet,
                    'range_note': 'Lean declaration range; generated declarations can share their parent’s range.'}
        return {'repository': None, 'revision': None, 'path': relative if module else None,
                'url': None, 'range': record['source_range'], 'snippet': None,
                'range_note': 'No matching source file was available; the exact kernel declaration is retained.'}
    resolve.headers = headers
    return resolve


def build():
    records = [json.loads(line) for line in (EXPORT / 'declarations.ndjson').read_text().splitlines()]
    expressions = [json.loads(line) for line in (EXPORT / 'expressions.ndjson').read_text().splitlines()]
    report = json.loads((EXPORT / 'export-report.json').read_text())
    lock = json.loads((ROOT / 'data/source-lock.json').read_text())
    checks = audit(records, expressions, report)
    resolve_source = source_resolver(lock)
    for r in records:
        r['source'] = resolve_source(r)
    annotate(records, CORPUS)
    by_name = {r['name']: r for r in records}
    OUT.mkdir(parents=True, exist_ok=True)
    nodes, edges = [], []
    details = [{} for _ in range(64)]
    source_count = 0

    # Propagate topics through recorded closure for discovery, not as proof edges.
    tags = {n: set() for n in by_name}
    branch_tags = {n: set() for n in by_name}
    for seed, (_, _, _, topics) in CURATED.items():
        todo, seen = [seed], set()
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            tags[n].update(topics)
            branch_tags[n].add(SEED_INFO[seed]['branch'])
            r = by_name[n]
            todo.extend(r['type_dependencies'] + r['value_dependencies'] + r['kernel_dependencies'])

    def edge(src, dst, relation, record=None, expression=None, editorial=False):
        evidence = {'kind': 'editorial', 'note': 'Manually curated topic association; not a formal implication.'} if editorial else {
            'kind': 'kernel_declaration', 'declaration': record['name'],
            'field': {'uses': 'value_expression', 'proves': 'value_expression', 'type_ref': 'type_expression',
                      'definition_ref': 'value_expression', 'kernel_requires': 'kernel_dependencies'}[relation],
            'expression': expression,
        }
        edges.append({'id': f'e{len(edges)}', 'from': src, 'to': dst, 'relation': relation, 'evidence': evidence})

    for r in records:
        name = r['name']
        shard = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) % len(details)
        r['used_in_branches'] = sorted(branch_tags[name])
        r['used_in_topics'] = sorted(tags[name])
        source_count += bool(r['source']['snippet'])
        details[shard][name] = r
        label, summary, formula, topics = CURATED.get(name, (name.split('.')[-1], '', '', sorted(tags[name])))
        if r['kind'] == 'axiom':
            label, summary = AXIOM_DESCRIPTIONS.get(name, (name, 'A foundational assumption in the pinned environment.'))
        technical = (r['generated'] is True or r['kind'] in ('constructor', 'recursor', 'quotient_primitive') or name.startswith('_')
                     or bool(re.search(r'(^|\.)(inst|_aux|match_|recOn|casesOn|brecOn)', name))
                     or r['module'].startswith('Init.') and r['kind'] != 'axiom'
                     and not (name in ('Nat', 'Int') or name.startswith(('Nat.', 'Int.'))))
        node = {**metadata_fields(r), 'id': name, 'name': name, 'kind': r['kind'], 'label': label, 'summary': summary,
                'formula': formula, 'topics': topics, 'seed': name in CURATED, 'technical': technical,
                'branches': sorted(branch_tags[name]), 'branch': SEED_INFO.get(name, {}).get('branch'),
                'notationNote': SEED_INFO.get(name, {}).get('notation_note', ''),
                'axioms': r['axioms'], 'detailShard': f'details/{shard:02d}.json',
                'module': r['module'], 'sourceId': f'{lock["snapshot_id"]}:{name}'}
        nodes.append(node)
        for dep in r['type_dependencies']:
            edge(name, dep, 'type_ref', r, r['type_expression'])
        if r['kind'] == 'theorem':
            proof = f'proof:{name}'
            nodes.append({**node, 'id': proof, 'name': proof, 'kind': 'proof', 'label': f'Proof of {label}',
                          'seed': False, 'target_candidate': False,
                          'statementId': name, 'sourceId': node['sourceId'] + ':proof'})
            edge(proof, name, 'proves', r, r['value_expression'])
            for dep in r['value_dependencies']:
                edge(proof, dep, 'uses', r, r['value_expression'])
        else:
            for dep in r['value_dependencies']:
                edge(name, dep, 'definition_ref', r, r['value_expression'])
        for dep in r['kernel_dependencies']:
            edge(name, dep, 'kernel_requires', r)

    for topic in TOPICS:
        cid = 'concept:' + topic['id']
        nodes.append({'id': cid, 'name': topic['label'], 'label': topic['label'], 'kind': 'concept',
                      'summary': topic['description'], 'topic': topic['id'], 'topics': [topic['id']],
                      'branch': topic['branch'], 'branches': [topic['branch']],
                      'seed': False, 'technical': False, 'axioms': []})
        for definition in topic['definitions']:
            assert definition in by_name, f'Unknown concept definition {definition}'
            edge(cid, definition, 'maps_to', editorial=True)
        for seed, (_, _, _, topics) in CURATED.items():
            if topic['id'] in topics:
                edge(cid, seed, 'about_result', editorial=True)

    node_ids = {n['id'] for n in nodes}
    assert len(node_ids) == len(nodes)
    assert all(e['from'] in node_ids and e['to'] in node_ids and e['evidence'] for e in edges)
    checks['edge_evidence_audit'] = 'passed'
    branch_stats = [{**branch, 'seed_count': sum(s['branch'] == branch['id'] for s in CORPUS['seeds']),
                     'declaration_count': sum(branch['id'] in branch_tags[n] for n in by_name)} for branch in BRANCHES]
    receipt = {**report, **checks, 'generated_at': datetime.now(timezone.utc).isoformat(),
               'training_metadata': {'main_benchmark_eligible_count': 0,
                   'source_candidate_count': sum(r['target_candidate'] for r in records),
                   'generated_name_candidates': sum(r['generated'] is True for r in records),
                   'subject_labelled_count': sum(bool(r['subject_labels']) for r in records),
                   'note': 'Conservative screening only. The replay pilot is an engineering demonstration, not held-out evaluation.'},
               'edition': CORPUS['edition'], 'title': CORPUS['title'], 'branch_count': len(BRANCHES), 'branches': branch_stats,
               'shared_declaration_count': sum(len(bs) > 1 for bs in branch_tags.values()),
               'input_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in [EXPORT / 'declarations.ndjson', EXPORT / 'expressions.ndjson',
                                          ROOT / 'formal/AtlasExport.lean', ROOT / 'scripts/build_graph.py',
                                          ROOT / 'data/source-lock.json', ROOT / 'data/corpus.json',
                                          ROOT / 'scripts/training_metadata.py']},
               'snapshot': lock, 'node_count': len(nodes), 'edge_count': len(edges),
               'proof_count': sum(r['kind'] == 'theorem' for r in records), 'concept_count': len(TOPICS),
               'source_snippet_count': source_count, 'declaration_kinds': dict(Counter(r['kind'] for r in records)),
               'axioms': [r['name'] for r in records if r['kind'] == 'axiom'],
               'limitations': [
                   f'Complete dependency closure of {len(CURATED)} selected results across {len(BRANCHES)} branches, not a complete map of mathematics or mathlib.',
                   'Common declarations have one canonical node reused across branches. Branch membership records use in a selected result’s closure.',
                   'Dependencies record these particular formal declarations and proofs. They are not claims of logically minimal assumptions.',
                   'Lean’s dependent type theory and primitive kernel rules are part of the trusted foundation, beyond named axiom nodes.',
                   'Kernel rechecking is relative to admitted foundational axioms; it does not establish their consistency.',
                   'Concept links and plain-language paraphrases are editorial. Exact Lean statements are authoritative.',
                   'Source proofs can use tactics and notation; exact elaborated proof expressions are included in the dataset.',
                   'The neighborhood view is intentionally bounded. The downloadable dataset contains the entire extracted closure.',
               ]}
    expression_bytes = (EXPORT / 'expressions.ndjson').read_bytes()
    (OUT / 'expressions.ndjson.gz').write_bytes(gzip.compress(expression_bytes, compresslevel=9, mtime=0))
    expression_table = {'path': 'expressions.ndjson.gz', 'archive_path': 'raw/expressions.ndjson',
        'encoding': 'gzip; NDJSON arrays; zero-based line index is expression ID',
        'count': len(expressions), 'schema': 'SCHEMA.md#exact-expressions',
        'sha256_uncompressed': hashlib.sha256(expression_bytes).hexdigest(),
        'sha256_gzip': hashlib.sha256((OUT / 'expressions.ndjson.gz').read_bytes()).hexdigest()}
    atlas = {'schemaVersion': '1.3.0', 'metadata': receipt, 'expression_table': expression_table,
             'seeds': list(CURATED), 'branches': branch_stats,
             'topics': [{k: v for k, v in t.items() if k != 'definitions'} for t in TOPICS],
             'nodes': nodes, 'edges': edges, 'declarations': by_name}
    payload = (json.dumps(atlas, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    (OUT / 'atlas.json.gz').write_bytes(gzip.compress(payload, compresslevel=9, mtime=0))
    (OUT / 'atlas.json').unlink(missing_ok=True)
    dump(OUT / 'verification.json', receipt)
    dump(OUT / 'source-lock.json', lock)
    dump(OUT / 'training-manifest.json', {'schema_version': '1.0.0', 'snapshot_id': lock['snapshot_id'],
        'graph': {'path': 'atlas.json.gz', 'sha256': hashlib.sha256((OUT / 'atlas.json.gz').read_bytes()).hexdigest()},
        'expression_table': expression_table, 'complete_dataset': 'mathematics-atlas-dataset.zip',
        'replay_pilot': 'mathematics-atlas-replay-pilot.zip',
        'main_benchmark_ready': False,
        'warning': 'Raw graph contains answers and proof-derived features. Do not index it for held-out evaluation. Use independently reconstructed source environments and sanitize before feature computation.'})
    shutil.copyfile(ROOT / 'data/corpus.json', OUT / 'corpus.json')
    dump(OUT / 'UPSTREAM_HEADERS.json', resolve_source.headers)
    for name in ['SCHEMA.md', 'LICENSE', 'NOTICE.md']:
        shutil.copyfile(ROOT / name, OUT / name)
    for i, shard in enumerate(details):
        dump(OUT / 'details' / f'{i:02d}.json', shard)
    dump(ROOT / 'data/verification.json', receipt)
    with zipfile.ZipFile(OUT / 'mathematics-atlas-dataset.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(EXPORT.glob('*')):
            if path.is_file():
                z.write(path, 'raw/' + path.name)
        for path in [OUT / 'atlas.json.gz', OUT / 'verification.json', OUT / 'source-lock.json', OUT / 'corpus.json', OUT / 'UPSTREAM_HEADERS.json', OUT / 'training-manifest.json']:
            z.write(path, path.name)
        # Full enriched records already appear in atlas.json.gz. Keep shards
        # available on the site, but omit this duplicate copy from the ZIP.
        for name in ['SCHEMA.md', 'LICENSE', 'NOTICE.md']:
            path = ROOT / name
            if path.exists():
                z.write(path, name)
    for path in [OUT / 'atlas.json.gz', OUT / 'mathematics-atlas-dataset.zip', OUT / 'expressions.ndjson.gz']:
        assert path.stat().st_size < 25 * 1024 * 1024, f'Hosting asset limit exceeded: {path.name}'
    print(json.dumps({k: receipt[k] for k in ['declaration_count', 'proof_count', 'node_count', 'edge_count', 'expression_count', 'axioms', 'source_snippet_count', 'kernel_replay', 'independent_axiom_audit']}, indent=2))


if __name__ == '__main__':
    build()
