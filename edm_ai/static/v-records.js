import { api, avatar, confirmDialog, dialog, el, empty, fail, field, fileToB64, fmtDate, fmtDay, fmtNum, go, pill, route, saveBlob, state, table, toast, userName } from "./core.js";
import { icon } from "./icons.js";

export const yearsLabel = (y) => (y >= 99 ? "영구" : y > 0 ? `${y}년` : "없음");
const TABS = [["disposal", "폐기 심의", "trash"], ["classes", "분류표", "layers"], ["ledger", "폐기 대장", "book"], ["privacy", "개인정보 점검", "shield"]];
const SEV = { high: ["높음", "bad"], medium: ["중간", "warn"], low: ["낮음", ""] };
const STATE = { pending: ["심의 중", "info"], approved: ["승인·폐기", "ok"], rejected: ["반려", "bad"] };

route(/^\/records$/, async (view, _m, params) => {
  const tab = TABS.some(([t]) => t === params.tab) ? params.tab : "disposal";
  const body = el("div", { class: "sec" });
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "기록물 관리"), el("span", { class: "hint" }, "분류·보존·폐기·개인정보. 담당자는 번호·제목 등 메타데이터만 보며 본문은 열람하지 않습니다.")),
    el("div", { class: "tabs", role: "tablist" }, TABS.map(([t, l, ic]) => el("button", { role: "tab", "aria-selected": String(t === tab), onclick: () => go("#/records?tab=" + t) }, icon(ic, 15), l))), body);
  body.append(await { disposal: disposalTab, classes: classesTab, ledger: ledgerTab, privacy: privacyTab }[tab]());
});

// ---------- disposal ----------
async function disposalTab() {
  const [cand, list] = await Promise.all([api("/api/dms/disposal/candidates"), api("/api/dms/disposal")]);
  const sel = new Set();
  const reason = el("input", { id: "disp-reason", placeholder: "폐기 사유 (예: 보존기간 경과, 기록물 폐기 계획 2026-1호)", "aria-label": "폐기 사유" });
  const btn = el("button", { class: "primary", id: "disp-go", disabled: true, onclick: async () => {
    try { const r = await api("/api/dms/disposal", { method: "POST", body: { ids: [...sel], reason: reason.value } }); toast(`폐기 심의 #${r.id} 를 신청했습니다 (${r.items}건)`); go("#/records?tab=disposal"); } catch (e) { fail(e); } } }, icon("task", 16), "폐기 심의 신청");
  const upd = () => { btn.disabled = sel.size === 0; };
  const all = el("input", { type: "checkbox", "aria-label": "전체 선택", onchange: (e) => { document.querySelectorAll(".cand-chk").forEach((c) => { c.checked = e.target.checked; e.target.checked ? sel.add(Number(c.value)) : sel.delete(Number(c.value)); }); upd(); } });
  const rows = cand.items.map((c) => [el("input", { type: "checkbox", class: "cand-chk", value: c.doc_id, "aria-label": c.doc_no, onchange: (e) => { e.target.checked ? sel.add(c.doc_id) : sel.delete(c.doc_id); upd(); } }),
    el("span", { class: "ref" }, c.doc_no), c.title, c.class_name ? `${c.class_code} ${c.class_name}` : "-", c.folder, userName(c.owner), c.retention_until]);
  const pending = list.items.filter((d) => d.status === "pending"), done = list.items.filter((d) => d.status !== "pending");
  const decide = (d, approve) => async () => {
    if (approve && !(await confirmDialog(`#${d.id} ${d.items.length}건을 영구 삭제합니다. 되돌릴 수 없습니다. (폐기 대장에는 기록이 남습니다)`, "승인·폐기", true))) return;
    const note = el("input", { placeholder: "의견 (선택)" });
    if (!approve && !(await dialog("반려", () => field("의견", note), [{ label: "취소", value: false }, { label: "반려", danger: true }]))) return;
    try { const r = await api(`/api/dms/disposal/${d.id}/decide`, { method: "POST", body: { approve, note: note.value } }); toast(approve ? `폐기 ${r.disposed}건, 제외 ${r.skipped}건` : "반려했습니다"); go("#/records?tab=disposal"); } catch (e) { fail(e); }
  };
  const card = (d) => el("div", { class: "card" }, el("div", { class: "row" }, el("b", {}, `#${d.id}`), pill(...(STATE[d.status] || [d.status, ""])), el("span", { class: "hint" }, `${userName(d.requested_by)} · ${fmtDate(d.requested_at)}`), el("span", { class: "grow" }),
    d.status === "pending" && state.me.is_admin && d.requested_by !== state.me.id ? [el("button", { class: "sm danger", onclick: decide(d, false) }, "반려"), el("button", { class: "sm primary", onclick: decide(d, true) }, "승인·폐기")] : "",
    d.status === "pending" && d.requested_by === state.me.id ? el("span", { class: "hint" }, "다른 관리자의 승인을 기다립니다") : ""),
  el("p", { class: "hint" }, "사유: " + d.reason + (d.note ? ` · 의견: ${d.note}` : "")), table(["문서번호", "제목", "분류", "보존만료", "결과"], d.items.map((i) => [el("span", { class: "ref" }, i.doc_no), i.title, i.class_code || "-", i.retention_until, i.result || "-"])));
  return el("div", { class: "stack" },
    el("div", { class: "card" }, el("h3", {}, icon("clock", 15), `폐기 대상 후보 (${cand.items.length})`), el("p", { class: "hint" }, "보존기간이 끝났고 법적 보류·결재 진행이 없는 문서입니다. 신청 후 다른 관리자가 승인하면 영구 삭제됩니다."),
      cand.items.length ? [table([all, "문서번호", "제목", "분류", "폴더", "소유자", "보존만료"], rows), el("div", { class: "row sec" }, el("div", { class: "grow" }, reason), btn)] : empty("폐기 대상 문서가 없습니다", "task")),
    pending.length ? el("div", { class: "stack" }, el("h3", {}, "심의 중"), pending.map(card)) : "", done.length ? el("div", { class: "stack" }, el("h3", {}, "처리 완료"), done.slice(0, 10).map(card)) : "");
}

// ---------- classification scheme ----------
async function classesTab() {
  const { items } = await api("/api/dms/classifications?all=1");
  const byParent = new Map();
  for (const c of items) (byParent.get(c.parent || "") || byParent.set(c.parent || "", []).get(c.parent || "")).push(c);
  const rows = [];
  const walk = (parent, depth) => { for (const c of byParent.get(parent) || []) { rows.push([el("span", { class: "cls-code d" + depth }, c.code), el("span", { class: "d" + depth }, c.name, c.active ? "" : pill("중단", "warn")), yearsLabel(c.retention_years), fmtNum(c.documents), c.description || "",
    el("button", { class: "sm", onclick: () => editClass(c, items) }, "편집")]); walk(c.code, depth + 1); } };
  walk("", 0);
  const imp = el("input", { type: "file", accept: ".csv", "aria-label": "분류표 CSV" });
  return el("div", { class: "stack" }, el("div", { class: "row" }, el("button", { class: "primary", onclick: () => editClass(null, items) }, icon("plus", 16), "분류 추가"),
    el("button", { onclick: () => dialog("분류표 CSV 가져오기", () => el("div", {}, el("p", { class: "hint" }, "열: 코드, 이름, 상위코드, 보존연수(99=영구), 설명. 한 줄이라도 잘못되면 전체가 취소됩니다."), field("CSV 파일", imp)),
      [{ label: "취소", value: false }, { label: "가져오기", primary: true, run: async () => { if (!imp.files[0]) throw new Error("파일을 선택하세요"); const r = await api("/api/dms/admin/classifications/import", { method: "POST", body: { content_b64: await fileToB64(imp.files[0]) } }); toast(`${r.imported}건을 가져왔습니다`); go("#/records?tab=classes"); } }]) }, icon("upload", 16), "CSV 가져오기")),
  el("div", { class: "card" }, rows.length ? table(["코드", "분류", "보존기간", "문서 수", "설명", ""], rows) : empty("분류표가 없습니다. 분류를 추가하거나 CSV로 가져오세요.", "layers")),
  el("p", { class: "hint" }, "문서에 분류를 지정하면 보존기간이 문서유형의 보존기간과 비교해 더 긴 쪽으로 적용됩니다 (영구 = 삭제·폐기 불가)."));
}
function editClass(c, all) {
  const code = el("input", { value: c?.code || "", disabled: !!c, "aria-label": "코드" }), name = el("input", { value: c?.name || "", "aria-label": "분류명" });
  const parent = el("select", { "aria-label": "상위 분류" }, [el("option", { value: "" }, "(최상위)"), ...all.filter((x) => x.code !== c?.code).map((x) => el("option", { value: x.code, selected: x.code === c?.parent }, `${x.code} ${x.name}`))]);
  const years = el("input", { type: "number", min: "0", max: "99", value: String(c?.retention_years ?? 0), "aria-label": "보존연수" }), desc = el("input", { value: c?.description || "", "aria-label": "설명" });
  const active = el("input", { type: "checkbox", checked: c ? c.active : true });
  return dialog(c ? "분류 편집" : "분류 추가", () => el("div", {}, field("코드", code, "영문·숫자·.-_ 20자 이하"), field("이름", name), field("상위 분류", parent), field("보존연수", years, "0=문서유형 기준, 99=영구"), field("설명", desc),
    el("label", { class: "inline sec" }, active, "사용")), [{ label: "취소", value: false }, { label: "저장", primary: true, run: async () => {
    await api("/api/dms/admin/classifications", { method: "PUT", body: { code: code.value, name: name.value, parent: parent.value, retention_years: Number(years.value), description: desc.value, active: active.checked } });
    toast("저장했습니다"); go("#/records?tab=classes"); } }]);
}

// ---------- ledger ----------
async function ledgerTab() {
  const { items } = await api("/api/dms/disposal/ledger");
  return el("div", { class: "stack" }, el("div", { class: "row" }, el("span", { class: "hint" }, `${items.length}건`), el("span", { class: "grow" }), el("button", { onclick: () => saveBlob("/api/dms/disposal/ledger.csv", {}, "disposal-ledger.csv") }, icon("download", 16), "CSV 내보내기")),
    el("div", { class: "card" }, items.length ? table(["폐기일시", "문서번호", "제목", "분류", "보존만료", "신청자", "승인자", "최종 SHA-256"], items.map((r) => [fmtDate(r.disposed_at), el("span", { class: "ref" }, r.doc_no), r.title, r.class_code || "-", r.retention_until,
      userName(r.requested_by), userName(r.approved_by), el("span", { class: "ref" }, (r.sha256 || "").slice(0, 16) + "…")])) : empty("폐기 이력이 없습니다", "book")),
    el("p", { class: "hint" }, "폐기 대장에는 문서 내용이 남지 않으며, 번호·제목·승인자·최종 파일 해시만 보존됩니다."));
}

// ---------- privacy ----------
async function privacyTab() {
  const ov = await api("/api/dms/admin/privacy");
  const modeLabel = { warn: "경고 (등록 허용, 알림)", block: "차단 (고위험 정보 포함 시 등록 거부)", raise_level: "보호 (보안등급 상향)", off: "꺼짐" }[ov.mode] || ov.mode;
  return el("div", { class: "stack" }, el("div", { class: "row" }, el("span", { class: "hint" }, "정책: "), pill(modeLabel, ov.mode === "off" ? "warn" : "info"), el("span", { class: "grow" }),
    el("button", { onclick: async (e) => { e.target.disabled = true; try { const r = await api("/api/dms/admin/privacy/scan", { method: "POST", body: {} }); toast(`${r.scanned}건 점검, ${r.with_findings}건 검출`); go("#/records?tab=privacy"); } catch (x) { fail(x); } finally { e.target.disabled = false; } } }, icon("search", 16), "전체 재점검")),
  el("div", { class: "chips" }, ov.totals.length ? ov.totals.map((t) => el("span", { class: "chip on" }, `${t.label} ${t.count}건`)) : el("span", { class: "hint" }, "검출된 항목이 없습니다")),
  el("div", { class: "card" }, ov.items.length ? table(["문서번호", "제목", "검출 항목 (값은 저장하지 않음)", "보안등급", "폴더", "소유자"], ov.items.map((i) => [el("span", { class: "ref" }, i.doc_no),
    i.title ? el("button", { class: "link", onclick: () => go("#/doc/" + i.doc_id) }, i.title) : el("span", { class: "hint" }, "(열람 권한 없음)"),
    el("div", { class: "chips" }, i.findings.map((f) => pill(`${f.label} ${f.count}`, SEV[f.severity][1]))), "L" + i.level, i.folder, userName(i.owner)])) : empty("개인정보가 검출된 문서가 없습니다", "shield")),
  el("p", { class: "hint" }, "검출은 형식·검증숫자(생년월일, Luhn, 체크섬)를 확인하는 규칙 기반입니다. 오탐·미탐이 있을 수 있으므로 보조 수단으로 사용하세요."));
}
