import { $, STATUS, api, aclEditor, avatar, confirmDialog, dialog, el, empty, fail, field, fileTile, fileToB64, fmtDate, fmtDay, fmtNum, go, menu, pill, relTime, route, saveBlob, state, statusPill, table, toast, typeName, userName } from "./core.js";
import { icon } from "./icons.js";

// ---------------- helpers ----------------
export function folderOptions(tree, { registerable = false } = {}) {
  const byParent = new Map();
  for (const f of tree) (byParent.get(f.parent_id) || byParent.set(f.parent_id, []).get(f.parent_id)).push(f);
  const out = [];
  (function walk(pid, prefix) {
    for (const f of byParent.get(pid) || []) {
      const label = prefix ? `${prefix} / ${f.name}` : f.name;
      if (!registerable || (f.can_register && f.origin !== "crawled")) out.push({ id: f.id, label });
      walk(f.id, label);
    }
  })(null, "");
  return out;
}
const typeSelect = (value = "GEN") => el("select", { id: "f-type", "aria-label": "문서유형" }, state.meta.doc_types.filter((t) => t.active).map((t) => el("option", { value: t.code, selected: t.code === value }, `${t.name} (${t.code})`)));

// ---------------- register dialog ----------------
export async function registerDialog(defaultFolder = 1) {
  const [tree, classRes] = await Promise.all([api("/api/dms/folders"), api("/api/dms/classifications")]);
  const opts = folderOptions(tree, { registerable: true });
  const cls = el("select", { id: "f-class", "aria-label": "기록물 분류" }, [el("option", { value: "" }, "(분류 없음 — 문서유형 기준)"), ...classRes.items.filter((c) => c.parent || c.retention_years).map((c) => el("option", { value: c.code }, `${c.code} ${c.name} · 보존 ${c.retention_years >= 99 ? "영구" : c.retention_years ? c.retention_years + "년" : "-"}`))]);
  const file = el("input", { type: "file", id: "f-file", required: true, "aria-label": "파일" });
  const title = el("input", { id: "f-title", placeholder: "비워 두면 파일 내용/이름에서 가져옵니다" });
  const type = typeSelect();
  const typeHint = el("div", { class: "hint" });
  const showHint = () => { const t = state.meta.doc_types.find((x) => x.code === type.value); typeHint.textContent = t ? `번호 ${t.pattern} · 보존 ${t.retention_years || 0}년 · ${t.approval_required ? "결재 후 게시" : "등록 즉시 게시"}` : ""; };
  type.addEventListener("change", showHint); showHint();
  const folder = el("select", { id: "f-folder", "aria-label": "폴더" }, opts.map((o) => el("option", { value: o.id, selected: o.id === Number(defaultFolder) }, o.label)));
  const tags = el("input", { id: "f-tags", placeholder: "쉼표로 구분 (예: 계약, 2026)" });
  const desc = el("textarea", { id: "f-desc", maxlength: "2000" });
  const level = el("input", { id: "f-level", type: "number", min: "0", max: String(state.me.level), value: "0" });
  const exp = el("input", { id: "f-exp", type: "date" });
  const custom = el("input", { type: "radio", name: "aclmode", id: "acl-custom" });
  const inherit = el("input", { type: "radio", name: "aclmode", id: "acl-inherit", checked: true });
  const editor = aclEditor([{ type: "dept", id: state.me.dept, role: "editor" }]);
  editor.hidden = true;
  custom.addEventListener("change", () => (editor.hidden = false)); inherit.addEventListener("change", () => (editor.hidden = true));
  return dialog("새 문서 등록", () => el("div", {},
    field("파일", file), field("제목", title), el("div", { class: "grid2" }, field("문서유형", type), field("폴더", folder)), typeHint,
    el("div", { class: "grid2" }, field("태그", tags), field("보안등급 (본인 L" + state.me.level + " 이하)", level)),
    field("기록물 분류 (선택)", cls), field("만료일 (선택)", exp), field("설명", desc),
    el("label", {}, "접근 권한"),
    el("label", { class: "inline" }, inherit, "폴더 권한 상속 (폴더에 지정이 없으면 내 부서만)"),
    el("label", { class: "inline" }, custom, "직접 지정"), editor),
  [{ label: "취소", value: false }, { label: "등록", primary: true, run: async () => {
    if (!file.files[0]) throw new Error("파일을 선택하세요");
    const body = { filename: file.files[0].name, content_b64: await fileToB64(file.files[0]), folder_id: Number(folder.value), doc_type: type.value,
      title: title.value.trim() || undefined, tags: tags.value.split(",").map((x) => x.trim()).filter(Boolean), description: desc.value,
      level: Number(level.value || 0), expires_at: exp.value || undefined, class_code: cls.value || undefined };
    if (custom.checked) body.entries = editor.get();
    const d = await api("/api/docs", { method: "POST", body });
    toast(`등록했습니다: ${d.doc_no}` + (d.status === "draft" ? " (결재 요청이 필요합니다)" : ""));
    if (d.privacy?.findings?.some((f) => f.severity === "high")) toast("개인정보가 검출되었습니다: " + d.privacy.findings.map((f) => `${f.label} ${f.count}건`).join(", "), "bad");
    if (d.duplicates?.length) toast(`내용이 같은 문서가 있습니다: ${d.duplicates.map((x) => x.doc_no).join(", ")}`);
    go("#/doc/" + d.id);
  } }]);
}

async function importDialog(defaultFolder) {
  const tree = await api("/api/dms/folders");
  const file = el("input", { type: "file", accept: ".zip", "aria-label": "ZIP 파일" });
  const folder = el("select", {}, folderOptions(tree, { registerable: true }).map((o) => el("option", { value: o.id, selected: o.id === Number(defaultFolder) }, o.label)));
  const type = typeSelect();
  const result = el("div", { class: "sec" });
  return dialog("ZIP 일괄 등록", () => el("div", {}, el("p", { class: "hint" }, "ZIP 안의 폴더 구조가 그대로 폴더로 만들어집니다. 최대 500개 파일, 지원하지 않거나 위험한 파일은 건너뜁니다."),
    field("ZIP 파일", file), el("div", { class: "grid2" }, field("대상 폴더", folder), field("문서유형", type)), result),
  [{ label: "닫기", value: false }, { label: "등록 시작", primary: true, run: async () => {
    if (!file.files[0]) throw new Error("ZIP 파일을 선택하세요");
    const r = await api("/api/dms/import-zip", { method: "POST", body: { folder_id: Number(folder.value), doc_type: type.value, content_b64: await fileToB64(file.files[0]) } });
    result.replaceChildren(el("p", {}, pill(`등록 ${r.created.length}건`, "ok"), " ", pill(`건너뜀 ${r.skipped.length}건`, r.skipped.length ? "warn" : "")),
      r.skipped.length ? table(["경로", "사유"], r.skipped.map((s) => [s.path, s.reason])) : "");
    return false;
  } }]);
}

async function folderDialog(tree, current) {
  const f = tree.find((x) => x.id === current);
  const name = el("input", { id: "fd-name", placeholder: "폴더 이름" });
  const modes = f ? ["하위 폴더 만들기", "이름/권한 변경"] : ["최상위 폴더 만들기"];
  const editor = aclEditor(f?.entries || []);
  const inherit = el("input", { type: "checkbox", checked: f ? f.inherit : true });
  return dialog(f ? `폴더: ${f.name}` : "새 폴더", () => el("div", {},
    field("이름 (새로 만들기 / 변경)", name),
    f && el("label", { class: "inline" }, inherit, "상위 폴더 권한 상속"),
    el("label", {}, "폴더 권한 (이 폴더 아래 문서에 상속됩니다)"), editor,
    el("p", { class: "hint" }, "열람 = 읽기·다운로드, 편집 = 새 문서·새 버전, 관리 = 권한·삭제. 폴더 권한을 바꾸면 하위 문서의 접근 범위가 즉시 다시 계산됩니다.")),
  [{ label: "닫기", value: false },
    ...(f && f.role >= 3 ? [{ label: "폴더 삭제", danger: true, run: async () => { if (!(await confirmDialog(`'${f.name}' 폴더를 삭제할까요? (비어 있어야 합니다)`, "삭제", true))) return false; await api("/api/dms/folders/" + f.id, { method: "DELETE" }); toast("삭제했습니다"); go("#/docs"); } },
      { label: "변경 저장", primary: true, run: async () => { const b = { entries: editor.get(), inherit: inherit.checked }; if (name.value.trim()) b.name = name.value.trim(); await api("/api/dms/folders/" + f.id, { method: "PUT", body: b }); toast("저장했습니다"); go("#/docs?folder=" + f.id); } }] : []),
    { label: f ? "하위 폴더 만들기" : "만들기", primary: !f, run: async () => {
      if (!name.value.trim()) throw new Error("폴더 이름을 입력하세요");
      const nf = await api("/api/dms/folders", { method: "POST", body: { parent_id: f ? f.id : null, name: name.value.trim(), entries: f ? [] : editor.get() } });
      toast("폴더를 만들었습니다"); go("#/docs?folder=" + nf.id); } }]);
}

// ---------------- list view ----------------
function renderTree(tree, current, onPick) {
  const byParent = new Map();
  for (const f of tree) (byParent.get(f.parent_id) || byParent.set(f.parent_id, []).get(f.parent_id)).push(f);
  const build = (pid) => { const kids = byParent.get(pid) || []; return kids.length ? el("ul", {}, kids.map((f) => el("li", {},
    el("button", { type: "button", class: f.id === current ? "on" : "", onclick: () => onPick(f.id) }, el("span", {}, f.name), f.doc_count ? el("span", { class: "n" }, f.doc_count) : ""), build(f.id)))) : ""; };
  return el("nav", { class: "card tree", "aria-label": "폴더" }, el("button", { type: "button", class: !current ? "on" : "", onclick: () => onPick(null) }, "전체 문서"), build(null));
}
const snippetNode = (sn) => {
  if (!sn) return "";
  const span = el("div", { class: "snip" });
  let pos = 0;
  for (const [a, b] of [...sn.hits].sort((x, y) => x[0] - y[0])) { if (a < pos) continue; span.append(sn.text.slice(pos, a), el("mark", {}, sn.text.slice(a, b))); pos = b; }
  span.append(sn.text.slice(pos));
  return span;
};

route(/^\/docs$/, async (view, _m, params) => {
  const [tree, saved] = await Promise.all([api("/api/dms/folders"), api("/api/dms/saved")]);
  const q = new URLSearchParams(); for (const k of ["q", "type", "status", "tag", "folder", "owner", "from", "to", "sort", "page", "mine", "favorites", "origin"]) if (params[k]) q.set(k, params[k]);
  const res = await api("/api/docs?" + q.toString());
  const cur = params.folder ? Number(params.folder) : null;
  const curF = tree.find((f) => f.id === cur);
  const nav = (patch) => { const n = { ...params, ...patch, page: patch.page || 1 }; for (const k of Object.keys(n)) if (n[k] === "" || n[k] == null) delete n[k]; go("#/docs?" + new URLSearchParams(n)); };
  const selected = new Set();
  const bulk = el("div", { class: "bulkbar", hidden: true });
  const updateBulk = () => {
    bulk.hidden = selected.size === 0;
    const act = (label, fn, cls = "") => el("button", { type: "button", class: "sm " + cls, onclick: () => fn().catch(fail) }, label);
    const run = async (action, params2 = {}) => { const r = await api("/api/dms/bulk", { method: "POST", body: { action, ids: [...selected], params: params2 } }); toast(`${r.ok}건 처리, ${r.failed}건 실패`, r.failed ? "bad" : ""); if (r.failed) await dialog("일괄 작업 결과", () => table(["문서 ID", "결과"], r.results.filter((x) => !x.ok).map((x) => [x.id, x.error]))); go(location.hash); };
    bulk.replaceChildren(el("b", {}, `${selected.size}건 선택`),
      act("이동", async () => { const opts = folderOptions(tree, { registerable: true }); const sel = el("select", {}, opts.map((o) => el("option", { value: o.id }, o.label))); if (await dialog("폴더 이동", () => field("대상 폴더", sel), [{ label: "취소", value: false }, { label: "이동", primary: true }])) await run("move", { folder_id: Number(sel.value) }); }),
      act("태그 추가", async () => { const t = el("input", { placeholder: "쉼표로 구분" }); if (await dialog("태그 추가", () => field("태그", t), [{ label: "취소", value: false }, { label: "추가", primary: true }])) await run("add_tags", { tags: t.value.split(",").map((x) => x.trim()).filter(Boolean) }); }),
      act("태그 제거", async () => { const t = el("input", { placeholder: "쉼표로 구분" }); if (await dialog("태그 제거", () => field("태그", t), [{ label: "취소", value: false }, { label: "제거", primary: true }])) await run("remove_tags", { tags: t.value.split(",").map((x) => x.trim()).filter(Boolean) }); }),
      act("즐겨찾기", () => run("favorite")),
      act("ZIP 내보내기", async () => { await saveBlob("/api/dms/export", { method: "POST", body: { ids: [...selected] } }, "export.zip"); }),
      act("휴지통으로", async () => { if (await confirmDialog(`${selected.size}건을 휴지통으로 옮길까요? (보존기간·법적 보류 문서는 거부됩니다)`, "삭제", true)) await run("delete"); }, "danger"));
  };
  const typeF = el("select", { "aria-label": "유형" }, [el("option", { value: "" }, "유형 전체"), ...state.meta.doc_types.map((t) => el("option", { value: t.code, selected: params.type === t.code }, t.name))]);
  const statusF = el("select", { "aria-label": "상태" }, [el("option", { value: "" }, "상태 전체"), ...[["draft", "작성 중"], ["in_review", "결재 중"], ["approved", "승인"], ["rejected", "반려"], ["expired", "만료"]].map(([v, l]) => el("option", { value: v, selected: params.status === v }, l))]);
  const qIn = el("input", { type: "search", id: "list-q", value: params.q || "", placeholder: "제목 · 문서번호 · 내용 · 태그로 검색", "aria-label": "키워드" });
  const tagIn = el("input", { value: params.tag || "", placeholder: "태그", "aria-label": "태그" });
  const fromIn = el("input", { type: "date", value: params.from || "", "aria-label": "시작일" }), toIn = el("input", { type: "date", value: params.to || "", "aria-label": "종료일" });
  const sortF = el("select", { "aria-label": "정렬", onchange: () => nav({ sort: sortF.value }) }, [["", q.get("q") ? "관련도순" : "최근 수정순"], ["updated", "최근 수정순"], ["title", "제목순"], ["doc_no", "문서번호순"]].map(([v, l]) => el("option", { value: v, selected: (params.sort || "") === v }, l)));
  const savedSel = el("select", { "aria-label": "저장된 검색", onchange: (e) => { const s = saved.find((x) => String(x.id) === e.target.value); if (s) nav({ ...Object.fromEntries(["q", "type", "status", "tag", "folder", "owner", "from", "to", "sort", "mine", "favorites"].map((k) => [k, ""])), ...s.query }); } },
    [el("option", { value: "" }, "저장된 검색..."), ...saved.map((s) => el("option", { value: s.id }, s.name))]);
  const titleText = params.favorites ? "즐겨찾기" : params.mine ? "내가 올린 문서" : params.q ? `검색: ${params.q}` : curF ? curF.name : "문서함";
  const FILTER_LABEL = { type: "유형", status: "상태", tag: "태그", owner: "소유자", from: "시작", to: "종료" };
  const active = Object.keys(FILTER_LABEL).filter((k) => params[k]);
  const filterName = (k, v) => k === "type" ? typeName(v) : k === "status" ? (STATUS[v]?.[0] || v) : k === "owner" ? userName(v) : v;
  const rows = res.items.map((d) => {
    const chk = el("input", { type: "checkbox", "aria-label": d.title, onclick: (e) => e.stopPropagation(), onchange: (e) => { e.target.checked ? selected.add(d.id) : selected.delete(d.id); updateBulk(); } });
    const ver = d.origin === "crawled" ? "" : d.effective_ver ? `v${d.effective_ver}` + (d.current_ver > d.effective_ver ? ` · v${d.current_ver} 작성 중` : "") : `v${d.current_ver}`;
    return { id: d.id, cells: [el("span", { onclick: (e) => e.stopPropagation() }, chk),
      el("div", { class: "doc-cell" }, fileTile(d.filename || d.title), el("div", { class: "doc-main" },
        el("div", { class: "doc-title" }, d.title), el("div", { class: "doc-sub" }, el("span", { class: "ref" }, d.doc_no), " · ", typeName(d.doc_type), ver ? " · " + ver : ""),
        d.snippet ? snippetNode(d.snippet) : "", d.tags.length ? el("div", { class: "chips doc-tags" }, d.tags.slice(0, 5).map((t) => el("span", { class: "tag" }, "#" + t))) : "")),
      el("div", { class: "doc-flags" }, statusPill(d.status), d.locked_by ? pill("잠김 " + d.locked_by, "warn") : "", d.legal_hold ? pill("법적 보류", "bad") : "", d.expires_at ? pill("만료 " + d.expires_at, d.status === "expired" ? "bad" : "") : "", !d.readable ? pill("읽기 불가", "warn") : ""),
      el("div", { class: "who" }, avatar(d.origin === "crawled" ? "외" : userName(d.owner || "-")), el("span", {}, d.origin === "crawled" ? "외부 소스" : userName(d.owner || "-"))),
      el("span", { class: "when", title: fmtDate(d.updated) }, relTime(d.updated))] };
  });
  const facetChips = (key, label, param) => res.facets[key].length ? el("div", { class: "row" }, el("span", { class: "hint" }, label), el("div", { class: "chips" }, res.facets[key].slice(0, 8).map((f) =>
    el("button", { type: "button", class: "chip" + (params[param] === f.value ? " on" : ""), onclick: () => nav({ [param]: params[param] === f.value ? "" : f.value }) },
      `${key === "type" ? typeName(f.value) : key === "owner" ? (f.value === "system" ? "시스템(외부 소스)" : userName(f.value)) : key === "status" ? (STATUS[f.value]?.[0] || f.value) : f.value} ${f.count}`)))) : "";
  const pages = Math.max(1, Math.ceil(res.total / res.size));
  const adv = el("div", { class: "adv", hidden: !(params.type || params.status || params.tag || params.from || params.to) },
    el("div", { class: "filters" }, el("div", {}, el("label", {}, "유형"), typeF), el("div", {}, el("label", {}, "상태"), statusF), el("div", {}, el("label", {}, "태그"), tagIn), el("div", {}, el("label", {}, "수정일 시작"), fromIn), el("div", {}, el("label", {}, "수정일 종료"), toIn)),
    el("div", { class: "row sec" }, el("button", { class: "primary", type: "submit" }, "적용"), el("button", { type: "button", onclick: () => go("#/docs") }, "초기화"),
      el("button", { type: "button", onclick: async () => { const n = el("input", { placeholder: "이름" }); if (await dialog("검색 저장", () => field("이름", n), [{ label: "취소", value: false }, { label: "저장", primary: true }])) { await api("/api/dms/saved", { method: "POST", body: { name: n.value, query: Object.fromEntries(q.entries()) } }); toast("저장했습니다"); go(location.hash); } } }, "이 검색 저장"),
      saved.length ? savedSel : ""));
  view.replaceChildren(
    el("div", { class: "page-h" }, el("h1", {}, titleText), el("span", { class: "count-pill" }, `${fmtNum(res.total)}건`), el("span", { class: "grow" }),
      el("button", { id: "btn-tpl", onclick: () => go("#/new") }, icon("file", 16), "양식으로 작성"),
      menu("더 보기", [{ label: "ZIP 일괄 등록", icon: "upload", run: () => importDialog(cur || 1) }, { label: curF ? "폴더 관리" : "새 폴더", icon: "folder", run: () => folderDialog(tree, cur) }]),
      el("button", { class: "primary", id: "btn-new", onclick: () => registerDialog(cur || 1).catch(fail) }, icon("plus", 16), "새 문서")),
    el("div", { class: "browser" }, renderTree(tree, cur, (id) => nav({ folder: id || "", favorites: "", mine: "" })),
      el("div", { class: "stack" },
        el("form", { class: "card toolbar", onsubmit: (e) => { e.preventDefault(); nav({ q: qIn.value.trim(), type: typeF.value, status: statusF.value, tag: tagIn.value.trim(), from: fromIn.value, to: toIn.value }); } },
          el("div", { class: "tb-row" }, el("div", { class: "tb-search" }, icon("search", 16), qIn),
            el("button", { type: "button", class: "ghost", "aria-expanded": String(!adv.hidden), onclick: (e) => { adv.hidden = !adv.hidden; e.currentTarget.setAttribute("aria-expanded", String(!adv.hidden)); } }, icon("settings", 16), "필터", active.length ? el("span", { class: "nb" }, String(active.length)) : ""),
            sortF, el("button", { class: "primary", type: "submit" }, "검색")),
          adv,
          active.length ? el("div", { class: "chips sec" }, active.map((k) => el("button", { type: "button", class: "chip on", onclick: () => nav({ [k]: "" }) }, `${FILTER_LABEL[k]}: ${filterName(k, params[k])}`, icon("x", 12)))) : ""),
        el("div", { class: "facets" }, facetChips("type", "유형", "type"), facetChips("status", "상태", "status"), facetChips("tag", "태그", "tag"), facetChips("owner", "소유자", "owner")),
        bulk,
        rows.length ? el("div", { class: "card flush-card" }, table(["", "문서", "상태", "소유자", "수정"], rows, { onRow: (r) => go("#/doc/" + r.id), cls: "docs-tbl" }))
          : el("div", { class: "card" }, empty(params.q ? "검색 결과가 없습니다. 읽을 권한이 있는 문서만 검색됩니다." : "문서가 없습니다. '새 문서'로 등록하거나 양식으로 작성해 보세요.", "search")),
        el("div", { class: "row end" }, el("button", { disabled: res.page <= 1, onclick: () => nav({ page: res.page - 1 }) }, "이전"), el("span", { class: "hint" }, `${res.page} / ${pages}`), el("button", { disabled: res.page >= pages, onclick: () => nav({ page: res.page + 1 }) }, "다음")))));
  updateBulk();
});
