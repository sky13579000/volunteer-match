from __future__ import annotations

from .entities import EntityExtractor
from .gateway import SqlGateway
from .index import Index
from .llm import citations_valid
from .models import Answer, Evidence, Principal
from .safety import redact_pii, scrub_injection
from .store import DocumentStore
from .text import sentences, tokenize

REFUSAL = "접근 가능한 자료에서 근거를 찾지 못했습니다. 질문을 구체화하거나 담당자에게 자료 접근 권한을 요청해 주세요."


def _num(v) -> str:
    return f"{int(v):,}" if float(v) == int(v) else f"{v:,.2f}"


class Engine:
    """Question -> permission-trimmed retrieval over documents -> entity hop into structured DB
    -> numbered evidence -> cited answer (LLM optional, validated) -> audit record."""

    def __init__(self, store: DocumentStore, gateway: SqlGateway | None, extractor: EntityExtractor,
                 llm=None, expand_top: int = 2, embedder=None, pii_reveal_roles=(), min_coverage: float = 0.2):
        self.store, self.gateway, self.extractor, self.llm = store, gateway, extractor, llm
        self.expand_top, self.embedder, self.min_coverage = expand_top, embedder, min_coverage
        self.pii_reveal_roles = frozenset(pii_reveal_roles)
        self.reload()

    def reload(self):
        docs, chunks = self.store.load()
        dense = None
        if self.embedder:
            try:
                cache = self.store.get_embeddings(self.embedder.model)
                missing = [c for c in chunks if c.id not in cache]
                if missing:
                    vecs = self.embedder.embed([c.text for c in missing])
                    self.store.put_embeddings(self.embedder.model, {c.id: v for c, v in zip(missing, vecs)})
                    cache.update({c.id: v for c, v in zip(missing, vecs)})
                dense = [cache[c.id] for c in chunks]
            except Exception:
                dense = None  # embedder down: fall back to fuzzy vectors rather than failing
        self.index = Index(docs, chunks, dense, min_coverage=self.min_coverage)

    def ask(self, p: Principal, question: str, k: int = 5) -> Answer:
        q_ents = self.extractor.extract(question)
        qvec = None
        if self.embedder and self.index.dense is not None:
            try:
                qvec = self.embedder.embed([question])[0]
            except Exception:
                qvec = None
        hits = self.index.search(p, question, q_ents, k=k, qvec=qvec)

        # Hop: entities found in the best documents become structured-DB lookups.
        hop = set(q_ents)
        if hits:
            best = hits[0][1]
            for c, s in hits[: self.expand_top]:
                if s >= best * 0.5:
                    hop |= c.entities
        link_types = self.gateway.link_types() if self.gateway else set()
        hop = {e for e in hop if e.partition(":")[0] in link_types}
        rows = self.gateway.lookup(p, hop) if self.gateway and hop else []

        evidence: list[Evidence] = []
        for c, s in hits:
            doc = self.index.docs[c.doc_id]
            text = c.text if self.pii_reveal_roles & p.roles else redact_pii(c.text)
            evidence.append(Evidence(f"E{len(evidence) + 1}", "doc", doc.title, text, f"{doc.source}#{c.ord}", s))
        for r in rows:
            body = "; ".join(f"{r['labels'][c]}={v}" for c, v in r["values"].items())
            evidence.append(Evidence(f"E{len(evidence) + 1}", "row", r["title"], body,
                                     f"{r['table']}:{r['key']}", masked=r["masked"]))

        if self.gateway:
            for m in self.gateway.metrics_for(question):
                agg = self.gateway.aggregate(p, m, q_ents)  # scope = what the user asked, not doc-hop guesses
                if agg:
                    grp = agg["group_by"]
                    body = "; ".join((f"{r['g']}: " if grp else "") + _num(r["v"]) + f" ({r['n']}건)"
                                     for r in agg["rows"])
                    evidence.append(Evidence(f"E{len(evidence) + 1}", "metric", agg["label"], body, f"metric:{agg['metric']}"))

        if not evidence:
            ans = Answer(REFUSAL, [], refused=True)
        else:
            ans = self._compose(question, evidence)
        self.store.log_audit(p.id, question, [e.ref for e in evidence], ans.refused, ans.generator)
        return ans

    def _compose(self, question: str, evidence: list[Evidence]) -> Answer:
        if self.llm:
            block = "\n\n".join(f"[{e.id}] ({e.title}) {scrub_injection(e.text)[0]}" for e in evidence)
            try:
                text = self.llm.complete(question, block)
                if citations_valid(text, len(evidence)):
                    return Answer(text, evidence, generator="llm")
            except Exception:  # network/model failure must never break the answer path
                pass
        return Answer(self._extractive(question, evidence), evidence)

    @staticmethod
    def _extractive(question: str, evidence: list[Evidence]) -> str:
        """Deterministic, no-LLM answer: best-matching sentences / DB rows, each tagged with its source."""
        qt = set(tokenize(question))
        lines = []
        for e in evidence:
            if e.kind == "metric":
                lines.append(f"- {e.title}: {e.text} [{e.id}]")
                continue
            if e.kind == "row":
                note = f" (권한 없음: {', '.join(e.masked)})" if e.masked else ""
                lines.append(f"- [{e.title}] {e.text}{note} [{e.id}]")
                continue
            ranked = sorted(sentences(e.text), key=lambda s: -len(qt & set(tokenize(s))))
            pick = [s for s in ranked[:2] if qt & set(tokenize(s))] or ranked[:1]
            lines.append(f"- {' '.join(pick)} [{e.id}]")
        return "관련 근거를 정리하면 다음과 같습니다.\n" + "\n".join(lines)
