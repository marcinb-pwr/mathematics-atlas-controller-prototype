"""Deterministic, statement-only retrieval shared by every controller client."""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re

from premise_cache import inventory_rows

VERSION = 'available-premises-bm25/1'
SOURCE_BYTES = Path(__file__).read_bytes()
STOP = {'type', 'prop', 'sort', 'inst', 'forall', 'fun', 'let', 'true', 'iff', 'eq'}
SYMBOLS = {'ℕ': ' nat ', 'ℤ': ' int ', '∣': ' dvd ', '∪': ' union ', '∩': ' inter ',
           '∈': ' mem ', '∉': ' not mem ', '⊆': ' subset ', '∅': ' empty ',
           '≠': ' ne ', '≤': ' le ', '≥': ' ge ', '<': ' lt ', '>': ' gt ',
           '¬': ' not ', '0': ' zero ', '1': ' one ', '2': ' two '}


def tokens(text):
    for symbol, replacement in SYMBOLS.items():
        text = text.replace(symbol, replacement)
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    words = re.findall(r'[^\W_]+', text.lower())
    return [word for word in words if len(word) > 1 and word not in STOP]


class PremiseIndex:
    def __init__(self, path):
        self.path = Path(path)
        self.inventory_sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.rows = list(inventory_rows(path))
        self.by_name = {row['name']: row for row in self.rows}
        if len(self.by_name) != len(self.rows):
            raise ValueError('Duplicate premise identities')
        self.postings = defaultdict(list)
        self.statement_links = defaultdict(list)
        self.lengths = []
        for index, row in enumerate(self.rows):
            if set(row) != {'name', 'kind', 'statement'}:
                raise ValueError('Retrieval accepts statement-only premise records')
            counts = Counter(tokens(row['statement'])) + Counter(tokens(row['name']) * 2)
            self.lengths.append(sum(counts.values()))
            for token, count in counts.items():
                self.postings[token].append((index, count))
            # A retrieval association, not an unconditional mathematical
            # equivalence claim: retain the source theorem for every edge.
            pattern = r'([A-Za-z][\w.]*)\s+([a-z][A-Za-z0-9_]*)\s+↔\s+([A-Za-z][\w.]*)\s+\2(?:\s|$|\))'
            for left, _, right in re.findall(pattern, row['statement']):
                for a, b in [(left, right), (right, left)]:
                    a, b = a.rsplit('.', 1)[-1].lower(), b.rsplit('.', 1)[-1].lower()
                    if a != b and len(a) > 2 and len(b) > 2:
                        self.statement_links[a].append((b, row['name']))
        self.average = sum(self.lengths) / max(1, len(self.rows))

    def retrieve(self, observation, limit=12):
        if not observation['goals']:
            return []
        goal = observation['goals'][0]
        query = Counter(tokens(goal['target']) * 3)
        for local in goal['hypotheses']:
            query.update(tokens(local['type']))
        # Generic equality extension hint, independent of any target proof/name.
        if ' = ' in goal['target']:
            query['ext'] += 2
        expanded = {}
        for token, weight in list(query.items()):
            for neighbor, source in self.statement_links.get(token, []):
                if neighbor not in query:
                    expanded.setdefault(neighbor, (min(weight, 3), source))
        for neighbor, (weight, _) in expanded.items():
            query[neighbor] += weight
        scores = defaultdict(float)
        size = len(self.rows)
        for token, weight in query.items():
            postings = self.postings.get(token, [])
            inverse = math.log(1 + (size - len(postings) + 0.5) / (len(postings) + 0.5))
            for index, frequency in postings:
                denominator = frequency + 1.2 * (0.25 + 0.75 * self.lengths[index] / max(1, self.average))
                scores[index] += weight * inverse * frequency * 2.2 / denominator
        selected = [index for index in sorted(scores, key=lambda index: (-scores[index], len(self.rows[index]['statement']), self.rows[index]['name']))
                    if not self.rows[index]['name'].startswith('_private.') and '_proof_' not in self.rows[index]['name']][:limit]
        result = [{'name': self.rows[index]['name'], 'statement': self.rows[index]['statement'],
                   'score': round(scores[index], 8), 'reason': 'bm25_with_statement_associations'} for index in selected]
        if ' = ' in goal['target']:
            for local in goal['hypotheses']:
                head = re.match(r'([A-Za-z][\w.]*)\b', local['type'])
                name = head.group(1) + '.ext' if head else None
                if name in self.by_name and name not in {row['name'] for row in result if row['reason'] == 'equality_extensionality_name_heuristic'}:
                    result = [row for row in result if row['name'] != name]
                    result.insert(0, {'name': name, 'statement': self.by_name[name]['statement'],
                                      'score': None, 'reason': 'equality_extensionality_name_heuristic'})
        self.last_query_expansions = [{'token': token, 'source_theorem': source} for token, (_, source) in sorted(expanded.items())]
        return result[:limit]

    def metadata(self):
        return {'name': VERSION, 'inventory_sha256': self.inventory_sha256,
                'source_sha256': hashlib.sha256(SOURCE_BYTES).hexdigest(),
                'score_tiebreak': 'shorter_printed_statement_then_name',
                'fields_used': ['name', 'statement'], 'uses_target_proof': False,
                'uses_proof_dependency_edges': False, 'theorem_count': len(self.rows)}


def candidate_actions(observation, retrieved):
    """Apply/exact/rewrite proposals, all checked against one environment index."""
    if not observation['goals']:
        return []
    goal = observation['goals'][0]
    locals_ = [local for local in goal['hypotheses'] if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_\u0027]*', local['name'])]
    names = {local['name'] for local in locals_}
    fresh = next('h_atlas_' + str(i) for i in range(len(names) + 1) if 'h_atlas_' + str(i) not in names)
    proposals = [('assumption', None), ('rfl', None), (f'intro {fresh}', None)]
    for local in locals_:
        proposals.append((f"exact {local['name']}", None))
        if ' = ' in local['type'] or '↔' in local['type']:
            proposals.extend([(f"rw [{local['name']}]", None), (f"rw [← {local['name']}]", None)])
    for lemma in retrieved:
        if (' = ' in lemma['statement'] or '↔' in lemma['statement']) and lemma['reason'] != 'equality_extensionality_name_heuristic':
            proposals.append((f"rw [{lemma['name']}]", lemma['name']))
        proposals.extend([(f"apply {lemma['name']}", lemma['name']), (f"exact {lemma['name']}", lemma['name'])])
        if '↔' in lemma['statement']:
            proposals.extend([(f"apply {lemma['name']}.mp", lemma['name']), (f"apply {lemma['name']}.mpr", lemma['name'])])
    for lemma in retrieved:
        if ' = ' in lemma['statement'] or '↔' in lemma['statement']:
            proposals.extend([(f"rw [{lemma['name']}]", lemma['name']), (f"rw [← {lemma['name']}]", lemma['name'])])
    proposals.append(('constructor', None))
    seen, result = set(), []
    for action, premise in proposals:
        if action not in seen:
            result.append({'id': f'c{len(result):03d}', 'action': action, 'premise': premise})
            seen.add(action)
    return result


class CandidatePipeline:
    def __init__(self, inventory, top_k=12):
        self.index = PremiseIndex(inventory)
        self.top_k = top_k

    def propose(self, session):
        observation = session.observe()
        with session.measure('premise_retrieval'):
            retrieved = self.index.retrieve(observation, self.top_k)
        with session.measure('candidate_generation'):
            candidates = candidate_actions(observation, retrieved)
        metadata = {**self.index.metadata(), 'candidate_version': 'apply-exact-rewrite/1', 'top_k': self.top_k}
        session._record({'kind': 'candidate_proposals', 'state_id': observation['state_id'],
                         'event_index': len(session.history), 'observation': observation,
                         'generator': metadata, 'query_expansions': self.index.last_query_expansions,
                         'retrieved_premises': retrieved, 'candidates': candidates})
        return candidates, metadata
