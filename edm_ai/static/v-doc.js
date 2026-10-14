import { $, renderMd, aclEditor, api, confirmDialog, dialog, el, empty, fail, field, fileToB64, fmtDate, fmtDay, fmtNum, fmtSize, go, pill, route, saveBlob, state, statusPill, table, toast, typeName, userName } from "./core.js";
import { folderOptions } from "./v-docs.js";
import { requestDialog } from "./v-social.js";
import { icon } from "./icons.js";

const TABS = [["info", "개요"], ["ask", "AI 질문"], ["versions", "버전"], ["approval", "결재"], ["comments", "댓글"], ["acl", "권한"], ["share", "외부 공유"], ["history", "이력"]];
const ACTION = { registered: "등록", new_version: "새 버전", restored: "버전 복원", submitted: "결재 요청", approved_step: "결재 승인", rejected: "반려", delegated: "결재 위임",
  workflow_cancelled: "결재 회수", checked_out: "체크아웃", checkout_cancelled: "체크아웃 해제", downloaded: "다운로드", acl_changed: "권한 변경", access_requested: "권한 신청", access_granted: "권한 승인", access_denied: "권한 거절", meta_changed: "정보 변경", moved: "이동",
  deleted: "삭제", restored_from_trash: "휴지통 복원", legal_hold_on: "법적 보류", legal_hold_off: "법적 보류 해제", expired: "만료", expiry_changed: "만료일 변경", commented: "댓글",
  comment_deleted: "댓글 삭제", linked: "문서 연결", unlinked: "연결 해제", share_created: "공유 링크 생성", share_revoked: "공유 링크 폐지", share_viewed: "공유 열람", share_downloaded: "공유 다운로드", indexed: "색인" };

route(/^\/doc\/(\d+)$/, async (view, m, params) => {
  const id = Number(m[1]);
  const d = await api("/api/docs/" + id);
  const reload = () => go(location.hash);
  const can = (p) => (d.perms || []).includes(p);
  const tab = params.tab || "info";
  const goTab = (t) => go(`#/doc/${id}?tab=${t}`);

  // ---------- header + actions ----------
  const act = (label, fn, cls = "") => el("button", { type: "button", class: cls, onclick: () => Promise.resolve(fn()).catch(fail) }, label);
  const actions = [];
  if (d.readable !== false) {
    if (can("download")) actions.push(act("다운로드", () => saveBlob(`/api/docs/${id}/download`, {}, d.title)));
    if (can("edit") && d.origin === "managed") {
      if (/\.(md|txt)$/i.test(d.versions?.[0]?.filename || "")) actions.push(act("온라인 편집", () => go(`#/doc/${id}/edit`)));
      actions.push(act("새 버전 올리기", () => newVersionDialog(d, reload)));
      actions.push(d.locked_by ? (d.locked_by === state.me.id || can("manage") ? act("체크아웃 해제", async () => { await api(`/api/docs/${id}/checkout/cancel`, { method: "POST", body: {} }); toast("해제했습니다"); reload(); }) : pill("잠김: " + d.locked_by, "warn"))
        : act("체크아웃", async () => { await api(`/api/docs/${id}/checkout`, { method: "POST", body: {} }); toast("체크아웃했습니다. 다른 사용자는 새 버전을 올릴 수 없습니다"); reload(); }));
      if (["draft", "rejected"].includes(d.status) && !d.workflow?.status?.startsWith("pending") && d.workflow?.status !== "pending") actions.push(act("결재 요청", () => submitDialog(d, reload), "primary"));
    }
    actions.push(act(d.following ? "구독 해제" : "구독", async () => { await api(`/api/docs/${id}/follow`, { method: "POST", body: { on: !d.following } }); toast(d.following ? "구독을 해제했습니다" : "새 버전·댓글·결재 알림을 받습니다"); reload(); }));
    if (!can("edit") && d.origin === "managed") actions.push(act("편집 권한 신청", () => requestDialog(d.doc_no, "editor")));
    actions.push(act(d.favorite ? "★ 즐겨찾기 해제" : "☆ 즐겨찾기", async () => { await api(`/api/docs/${id}/favorite`, { method: "POST", body: { on: !d.favorite } }); reload(); }));
    if (can("edit") && d.origin === "managed") actions.push(act("이동", () => moveDialog(d, reload)));
  }
  if (can("delete") && d.origin === "managed") actions.push(act("삭제", async () => { if (await confirmDialog(`'${d.title}' 을(를) 휴지통으로 옮길까요?`, "삭제", true)) { await api("/api/docs/" + id, { method: "DELETE" }); toast("휴지통으로 이동했습니다"); go("#/docs"); } }, "danger"));

  const head = el("div", {},
    el("div", { class: "hint" }, "문서함 / " + (d.folder_path || "")),
    el("div", { class: "page-h" }, el("h1", {}, d.title), statusPill(d.status), d.legal_hold ? pill("법적 보류", "bad") : "", d.locked_by ? pill("체크아웃 " + d.locked_by, "warn") : "", d.props?.no_download ? pill("다운로드 제한", "warn") : ""),
    el("div", { class: "hint" }, `${d.doc_no} · ${typeName(d.doc_type)} · 소유 ${userName(d.owner)} · 현행 ${d.effective_ver ? "v" + d.effective_ver : "없음"}${d.current_ver !== d.effective_ver ? ` / 최신 v${d.current_ver}` : ""}`),
    el("div", { class: "row sec" }, actions));
  if (d.readable === false) {
    view.replaceChildren(head, el("p", { class: "hint sec" }, d.note || "이 문서의 내용을 읽을 권한이 없습니다. 관리자는 권한과 삭제만 관리할 수 있습니다."),
      can("acl") ? el("div", { class: "card sec" }, aclTab(d, reload)) : "");
    return;
  }
  const alerts = d.analysis?.findings || [];
  const body = { info: infoTab, ask: askTab, versions: versionsTab, approval: approvalTab, comments: commentsTab, acl: aclTab, share: shareTab, history: historyTab }[tab] || infoTab;
  const panel = el("div", { role: "tabpanel" });
  view.replaceChildren(head,
    alerts.length ? el("div", { class: "card sec" }, el("b", { class: "bad" }, `업무 DB와 불일치 ${alerts.length}건`), alerts.map((f) => el("p", { class: "flush" }, pill(f.severity === "high" ? "높음" : "중간", f.severity === "high" ? "bad" : "warn"), " " + f.message))) : "",
    el("div", { class: "tabs", role: "tablist" }, TABS.filter(([t]) => t !== "acl" || can("acl") || d.origin === "crawled" && can("manage")).filter(([t]) => t !== "share" || can("share"))
      .map(([t, l]) => el("button", { role: "tab", "aria-selected": String(t === tab), onclick: () => goTab(t) }, l + (t === "comments" && d.comment_count ? ` (${d.comment_count})` : "")))),
    panel);
  panel.append(await body(d, reload));
});

function askTab(d) {
  const q = el("input", { id: "doc-q", placeholder: "이 문서에 대해 질문하세요 (예: 지체상금 조항은?)", "aria-label": "문서에 질문" });
  const out = el("div", { class: "sec" });
  const go1 = async () => {
    if (!q.value.trim()) return;
    out.replaceChildren(el("p", { class: "hint" }, "찾는 중..."));
    try {
      const a = await api(`/api/docs/${d.id}/ask`, { method: "POST", body: { question: q.value } });
      out.replaceChildren(el("div", { class: "a" + (a.refused ? " refused" : "") }, a.answer.split("\n").map((l) => el("p", {}, l)),
        el("div", { class: "hint" }, a.refused ? (d.status === "approved" ? "이 문서에서 근거를 찾지 못했습니다" : "결재가 끝나 승인된 버전만 AI 답변에 쓰입니다") : `${a.evidence.length}건의 근거 · 이 문서${a.evidence.some((e) => e.kind === "row") ? "와 연결된 업무 DB 행" : ""}만 사용`)),
      ...a.evidence.map((e) => el("div", { class: "ev " + e.kind }, el("b", {}, `[${e.id}] ${e.title}`), el("div", { class: "ref" }, e.ref), el("div", { class: "t" }, e.text),
        e.masked_columns.length ? el("div", { class: "mask" }, "권한 없음으로 가려진 컬럼: " + e.masked_columns.join(", ")) : "")));
    } catch (e) { out.replaceChildren(el("p", { class: "err" }, e.message)); }
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); go1(); } });
  return el("div", {}, el("p", { class: "hint" }, "이 문서의 내용만으로 답합니다. 내가 볼 수 없는 내용은 답에 쓰이지 않습니다."),
    el("div", { class: "row" }, el("div", { class: "grow" }, q), el("button", { class: "primary", onclick: go1 }, icon("sparkles", 16), "질문")), out);
}

/** Body preview: Markdown documents are rendered, everything else is plain text. Confidential documents carry a viewer watermark. */
function bodyView(d) {
  const cur = d.versions?.find((v) => v.ver === (d.viewing_ver || d.effective_ver)) || d.versions?.[0];
  const isMd = /\.md$/i.test(cur?.filename || "") || d.origin === "crawled" && /\.md$/i.test(d.source || "");
  const box = isMd ? el("div", { class: "body-md card md-wrap" }, renderMd(d.text)) : el("pre", { class: "body" }, d.text);
  if ((d.level || 0) >= 2) {
    const mark = `${state.me.name} · ${new Date().toISOString().slice(0, 10)}`;
    box.classList.add("wm-host");
    box.append(el("div", { class: "wm", "aria-hidden": "true" }, Array.from({ length: 12 }, () => el("span", {}, mark))));
  }
  return box;
}

// ---------- dialogs ----------
function newVersionDialog(d, reload) {
  const f = el("input", { type: "file", id: "nv-file" }), c = el("input", { id: "nv-comment", placeholder: "변경 내용 (선택)" });
  return dialog("새 버전 올리기", () => el("div", {}, el("p", { class: "hint" }, `v${d.current_ver} 다음 버전으로 저장됩니다. ` + (state.meta.doc_types.find((t) => t.code === d.doc_type)?.approval_required ? "결재가 끝나기 전까지 이전 승인본이 계속 현행입니다." : "승인이 필요 없는 유형이라 즉시 현행이 됩니다.")),
    field("파일", f), field("변경 내용", c)), [{ label: "취소", value: false }, { label: "올리기", primary: true, run: async () => {
    if (!f.files[0]) throw new Error("파일을 선택하세요");
    await api(`/api/docs/${d.id}/versions`, { method: "POST", body: { filename: f.files[0].name, content_b64: await fileToB64(f.files[0]), comment: c.value } });
    toast("새 버전을 올렸습니다"); reload(); } }]);
}
function submitDialog(d, reload) {
  const type = state.meta.doc_types.find((t) => t.code === d.doc_type);
  const checks = state.meta.users.filter((u) => u.id !== state.me.id && u.id !== d.owner).map((u) => {
    const cb = el("input", { type: "checkbox", value: u.id, checked: (type?.default_approvers || []).includes(u.id), id: "ap-" + u.id });
    return el("label", { class: "inline" }, cb, `${u.name} (${u.dept || u.id})`);
  });
  const mode = el("select", {}, [["sequential", "순차 (위에서부터 한 명씩)"], ["parallel", "병렬 (모두 동시에, 전원 승인)"]].map(([v, l]) => el("option", { value: v }, l)));
  const note = el("input", { placeholder: "결재 요청 메모 (선택)" });
  return dialog("결재 요청", () => el("div", {}, el("p", { class: "hint" }, "선택한 순서대로 결재합니다. 체크한 순서는 목록 순서입니다. 승인되면 현행 버전으로 게시되고 AI 답변·검색에 반영됩니다."), el("label", {}, "결재자"), checks, field("방식", mode), field("메모", note)),
    [{ label: "취소", value: false }, { label: "요청", primary: true, run: async () => {
      const approvers = checks.map((l) => l.firstChild).filter((c) => c.checked).map((c) => c.value);
      if (!approvers.length) throw new Error("결재자를 선택하세요");
      await api(`/api/docs/${d.id}/submit`, { method: "POST", body: { approvers, mode: mode.value, note: note.value } }); toast("결재를 요청했습니다"); go(`#/doc/${d.id}?tab=approval`); } }]);
}
async function moveDialog(d, reload) {
  const tree = await api("/api/dms/folders");
  const sel = el("select", {}, folderOptions(tree, { registerable: true }).map((o) => el("option", { value: o.id, selected: o.id === d.folder_id }, o.label)));
  return dialog("문서 이동", () => el("div", {}, field("대상 폴더", sel), el("p", { class: "hint" }, "이동하면 대상 폴더의 상속 권한이 적용되어 열람 가능한 사람이 바뀔 수 있습니다.")),
    [{ label: "취소", value: false }, { label: "이동", primary: true, run: async () => { await api(`/api/docs/${d.id}/move`, { method: "POST", body: { folder_id: Number(sel.value) } }); toast("이동했습니다"); reload(); } }]);
}
function metaDialog(d, reload) {
  const title = el("input", { id: "m-title", value: d.title }), tags = el("input", { id: "m-tags", value: d.tags.join(", ") }), desc = el("textarea", { id: "m-desc" }, d.description || "");
  const props = el("textarea", { id: "m-props", placeholder: "항목: 값 (한 줄에 하나)" }, Object.entries(d.props || {}).map(([k, v]) => `${k}: ${v}`).join("\n"));
  const exp = el("input", { type: "date", id: "m-exp", value: d.expires_at || "" });
  const nodl = el("input", { type: "checkbox", id: "m-nodl", checked: !!d.props?.no_download });
  props.value = Object.entries(d.props || {}).filter(([k]) => k !== "no_download").map(([k, v]) => `${k}: ${v}`).join("\n");
  return dialog("문서 정보 편집", () => el("div", {}, field("제목", title), field("태그", tags), field("설명", desc), field("사용자 정의 속성", props, "예: 고객사: 제주관광공사"), field("만료일", exp, "만료되면 본문이 검색·AI 답변에서 제외됩니다"),
    el("label", { class: "inline" }, nodl, "다운로드 제한 (파일 반출·외부 공유 다운로드·ZIP 내보내기 차단. 화면 열람은 가능)")),
    [{ label: "취소", value: false }, { label: "저장", primary: true, run: async () => {
      const pr = {}; for (const line of props.value.split("\n")) { const i = line.indexOf(":"); if (i > 0) pr[line.slice(0, i).trim()] = line.slice(i + 1).trim(); }
      if (nodl.checked) pr.no_download = "1";
      await api("/api/docs/" + d.id, { method: "PUT", body: { title: title.value, tags: tags.value.split(",").map((x) => x.trim()).filter(Boolean), description: desc.value, props: pr, expires_at: exp.value || null } }); toast("저장했습니다"); reload(); } }]);
}

// ---------- tabs ----------
function infoTab(d, reload) {
  const a = d.analysis || {};
  const dl = el("dl", { class: "dl" });
  const add = (k, v) => v !== "" && v != null && dl.append(el("dt", {}, k), el("dd", {}, v));
  add("문서번호", d.doc_no); add("유형", typeName(d.doc_type)); add("상태", statusPill(d.status)); add("폴더", d.folder_path); add("소유자", userName(d.owner)); add("등록", fmtDate(d.created)); add("수정", fmtDate(d.updated));
  add("보안등급", "L" + d.level); add("태그", d.tags.length ? el("span", {}, d.tags.map((t) => pill("#" + t))) : "-"); add("설명", d.description);
  for (const [k, v] of Object.entries(d.props || {})) add(k, v);
  add("보존기간", d.retention_until ? `${d.retention_until} 까지 (이 기간에는 삭제할 수 없습니다)` : "없음"); add("만료일", d.expires_at || "없음");
  const lifecycle = [];
  if (d.origin === "managed" && can(d, "edit")) lifecycle.push(el("button", { class: "sm", onclick: () => metaDialog(d, reload) }, "정보 편집"));
  if (state.me.is_admin) lifecycle.push(el("button", { class: "sm " + (d.legal_hold ? "danger" : ""), onclick: async () => {
    const reason = el("input", { placeholder: "사유" });
    if (await dialog(d.legal_hold ? "법적 보류 해제" : "법적 보류 설정", () => field("사유", reason, "법적 보류 중에는 삭제·만료·영구삭제가 모두 차단됩니다"), [{ label: "취소", value: false }, { label: "확인", primary: true }])) {
      await api(`/api/docs/${d.id}/hold`, { method: "POST", body: { hold: !d.legal_hold, reason: reason.value } }); toast("변경했습니다"); reload(); } } }, d.legal_hold ? "법적 보류 해제" : "법적 보류"));
  const checks = a.checks?.length ? el("div", { class: "sec" }, el("h3", {}, "업무 DB 대조"), table(["항목", "문서", "DB", "결과", "DB 행"], a.checks.map((c) => [c.field, typeof c.doc === "number" ? fmtNum(c.doc) : c.doc,
    typeof c.db === "number" ? fmtNum(c.db) : c.db, pill(c.status === "ok" ? "일치" : "불일치", c.status === "ok" ? "ok" : "bad"), c.row]))) : "";
  const fields = Object.keys(a.fields || {}).length ? el("div", { class: "sec" }, el("h3", {}, "자동 추출 항목"), el("dl", { class: "dl" }, Object.entries(a.fields).flatMap(([k, v]) =>
    [el("dt", {}, { contract_no: "계약번호", project: "행사", amount: "계약금액", due_date: "납기" }[k] || k), el("dd", {}, typeof v === "number" ? fmtNum(v) + "원" : v)]))) : "";
  const rows = d.linked_rows?.length ? el("div", { class: "sec" }, el("h3", {}, "연결된 업무 DB 행"), d.linked_rows.map((r) => el("div", { class: "ev row" }, el("b", {}, `${r.title} · ${r.key}`),
    el("div", { class: "t" }, Object.entries(r.values).map(([k, v]) => `${k}: ${v}`).join("\n")), r.masked.length ? el("div", { class: "mask" }, "권한 없음으로 가려진 컬럼: " + r.masked.join(", ")) : ""))) : "";
  return el("div", {}, dl, el("div", { class: "row sec" }, lifecycle), a.summary ? el("div", { class: "sec" }, el("h3", {}, `자동 요약 · ${a.category}`), el("p", { class: "flush" }, a.summary)) : "",
    fields, checks, rows, relatedBlock(d, reload), imagePreview(d), el("div", { class: "sec" }, el("h3", {}, `본문 미리보기${d.viewing_ver ? " (v" + d.viewing_ver + ")" : ""}`), d.text ? bodyView(d) : empty("미리볼 텍스트가 없습니다 (이미지 등 텍스트가 없는 형식이거나 열람 가능한 버전이 없습니다)")));
}
const can = (d, p) => (d.perms || []).includes(p);


function relatedBlock(d, reload) {
  const add = el("input", { id: "rel-add", placeholder: "문서번호 (예: GEN-2026-0001)", "aria-label": "연결할 문서번호" });
  return el("div", { class: "sec" }, el("h3", {}, "관련 문서"),
    d.related?.length ? table(["문서", "상태", ""], d.related.map((r) => [el("button", { class: "link", onclick: () => go("#/doc/" + r.id) }, `${r.doc_no} ${r.title}`), statusPill(r.status),
      can(d, "edit") ? el("button", { class: "sm", "aria-label": "연결 해제", onclick: async () => { await api(`/api/docs/${d.id}/links/${r.id}`, { method: "DELETE" }); toast("연결을 해제했습니다"); reload(); } }, "해제") : ""])) : empty("연결된 문서가 없습니다"),
    can(d, "edit") ? el("div", { class: "row" }, add, el("button", { class: "sm", id: "rel-go", onclick: async () => { try { await api(`/api/docs/${d.id}/links`, { method: "POST", body: { other: add.value } }); toast("연결했습니다"); reload(); } catch (e) { fail(e); } } }, "연결")) : "");
}

const IMG = /\.(png|jpe?g|gif|webp)$/i;
/** Inline preview for raster images. Only when the viewer may download: a preview must not bypass a download restriction. */
function imagePreview(d) {
  const ver = (d.versions || []).find((v) => v.ver === d.viewing_ver);
  if (!ver || !IMG.test(ver.filename) || !can(d, "download")) return "";
  const box = el("div", { class: "sec" }, el("h3", {}, "이미지 미리보기"), el("p", { class: "hint" }, "불러오는 중..."));
  api(`/api/docs/${d.id}/download?ver=${ver.ver}`, { raw: true }).then(async (r) => { const blob = await r.blob();
    box.replaceChildren(el("h3", {}, "이미지 미리보기"), el("img", { class: "preview", alt: d.title, src: URL.createObjectURL(blob) })); }).catch(() => box.remove());
  return box;
}

async function versionsTab(d, reload) {
  const vs = await api(`/api/docs/${d.id}/versions`);
  if (!vs.length) return empty("열람할 수 있는 버전이 없습니다");
  let a = null, b = null;
  const out = el("div", { class: "sec" });
  const rows = vs.map((v) => [el("b", {}, v.label + (v.effective ? " ★현행" : "")), v.filename, fmtSize(v.size), statusPill(v.status), userName(v.created_by), fmtDate(v.created), v.comment || "",
    el("div", { class: "row" },
      v.can_download ? el("button", { class: "sm", onclick: () => saveBlob(`/api/docs/${d.id}/download?ver=${v.ver}`, {}, v.filename).catch(fail) }, "다운로드") : "",
      can(d, "edit") && v.ver !== d.current_ver ? el("button", { class: "sm", onclick: async () => { if (await confirmDialog(`${v.label} 내용으로 새 버전을 만들까요?`, "복원")) { await api(`/api/docs/${d.id}/restore`, { method: "POST", body: { ver: v.ver } }); toast("복원했습니다"); reload(); } } }, "복원") : "",
      el("label", { class: "inline" }, el("input", { type: "radio", name: "va", onchange: () => (a = v.ver), "aria-label": v.label + " 기준" }), "A"),
      el("label", { class: "inline" }, el("input", { type: "radio", name: "vb", onchange: () => (b = v.ver), "aria-label": v.label + " 비교" }), "B"))]);
  return el("div", {}, table(["버전", "파일", "크기", "상태", "작성자", "일시", "변경 내용", ""], rows),
    el("div", { class: "row sec" }, el("button", { onclick: async () => {
      if (!a || !b) return toast("A와 B 버전을 선택하세요", "bad");
      const r = await api(`/api/docs/${d.id}/diff?a=${a}&b=${b}`);
      out.replaceChildren(el("p", {}, pill(`추가 ${r.added}줄`, "ok"), " ", pill(`삭제 ${r.removed}줄`, r.removed ? "bad" : ""), r.same ? " 내용이 같습니다" : "", r.note ? " " + r.note : ""),
        el("div", { class: "diff" }, r.lines.map((l) => el("div", { class: l.tag }, (l.tag === "add" ? "+ " : l.tag === "del" ? "- " : "  ") + l.text))), r.truncated ? el("p", { class: "hint" }, "일부만 표시했습니다") : "");
    } }, "A ↔ B 비교")), out);
}

async function approvalTab(d, reload) {
  const w = d.workflow;
  const node = el("div", {});
  if (!w) node.append(empty("결재 이력이 없습니다."));
  else {
    const comment = el("input", { id: "ap-comment", placeholder: "의견 (반려 시 사유 필수)" });
    node.append(el("p", {}, statusPill(w.status === "pending" ? "in_review" : w.status === "approved" ? "approved" : w.status === "rejected" ? "rejected" : "draft"), ` v${w.ver} · ${w.mode === "parallel" ? "병렬" : "순차"} · 요청 ${userName(w.requester)} · ${fmtDate(w.created)}`, w.note ? ` · ${w.note}` : ""),
      el("div", { class: "steps" }, w.steps.map((s) => el("div", { class: "step" + (s.active ? " active" : "") }, el("b", {}, `${s.ord + 1}. ${userName(s.approver)}`),
        pill({ pending: s.active ? "처리 대기" : "대기", approved: "승인", rejected: "반려", skipped: "건너뜀" }[s.status] || s.status, { approved: "ok", rejected: "bad", pending: s.active ? "info" : "" }[s.status] || ""),
        s.delegated_from ? el("span", { class: "hint" }, `(${userName(s.delegated_from)} 위임)`) : "", el("span", { class: "hint grow" }, [s.comment, fmtDate(s.acted_at)].filter(Boolean).join(" · "))))));
    if (w.can_act) node.append(el("div", { class: "card sec" }, field("의견", comment), el("div", { class: "row sec" },
      el("button", { class: "primary", onclick: async () => { try { await api(`/api/docs/${d.id}/approve`, { method: "POST", body: { comment: comment.value } }); toast("승인했습니다"); go("#/tasks"); } catch (e) { fail(e); } } }, "승인"),
      el("button", { class: "danger", onclick: async () => { try { await api(`/api/docs/${d.id}/reject`, { method: "POST", body: { comment: comment.value } }); toast("반려했습니다"); go("#/tasks"); } catch (e) { fail(e); } } }, "반려"),
      el("button", { onclick: () => { const sel = el("select", {}, state.meta.users.filter((u) => u.id !== state.me.id && u.id !== w.requester && u.id !== d.owner).map((u) => el("option", { value: u.id }, `${u.name} (${u.dept || u.id})`)));
        dialog("결재 위임", () => field("위임할 사용자", sel), [{ label: "취소", value: false }, { label: "위임", primary: true, run: async () => { await api(`/api/docs/${d.id}/delegate`, { method: "POST", body: { to: sel.value, comment: comment.value } }); toast("위임했습니다"); go("#/tasks"); } }]); } }, "위임"))));
    if (w.can_cancel) node.append(el("div", { class: "row sec" }, el("button", { onclick: async () => { if (await confirmDialog("결재 요청을 회수할까요?", "회수")) { await api(`/api/docs/${d.id}/approval/cancel`, { method: "POST", body: {} }); toast("회수했습니다"); reload(); } } }, "결재 회수")));
  }
  return node;
}

async function commentsTab(d, reload) {
  const list = await api(`/api/docs/${d.id}/comments`);
  const ta = el("textarea", { id: "cm-text", placeholder: "댓글 (@사용자ID 로 언급하면 알림이 갑니다. 문서를 읽을 수 있는 사람만 알림을 받습니다)", maxlength: "2000" });
  const send = async (parent) => { const r = await api(`/api/docs/${d.id}/comments`, { method: "POST", body: { text: ta.value, parent_id: parent || null } }); toast("등록했습니다"); reload(); return r; };
  const view = (c) => el("div", { class: "cmt" + (c.parent_id ? " reply" : "") }, el("div", { class: "row" }, el("b", {}, userName(c.user)), el("span", { class: "hint grow" }, fmtDate(c.ts)),
    !c.parent_id ? el("button", { class: "sm", onclick: () => { ta.focus(); ta.dataset.parent = c.id; ta.placeholder = `${userName(c.user)} 님에게 답글`; } }, "답글") : "",
    c.can_delete ? el("button", { class: "sm danger", onclick: async () => { await api(`/api/docs/${d.id}/comments/${c.id}`, { method: "DELETE" }); reload(); } }, "삭제") : ""), el("div", { class: "t" }, c.text));
  const top = list.filter((c) => !c.parent_id);
  return el("div", { class: "stack" }, ...(top.length ? top.flatMap((c) => [view(c), ...list.filter((r) => r.parent_id === c.id).map(view)]) : [empty("댓글이 없습니다")]),
    el("div", { class: "card" }, ta, el("div", { class: "row end sec" }, el("button", { class: "primary", onclick: () => send(Number(ta.dataset.parent) || null).catch(fail) }, "댓글 등록"))));
}

function aclTab(d, reload) {
  const entries = d.entries || [];
  const editor = aclEditor(entries);
  const inherit = el("input", { type: "checkbox", id: "acl-inh", checked: d.inherit !== false });
  const level = el("input", { type: "number", id: "acl-level", min: "0", max: "99", value: String(d.level ?? 0) });
  const eff = (d.effective_entries || []).filter((e) => !entries.some((x) => x.type === e.type && x.id === e.id));
  return el("div", { class: "stack" },
    el("p", { class: "hint" }, d.origin === "crawled" ? "원본 폴더에서 수집된 문서입니다. 관리자가 지정한 권한이 manifest 규칙보다 우선합니다." : "열람 = 읽기·다운로드·댓글, 편집 = 새 버전·정보 수정·결재 요청, 관리 = 권한·삭제·외부 공유. 소유자는 항상 관리 권한을 가집니다. 승인 전(작성 중·결재 중·반려) 문서는 편집 권한자와 결재자만 읽을 수 있습니다."),
    d.origin === "managed" ? el("label", { class: "inline" }, inherit, "폴더 권한 상속") : "",
    eff.length ? el("div", {}, el("h3", {}, "상속된 권한 (폴더)"), table(["대상", "ID", "권한"], eff.map((e) => [{ user: "사용자", dept: "부서", role: "역할", all: "전체 직원", deny: "제외" }[e.type], e.id, { viewer: "열람", editor: "편집", manager: "관리" }[e.role] || "-"]))) : "",
    el("div", {}, el("h3", {}, "이 문서에 직접 지정한 권한"), editor), field("보안등급 (이 값 이상의 등급을 가진 사용자만 열람)", level),
    d.read_acl ? el("p", { class: "hint" }, `현재 열람 범위: ${[...(d.read_acl.roles || []).map((r) => "역할 " + r), ...(d.read_acl.depts || []).map((r) => "부서 " + r), ...(d.read_acl.users || []).map((r) => "사용자 " + r)].join(", ") || "등급을 충족한 전체 직원"} / 제외: ${(d.read_acl.deny_users || []).join(", ") || "없음"}`) : "",
    el("div", { class: "row" }, el("button", { class: "primary", onclick: async () => { try { await api(`/api/docs/${d.id}/acl`, { method: "PUT", body: { entries: editor.get(), inherit: inherit.checked, level: Number(level.value) } }); toast("권한을 저장했습니다"); reload(); } catch (e) { fail(e); } } }, "권한 저장")));
}

async function shareTab(d, reload) {
  if (!state.meta.share.enabled) return empty("외부 공유가 비활성화되어 있습니다 (설정 dms.external_share.enabled).");
  const shares = await api(`/api/docs/${d.id}/shares`);
  const days = el("input", { type: "number", id: "sh-days", min: "1", max: String(state.meta.share.max_days), value: "7" }), maxdl = el("input", { type: "number", id: "sh-max", min: "0", value: "0" });
  const pw = el("input", { type: "password", id: "sh-pw", autocomplete: "new-password", placeholder: "선택 (6자 이상)" }), perm = el("select", { id: "sh-perm" }, [["view", "보기만"], ["download", "다운로드 허용"]].map(([v, l]) => el("option", { value: v }, l)));
  const note = el("input", { id: "sh-note", placeholder: "메모 (선택)" });
  return el("div", { class: "stack" },
    el("p", { class: "hint" }, `로그인 없이 열 수 있는 링크입니다. 승인(게시)된 현행 버전만, 보안등급 L${state.meta.share.max_level} 이하 문서만 공유할 수 있고 새 버전이 승인되면 링크는 자동으로 닫힙니다. 개인정보는 가려서 보여 줍니다.`),
    el("div", { class: "card" }, el("div", { class: "grid2" }, field("유효 기간(일)", days), field("최대 다운로드 (0=무제한)", maxdl), field("권한", perm), field("비밀번호", pw)), field("메모", note),
      el("div", { class: "row end sec" }, el("button", { class: "primary", id: "sh-create", onclick: async () => {
        try {
          const r = await api(`/api/docs/${d.id}/shares`, { method: "POST", body: { days: Number(days.value), max_downloads: Number(maxdl.value), password: pw.value || null, perm: perm.value, note: note.value } });
          const url = location.origin + r.path;
          await dialog("공유 링크가 만들어졌습니다", () => el("div", {}, el("p", { class: "bad" }, "이 링크는 지금 한 번만 표시됩니다. 서버에는 해시만 저장됩니다."),
            el("input", { id: "sh-url", readonly: true, value: url, onfocus: (e) => e.target.select() }), el("p", { class: "hint" }, `유효기간 ${fmtDay(r.expires)} · ${r.perm === "download" ? "다운로드 허용" : "보기만"}${r.password ? " · 비밀번호 필요" : ""}`)),
            [{ label: "복사", run: async () => { try { await navigator.clipboard.writeText(url); toast("복사했습니다"); } catch { $("#sh-url").select(); toast("선택했습니다. Ctrl+C 로 복사하세요"); } return false; } }, { label: "닫기", primary: true }]);
          reload();
        } catch (e) { fail(e); } } }, "링크 만들기"))),
    shares.length ? table(["#", "만든 사람", "만든 날", "만료", "권한", "열람", "다운로드", "상태", ""], shares.map((s) => [s.id, userName(s.created_by), fmtDay(s.created), fmtDay(s.expires), s.perm === "download" ? "다운로드" : "보기", s.views,
      `${s.downloads}${s.max_downloads ? " / " + s.max_downloads : ""}`, s.revoked ? pill("폐지", "bad") : s.expired ? pill("만료") : pill(s.has_password ? "사용 중 (비밀번호)" : "사용 중", "ok"),
      !s.revoked && !s.expired ? el("button", { class: "sm danger", onclick: async () => { await api(`/api/docs/${d.id}/shares/${s.id}`, { method: "DELETE" }); toast("폐지했습니다"); reload(); } }, "폐지") : ""])) : empty("만든 링크가 없습니다"));
}

function historyTab(d) {
  return d.history.length ? table(["일시", "사용자", "작업", "내용"], d.history.map((h) => [fmtDate(h.ts), userName(h.user.replace(/^share:/, "외부 공유 #")), ACTION[h.action] || h.action, h.detail])) : empty("이력이 없습니다");
}
