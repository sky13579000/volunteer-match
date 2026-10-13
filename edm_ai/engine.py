from __future__ import annotations

from .entities import EntityExtractor
from .gateway import SqlGateway
from .index import Index
from .llm import citations_valid
from .models import Answer, Evidence, Principal
from .store import DocumentStore
from .text import sentences, tokenize

REFUSAL = "접근 가능한 자료에서 근거를 찾지 못했습니다. 질문을 구체화하거나 담당자에게 자료 접근 권한을 요청해 주세요."


class Engine:
    """Question -> permission-trimmed retrieval over documents -> entity hop into structured DB
    -> numbered evidence -> cited answer (LLM optional, validated) -> audit record."""

    def __init__(self, store: DocumentStore, gateway: SqlGateway | None, extractor: EntityExtractor,
                 llm=None, expand_top: int = 2):
        self.store, self.gateway, self.extractor, self.llm = store, gateway, extractor, llm
        self.expand_top = expand_top
        self.reload()

    def reload(self):
        self.index = Index(*self.store.load())

    def ask(self, p: Principal, question: str, k: int = 5) -> Answer:
        q_ents = self.extractor.extract(question)
        hits = self.index.search(p, question, q_ents, k=k)

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
            evidence.append(Evidence(f"E{len(evidence) + 1}", "doc", doc.title, c.text, f"{doc.source}#{c.ord}", s))
        for r in rows:
            body = "; ".join(f"{r['labels'][c]}={v}" for c, v in r["values"].items())
            evidence.append(Evidence(f"E{len(evidence) + 1}", "row", r["title"], body,
                                     f"{r['table']}:{r['key']}", masked=r["masked"]))

        if not evidence:
            ans = Answer(REFUSAL, [], refused=True)
        else:
            ans = self._compose(question, evidence)
        self.store.log_audit(p.id, question, [e.ref for e in evidence], ans.refused, ans.generator)
        return ans

    def _compose(self, question: str, evidence: list[Evidence]) -> Answer:
        if self.llm:
            block = "\n\n".join(f"[{e.id}] ({e.title}) {e.text}" for e in evidence)
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
            if e.kind == "row":
                note = f" (권한 없음: {', '.join(e.masked)})" if e.masked else ""
                lines.append(f"- [{e.title}] {e.text}{note} [{e.id}]")
                continue
            ranked = sorted(sentences(e.text), key=lambda s: -len(qt & set(tokenize(s))))
            pick = [s for s in ranked[:2] if qt & set(tokenize(s))] or ranked[:1]
            lines.append(f"- {' '.join(pick)} [{e.id}]")
        return "관련 근거를 정리하면 다음과 같습니다.\n" + "\n".join(lines)
