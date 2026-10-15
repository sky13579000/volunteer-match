from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import automation
from .dms import now, today
from .errors import DmsError

CLAUSES = [
    ("payment", "대금 지급 조건", r"대금|지급\s*(조건|기한|시기)|지급한다|청구"),
    ("term", "계약 기간", r"계약\s*기간|계약\s*(체결일|종료일)|유효\s*기간"),
    ("delay", "지체상금(지연 배상)", r"지체\s*상금|지연\s*(배상|손해)|지체\s*일수"),
    ("terminate", "계약 해지·해제", r"해지|해제"),
    ("damages", "손해배상", r"손해\s*배상|배상\s*책임"),
    ("warranty", "하자 보증", r"하자|보증\s*(기간|책임)"),
    ("secrecy", "비밀 유지", r"비밀\s*(유지|준수)|기밀|누설"),
    ("privacy", "개인정보 보호", r"개인\s*정보"),
    ("dispute", "분쟁 해결·관할", r"분쟁|관할\s*법원|중재|소송"),
    ("force", "불가항력", r"불가항력|천재지변"),
]
TYPE_WORDS = {"계약": "CT", "계약서": "CT"}
STATUS_WORDS = {"작성 중": "draft", "작성중": "draft", "결재 중": "in_review", "결재중": "in_review", "승인": "approved", "승인된": "approved", "반려": "rejected", "만료": "expired"}


class InsightMixin:
    """Rule-based helpers that make the AI features trustworthy and explainable. Nothing here calls an LLM, and none of it widens access:
    every function reads only what the caller may read."""

    # =============== AI trust center ===============
    def trust_run(self, p, by=None) -> dict:
        if p is not None and not self.admin(p):
            raise DmsError(403, "관리자만 실행할 수 있습니다")
        g = (self.app.cfg.get("eval") or {}).get("golden")
        if not g:
            raise DmsError(400, "평가 세트가 설정되지 않았습니다 (설정 eval.golden)")
        path = Path(self.app._p(g))
        if not path.is_file():
            raise DmsError(404, f"평가 세트 파일을 찾을 수 없습니다: {g}")
        from .evaluate import run_eval
        r = run_eval(self.app, path)
        who = by or (p.id if p else "system")
        self.x("INSERT INTO trust_runs(ts,by,cases,recall,mrr,leaks,failures,answers) VALUES(?,?,?,?,?,?,?,?)",
               (now(), who, r["cases"], r["recall"], r["mrr"], r["leaks"], json.dumps(r["failures"][:50], ensure_ascii=False), json.dumps(r["answers"])))
        if r["leaks"]:
            for uid, u in self.app.users.items():
                if self.admin(u):
                    self.notify(uid, "privacy_alert", None, f"AI 신뢰성 점검: 접근 제한 정보 노출 {r['leaks']}건 감지 — 즉시 확인하세요")
        self.store.log_audit(who, f"[AI 신뢰성 점검] 사례 {r['cases']}건, 노출 {r['leaks']}건, 재현율 {r['recall']:.2f}", [], False, "dms")
        return self.trust_status(p) if p else r

    def trust_status(self, p) -> dict:
        if not self.admin(p):
            raise DmsError(403, "관리자만 볼 수 있습니다")
        runs = self.q("SELECT * FROM trust_runs ORDER BY id DESC LIMIT 30")
        for r in runs:
            r["failures"] = json.loads(r["failures"] or "[]")
            r["answers"] = json.loads(r["answers"] or "{}")
        cfg = self.app.cfg.get("eval") or {}
        return {"configured": bool(cfg.get("golden")), "daily": bool(cfg.get("daily")), "runs": runs, "latest": runs[0] if runs else None,
                "policy": {"leaks_must_be_zero": True, "min_recall": float(cfg.get("min_recall", 0.8))}}

    def trust_maintenance(self):
        cfg = self.app.cfg.get("eval") or {}
        if not (cfg.get("golden") and cfg.get("daily")):
            return 0
        last = self.q1("SELECT ts FROM trust_runs ORDER BY id DESC LIMIT 1")
        if last and last["ts"][:10] == today():
            return 0
        try:
            self.trust_run(None, "system")
            return 1
        except DmsError:
            return 0

    # =============== natural-language search -> filters ===============
    def nl_parse(self, p, q: str) -> dict:
        q = str(q or "").strip()[:200]
        f: dict = {}
        why: list[str] = []
        rest = q
        t0 = datetime.now(timezone.utc).date()

        def take(pattern, fn):
            nonlocal rest
            m = re.search(pattern, rest)
            if m:
                fn(m)
                rest = (rest[:m.start()] + " " + rest[m.end():]).strip()
        take(r"내가\s*(올린|등록한|작성한)|내\s*문서", lambda m: (f.__setitem__("mine", True), why.append("내가 올린 문서")))
        take(r"최근\s*(\d{1,3})\s*일", lambda m: (f.__setitem__("date_from", (t0 - timedelta(days=int(m.group(1)))).isoformat()), why.append(f"최근 {m.group(1)}일")))
        take(r"이번\s*달", lambda m: (f.__setitem__("date_from", t0.replace(day=1).isoformat()), why.append("이번 달")))
        take(r"지난\s*달", lambda m: (f.__setitem__("date_from", (t0.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()), f.__setitem__("date_to", (t0.replace(day=1) - timedelta(days=1)).isoformat()), why.append("지난 달")))
        take(r"올해", lambda m: (f.__setitem__("date_from", f"{t0.year}-01-01"), why.append(f"{t0.year}년")))
        for w, code in sorted(STATUS_WORDS.items(), key=lambda x: -len(x[0])):
            if w in rest:
                f["status"] = code
                why.append(f"상태: {w}")
                rest = rest.replace(w, " ")
                break
        for t in self.q("SELECT code,name FROM doc_types WHERE active=1"):
            for w in sorted({t["name"], *(k for k, v in TYPE_WORDS.items() if v == t["code"])}, key=len, reverse=True):
                if w and w in rest:
                    f["type"] = t["code"]
                    why.append(f"유형: {t['name']}")
                    rest = rest.replace(w, " ", 1)
                    break
            if "type" in f:
                break
        for m in re.findall(r"#([^\s#]{1,30})", rest):
            f["tag"] = m
            why.append(f"태그: {m}")
            rest = rest.replace("#" + m, " ")
        for uid, u in self.app.users.items():
            if len(u.name) >= 2 and u.name in rest and self.can_see_user(p, uid):
                f["owner"] = uid
                why.append(f"작성자: {u.name}")
                rest = rest.replace(u.name, " ", 1)
                break
        rest = re.sub(r"(문서|파일|찾아줘|보여줘|검색|알려줘|에서|의|좀)\b", " ", rest)
        rest = re.sub(r"\s+", " ", rest).strip()
        return {"filters": f, "q": rest, "explain": why}

    def can_see_user(self, p, uid) -> bool:
        return uid in self.app.users

    def nl_search(self, p, q: str) -> dict:
        parsed = self.nl_parse(p, q)
        res = self.search(p, parsed["q"] or None, **parsed["filters"])
        return {**parsed, "result": res}

    # =============== contract clause checklist ===============
    def clause_check(self, p, doc_id) -> dict:
        row = self.get_row(p, doc_id, "read")
        vv = self.view_ver(p, row)
        text = self._ver(row, vv)["text"] if vv and row["origin"] == "managed" else ""
        if not text:
            raise DmsError(409, "점검할 본문이 없습니다")
        items = (self.app.cfg.get("clauses") or {}).get("items") or [{"key": k, "label": l, "pattern": pat} for k, l, pat in CLAUSES]
        out = []
        for it in items:
            try:
                m = re.search(it["pattern"], text)
            except re.error:
                continue
            snip = None
            if m:
                a = max(0, m.start() - 30)
                snip = text[a:m.end() + 60].replace("\n", " ").strip()
            out.append({"key": it["key"], "label": it["label"], "present": bool(m), "evidence": snip})
        nums = [a for a in automation.parse_krw(text)]
        warn = []
        if len(set(nums)) > 1 and re.search(r"계약\s*금액", text):
            warn.append(f"문서에 서로 다른 금액이 {len(set(nums))}종 있습니다 — 계약금액 표기를 확인하세요")
        miss = [i["label"] for i in out if not i["present"]]
        return {"doc_no": row["doc_no"], "ver": vv, "items": out, "missing": miss, "warnings": warn,
                "note": "키워드 기반 점검입니다. 조항의 적정성·법적 효력은 판단하지 않으며, 법무 검토를 대신하지 않습니다."}

    # =============== what changed between versions ===============
    def version_summary(self, p, doc_id, a, b) -> dict:
        row = self.get_row(p, doc_id, "read")
        vis = self.visible_vers(p, row)
        if int(a) not in vis or int(b) not in vis:
            raise DmsError(404, "버전을 찾을 수 없습니다")
        ta, tb = self._ver(row, a)["text"], self._ver(row, b)["text"]
        pts: list[str] = []
        ma, mb = automation.parse_krw(ta), automation.parse_krw(tb)
        if sorted(ma) != sorted(mb):
            pts.append("금액 변경: " + (", ".join(f"{x:,}원" for x in ma[:5]) or "없음") + " → " + (", ".join(f"{x:,}원" for x in mb[:5]) or "없음"))
        da, db_ = automation.parse_dates(ta), automation.parse_dates(tb)
        if sorted(da) != sorted(db_):
            pts.append("날짜 변경: " + (", ".join(da[:4]) or "없음") + " → " + (", ".join(db_[:4]) or "없음"))
        ha = {l.lstrip("# ").strip() for l in ta.splitlines() if l.startswith("#")}
        hb = {l.lstrip("# ").strip() for l in tb.splitlines() if l.startswith("#")}
        if hb - ha:
            pts.append("추가된 항목: " + ", ".join(sorted(hb - ha))[:200])
        if ha - hb:
            pts.append("삭제된 항목: " + ", ".join(sorted(ha - hb))[:200])
        la, lb = ta.splitlines(), tb.splitlines()
        import difflib
        sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
        added = sum(j2 - j1 for t, i1, i2, j1, j2 in sm.get_opcodes() if t in ("insert", "replace"))
        removed = sum(i2 - i1 for t, i1, i2, j1, j2 in sm.get_opcodes() if t in ("delete", "replace"))
        samples = [lb[j1] for t, i1, i2, j1, j2 in sm.get_opcodes() if t in ("insert", "replace") and lb[j1].strip()][:3]
        return {"a": int(a), "b": int(b), "added_lines": added, "removed_lines": removed, "points": pts, "samples": [s[:160] for s in samples],
                "same": not (added or removed), "note": "규칙 기반 요약입니다(금액·날짜·제목 변화와 줄 단위 증감)."}

    # =============== knowledge gaps ===============
    def knowledge_gaps(self, p, days=90) -> dict:
        if not self.admin(p):
            raise DmsError(403, "관리자만 볼 수 있습니다")
        since = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat(timespec="seconds")
        rows = self.q("SELECT question, refused FROM audit WHERE ts>=? AND generator IS NOT NULL AND question NOT LIKE '[%' ORDER BY id DESC LIMIT 5000", (since,))
        groups: dict[str, dict] = {}
        for r in rows:
            if not r["refused"]:
                continue
            key = re.sub(r"[\s?？.!]+", "", r["question"])[:60]
            g = groups.setdefault(key, {"question": r["question"][:120], "count": 0})
            g["count"] += 1
        total = sum(1 for _ in rows)
        gaps = sorted(groups.values(), key=lambda x: -x["count"])[:30]
        return {"days": int(days), "questions": total, "unanswered": sum(g["count"] for g in groups.values()), "gaps": gaps,
                "note": "답변하지 못한(근거 부족·권한 없음 포함) 질문입니다. 해당 주제의 문서를 등록하거나 권한을 점검하세요."}

    # =============== evidence-based draft ===============
    def draft_from_evidence(self, p, topic: str, k=6) -> dict:
        """Compile excerpts the caller may read into a cited skeleton. Extractive on purpose: every line is traceable to a document."""
        topic = str(topic or "").strip()[:200]
        if len(topic) < 2:
            raise DmsError(400, "초안 주제를 입력하세요")
        ans = self.app.engine.ask(p, topic, k=int(k))
        ev = [e for e in ans.evidence if e.kind == "doc"]
        if not ev:
            raise DmsError(404, "근거가 될 문서를 찾지 못했습니다 (권한이 있는 문서만 사용합니다)")
        lines = [f"# {topic} (초안)", "", "> 아래는 열람 권한이 있는 문서의 발췌입니다. 검토 후 내용을 정리해 사용하세요.", ""]
        refs = []
        for n, e in enumerate(ev, 1):
            lines += [f"## {n}. {e.title}", "", e.text.strip()[:600], "", f"출처: [{n}] {e.title}", ""]
            refs.append({"n": n, "title": e.title, "ref": e.ref})
        lines += ["## 참고 문서", ""] + [f"- [{r['n']}] {r['title']}" for r in refs]
        return {"title": f"{topic} (초안)", "text": "\n".join(lines), "refs": refs, "note": "생성형 문장이 아닌 근거 발췌 조합입니다. 모든 문장이 위 문서에서 그대로 가져온 것입니다."}
