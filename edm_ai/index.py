from __future__ import annotations

import math
from collections import defaultdict

from .models import Chunk, Document, Principal
from .text import tokenize

K1, B = 1.5, 0.75
ENTITY_BOOST = 1.5


class Index:
    """In-memory BM25 over chunks.

    Security trimming happens BEFORE scoring: IDF/length statistics and the candidate set only ever
    include chunks the caller may read, so forbidden documents cannot influence ranking or leak via scores."""

    def __init__(self, docs: dict[int, Document], chunks: list[Chunk]):
        self.docs, self.chunks = docs, chunks
        self.postings: dict[str, dict[int, int]] = defaultdict(dict)
        self.lengths: list[int] = []
        self.by_entity: dict[str, set[int]] = defaultdict(set)
        for i, c in enumerate(chunks):
            toks = tokenize(c.text)
            self.lengths.append(len(toks) or 1)
            for t in toks:
                self.postings[t][i] = self.postings[t].get(i, 0) + 1
            for e in c.entities:
                self.by_entity[e].add(i)

    def allowed(self, p: Principal) -> set[int]:
        ok = {d for d, doc in self.docs.items() if doc.acl.allows(p)}
        return {i for i, c in enumerate(self.chunks) if c.doc_id in ok}

    def search(self, p: Principal, query: str, entities: frozenset, k: int = 5,
               rel_cutoff: float = 0.3, per_doc: int = 2) -> list[tuple[Chunk, float]]:
        allowed = self.allowed(p)
        if not allowed:
            return []
        n = len(allowed)
        avgdl = sum(self.lengths[i] for i in allowed) / n
        scores: dict[int, float] = defaultdict(float)
        for t in set(tokenize(query)):
            post = self.postings.get(t)
            if not post:
                continue
            hit = [i for i in post if i in allowed]
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
        if not scores:
            return []
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        top = ranked[0][1]
        out, seen = [], defaultdict(int)
        for i, s in ranked:
            c = self.chunks[i]
            if s < top * rel_cutoff or seen[c.doc_id] >= per_doc:
                continue
            seen[c.doc_id] += 1
            out.append((c, s))
            if len(out) >= k:
                break
        return out
