import { api, dialog, el, empty, fail, fileToB64, fmtDate, fmtDay, go, pill, route, table, toast, userName } from "./core.js";
import { icon } from "./icons.js";

// ---------- natural-language search ----------
export async function nlDialog() {
  const q = el("input", { id: "nl-q", placeholder: "예: 내가 올린 승인된 계약서 최근 30일 지체상금", "aria-label": "자연어 검색" });
  const out = el("div", { class: "sec", id: "nl-out" });
  const run = async () => {
    try {
      const r = await api("/api/dms/nl-search?q=" + encodeURIComponent(q.value));
      out.replaceChildren(el("div", { class: "chips" }, r.explain.length ? r.explain.map((x) => el("span", { class: "chip on" }, x)) : el("span", { class: "hint" }, "적용된 조건 없음"), r.q ? el("span", { class: "chip" }, "검색어: " + r.q) : ""),
        r.result.items.length ? table(["문서번호", "제목", "상태", "수정일"], r.result.items.slice(0, 15).map((d) => [el("span", { class: "ref" }, d.doc_no), el("button", { class: "link", onclick: () => { document.querySelector("dialog[open]")?.close(); go("#/doc/" + d.id); } }, d.title), d.status, fmtDay(d.updated)])) : empty("조건에 맞는 문서가 없습니다", "search"),
        el("p", { class: "hint" }, `${r.result.total}건 · 이해한 조건을 위에 보여 줍니다. 권한이 있는 문서만 나옵니다.`));
    } catch (e) { fail(e); }
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  await dialog("자연어로 찾기", () => el("div", {}, el("div", { class: "row" }, el("div", { class: "grow" }, q), el("button", { class: "primary", id: "nl-go", onclick: run }, icon("search", 16), "찾기")), out));
}

// ---------- evidence-based draft ----------
export async function draftDialog(folderId) {
  const topic = el("input", { id: "dr-topic", placeholder: "예: 인센티브 지급 기준", "aria-label": "초안 주제" });
  const out = el("div", { class: "sec" });
  let last = null;
  const make = async () => {
    try { last = await api("/api/dms/draft", { method: "POST", body: { topic: topic.value } }); out.replaceChildren(el("p", { class: "hint" }, last.note), el("pre", { class: "body", id: "dr-text" }, last.text)); } catch (e) { last = null; out.replaceChildren(); fail(e); }
  };
  await dialog("근거 기반 초안", () => el("div", {}, el("p", { class: "hint" }, "열람 권한이 있는 문서에서 관련 부분을 모아 출처와 함께 초안을 만듭니다. 문장을 새로 지어내지 않습니다."), el("div", { class: "row" }, el("div", { class: "grow" }, topic), el("button", { class: "primary", id: "dr-go", onclick: make }, "초안 만들기")), out),
    [{ label: "닫기", value: false }, { label: "문서로 저장", primary: true, run: async () => {
      if (!last) { toast("먼저 초안을 만드세요", "bad"); return false; }
      const f = new File([last.text], `${last.title}.md`.replace(/[\\/:*?"<>|]/g, "_"), { type: "text/markdown" });
      const r = await api("/api/docs", { method: "POST", body: { filename: f.name, content_b64: await fileToB64(f), folder_id: folderId || 1, title: last.title, doc_type: "GEN", tags: ["초안"] } });
      toast("초안 문서를 만들었습니다"); go("#/doc/" + r.id);
    } }]);
}

// ---------- per-document checks (clauses) ----------
export async function insightTab(d) {
  const [c, vs] = await Promise.all([api(`/api/docs/${d.id}/clauses`).catch((e) => ({ error: e.message })), Promise.resolve(null)]);
  if (c.error) return empty(c.error, "file");
  return el("div", { class: "stack" }, el("div", { class: "card" }, el("h3", {}, icon("check", 15), "표준 조항 점검"),
    c.warnings.map((w) => el("p", { class: "bad" }, w)),
    table(["항목", "결과", "근거 문장"], c.items.map((i) => [i.label, pill(i.present ? "있음" : "없음", i.present ? "ok" : "warn"), i.evidence ? el("span", { class: "hint" }, "…" + i.evidence + "…") : ""])),
    el("p", { class: "hint" }, c.note)));
}

// ---------- AI trust center + knowledge gaps (admin) ----------
route(/^\/admin\/trust$/, async (view) => {
  const [t, g] = await Promise.all([api("/api/dms/admin/trust"), api("/api/dms/admin/knowledge-gaps")]);
  const L = t.latest;
  const run = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try { await api("/api/dms/admin/trust/run", { method: "POST", body: {} }); toast("점검을 마쳤습니다"); go("#/admin/trust"); } catch (x) { fail(x); } finally { btn.disabled = false; }
  };
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "AI 신뢰성 센터"), el("span", { class: "hint" }, "권한 없는 정보가 AI 답변에 섞이지 않는지 정기적으로 시험합니다")),
    el("div", { class: "card" }, el("div", { class: "row" }, icon("shield", 20), el("b", {}, "누출 시험 (골든 세트)"), t.daily ? pill("매일 자동 실행", "info") : pill("수동"), el("span", { class: "grow" }),
      t.configured ? el("button", { class: "primary", id: "trust-run", onclick: run }, icon("history", 16), "지금 시험") : ""),
      !t.configured ? el("p", { class: "hint" }, "설정 eval.golden 에 평가 세트(JSON)를 지정하세요. 예: examples/demo/golden.json") : L ? el("div", { class: "statrow" },
        el("div", { class: "stat" }, el("span", { class: "hint" }, "접근 제한 정보 노출"), el("b", { class: L.leaks ? "bad" : "" }, String(L.leaks)), pill(L.leaks ? "즉시 확인" : "안전", L.leaks ? "bad" : "ok")),
        el("div", { class: "stat" }, el("span", { class: "hint" }, "근거 재현율"), el("b", {}, (L.recall * 100).toFixed(0) + "%"), pill(L.recall >= t.policy.min_recall ? "기준 충족" : "기준 미달", L.recall >= t.policy.min_recall ? "ok" : "warn")),
        el("div", { class: "stat" }, el("span", { class: "hint" }, "사례 수 / 마지막 실행"), el("b", {}, `${L.cases}건`), el("span", { class: "hint" }, fmtDate(L.ts)))) : empty("아직 실행 기록이 없습니다", "shield"),
      L?.failures?.length ? el("div", { class: "sec" }, el("h3", {}, "확인이 필요한 사례"), el("pre", { class: "body" }, L.failures.join("\n"))) : ""),
    t.runs.length ? el("div", { class: "card" }, el("h3", {}, "실행 이력"), table(["일시", "실행", "사례", "재현율", "노출"], t.runs.map((r) => [fmtDate(r.ts), userName(r.by), r.cases, (r.recall * 100).toFixed(0) + "%", r.leaks ? pill(String(r.leaks), "bad") : pill("0", "ok")]))) : "",
    el("div", { class: "card" }, el("h3", {}, icon("search", 15), "지식 공백 — 답하지 못한 질문"),
      g.gaps.length ? table(["질문", "횟수"], g.gaps.map((x) => [x.question, x.count])) : empty("최근 답하지 못한 질문이 없습니다", "check"), el("p", { class: "hint" }, `최근 ${g.days}일 질문 ${g.questions}건 중 ${g.unanswered}건. ${g.note}`)));
});
