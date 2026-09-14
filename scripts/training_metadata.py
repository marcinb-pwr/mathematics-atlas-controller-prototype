"""Conservative metadata for dataset consumers; no inferred label is reviewed truth."""
import hashlib
import re
from collections import defaultdict


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def annotate(records, corpus):
    by_name = {r['name']: r for r in records}
    curated = {s['name']: s for s in corpus['seeds']}
    subjects = defaultdict(set)
    for seed in corpus['seeds']:
        subjects[seed['name']].update(seed['topics'])
    for topic in corpus['topics']:
        for name in topic['definitions']:
            subjects[name].add(topic['id'])
    # One explicitly reviewed counterexample from the dataset review.
    subjects['Nat.add_comm'] = {'arithmetic', 'addition'}
    parents = {n: n for n in by_name}

    def root(n):
        while parents[n] != n:
            parents[n] = parents[parents[n]]
            n = parents[n]
        return n

    def join(a, b):
        a, b = root(a), root(b)
        if a != b:
            parents[max(a, b)] = min(a, b)

    generated, seen_statements, seen_ranges = {}, {}, {}
    for r in records:
        n = r['name']
        match = re.search(r'(?:\._proof_\d+|\._?match_\d+|\._aux\w*)(?:\.|$)', n)
        parent = n[:match.start()] if match else None
        parent = parent if parent in by_name else None
        generated[n] = (True if match else False if n in curated else None, parent)
        if parent:
            join(n, parent)
        if r['kind'] == 'theorem':
            statement = r['statement']
            if statement in seen_statements:
                join(n, seen_statements[statement])
            seen_statements[statement] = n
        location = r.get('source_range')
        if location:
            key = (r['module'], tuple(sorted(location.items())))
            if key in seen_ranges:
                join(n, seen_ranges[key])
            seen_ranges[key] = n

    families = defaultdict(list)
    for n in by_name:
        families[root(n)].append(n)
    family_ids = {r: 'family:' + digest('\n'.join(sorted(ns)))[:24] for r, ns in families.items()}
    for r in records:
        n = r['name']
        generated_flag, parent = generated[n]
        source = r.get('source', {}).get('snippet') or ''
        candidate = (r['kind'] == 'theorem' and generated_flag is not True
                     and not n.startswith('_') and bool(re.search(r'\b(theorem|lemma)\b', source)))
        labels = sorted(subjects[n])
        r.update(
            subject_labels=labels,
            label_provenance={
                'subject_labels': {'method': 'statement_review' if n == 'Nat.add_comm' else
                                   'existing_editorial_mapping' if labels else 'unassigned',
                                   'source': 'dataset review' if n == 'Nat.add_comm' else
                                   'data/corpus.json' if labels else None,
                                   'review_status': 'reviewed' if n == 'Nat.add_comm' else
                                   'editorial_unreviewed' if labels else 'unassigned'},
                'used_in_branches': {'method': 'selected_result_dependency_closure',
                                     'source': 'data/corpus.json', 'review_status': 'derived'},
                'used_in_topics': {'method': 'selected_result_dependency_closure',
                                   'source': 'data/corpus.json', 'review_status': 'derived'}},
            generated=generated_flag, generated_from=parent,
            generation_provenance={'method': 'generated_name_pattern' if generated_flag else
                                   'curated_entry_point' if generated_flag is False else 'unknown',
                                   'review_status': 'heuristic' if generated_flag else
                                   'editorial' if generated_flag is False else 'unreviewed'},
            target_candidate=candidate,
            eligible_as_target=False,
            target_eligibility={'status': 'pending_environment_and_family_review' if candidate else 'excluded',
                                'scope': 'main_benchmark',
                                'reason': 'A source snippet is a screening signal, not benchmark approval.' if candidate else
                                          'Not a human-facing theorem candidate with a source declaration.'},
            statement_text_sha256=digest(r['statement']),
            split_family_id=family_ids[root(n)],
            family_provenance='conservative union of identical printed statements, identical source ranges, and inferred generated parents; not semantic equivalence')


def metadata_fields(record):
    # Full provenance, hashes and screening reasons live in declarations.
    # Keep the browser index compact instead of duplicating these long records.
    return {k: record[k] for k in ('used_in_branches', 'used_in_topics', 'subject_labels',
        'generated', 'generated_from', 'target_candidate', 'eligible_as_target', 'split_family_id')}
