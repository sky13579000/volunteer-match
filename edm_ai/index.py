from __future__ import annotations

import math
from collections import defaultdict

from .models import Chunk, Document, Principal
from .text import tokenize
from .vectors import cosine, dot, fuzzy_vec

K1, B = 1.5, 0.75
ENTITY_BOOST = 1.5
RRF_K = 60


class Index:
    """Hybrid retriever: BM25 (+entity boost) fused with vector similarity by Reciprocal Rank Fusion.

    Vectors are dense (external embedder) when supplied, else hashed-trigram fuzzy vectors.
    Security trimming happens BEFORE any scoring: statistics, candidates and similarities only ever
    involve chunks the caller may read, so forbidden documents cannot influence ranking or leak via scores."""

    def __init__(self, docs: dict[int, Document], chunks: list[Chunk], dense: list[list[float]] | None = None,
                 fuzzy_min: float = 0.35, dense_min: float = 0.55, min_coverage: float = 0.2):
        self.docs, self.chunks, self.dense = docs, chunks, dense
        self.fuzzy_min, self.dense_min, self.min_coverage = fuzzy_min, dense_min, min_coverage
        self.token_sets: list[frozenset] = []
        self.postings: dict[str, dict[int, int]] = defaultdict(dict)
        self.lengths: list[int] = []
        self.by_entity: dict[str, set[int]] = defaultdict(set)
        self.fuzzy = [fuzzy_vec(c.text) for c in chunks]
        for i, c in enumerate(chunks):
            toks = tokenize(c.text)
            self.token_sets.append(frozenset(toks))
            self.lengths.append(len(toks) or 1)
            for t in toks:
                self.postings[t][i] = self.postings[t].get(i, 0) + 1
            for e in c.entities:
                self.by_entity[e].add(i)

    def allowed(self, p: Principal) -> set[int]:
        ok = {d for d, doc in self.docs.items() if doc.acl.allows(p)}
        return {i for i, c in enumerate(self.chunks) if c.doc_id in ok}

    def _bm25(self, allowed: set[int], query: str, entities: frozenset) -> dict[int, float]:
        n = len(allowed)
        avgdl = sum(self.lengths[i] for i in allowed) / n
        scores: dict[int, float] = defaultdict(float)
        for t in set(tokenize(query)):
            post = self.postings.get(t)
            hit = [i for i in post if i in allowed] if post else []
            if not hit:
                continue
            idf = math.log(1 + (n - len(hit) + 0.5) / (len(hit) + 0.5))
            for i in hit:
                tf = post[i]
                scores[i] += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * self.lengths[i] / avgdl))
        for e in entities:
            for i in self.by_entity.get(e, ()):
                if i in allowed:
                    scores[i] += ENTITY_BOOST
        return scores

    def search(self, p: Principal, query: str, entities: frozenset, k: int = 5, rel_cutoff: float = 0.3,
               per_doc: int = 2, qvec: list[float] | None = None) -> list[tuple[Chunk, float]]:
        allowed = self.allowed(p)
        if not allowed:
            return []
        bm = self._bm25(allowed, query, entities)
        if self.dense is not None and qvec is not None:
            sims = {i: dot(qvec, self.dense[i]) for i in allowed}
            vmin = self.dense_min
        else:
            qv = fuzzy_vec(query)
            sims = {i: cosine(qv, self.fuzzy[i]) for i in allowed}
            vmin = self.fuzzy_min
        # Relevance gate. A relative cut-off alone can never reject the best of a poor candidate set (e.g. a user
        # who may read a single unrelated document), so lexical candidates must also cover enough of the question.
        qt = set(tokenize(query))
        top_bm = max(bm.values(), default=0.0)
        entity_hit = {i for e in entities for i in self.by_entity.get(e, ()) if i in allowed}
        cand = {i for i, s in bm.items() if s >= top_bm * rel_cutoff and
                (i in entity_hit or (qt and len(qt & self.token_sets[i]) / len(qt) >= self.min_coverage))}
        cand |= {i for i, s in sims.items() if s >= vmin}
        if not cand:
            return []
        fused: dict[int, float] = defaultdict(float)
        for ranking in (sorted((i for i in cand if i in bm), key=lambda i: -bm[i]),
                        sorted(cand, key=lambda i: -sims[i])):
            for r, i in enumerate(ranking):
                fused[i] += 1.0 / (RRF_K + r + 1)
        out, seen = [], defaultdict(int)
        for i, s in sorted(fused.items(), key=lambda x: -x[1]):
            c = self.chunks[i]
            if seen[c.doc_id] >= per_doc:
                continue
            seen[c.doc_id] += 1
            out.append((c, s))
            if len(out) >= k:
                break
        return out
