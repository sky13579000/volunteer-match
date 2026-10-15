import { STATUS, api, confirmDialog, dialog, el, empty, fail, field, fmtDate, fmtDay, fmtNum, fmtSize, go, pill, route, saveBlob, setNavBadge, state, statusPill, table, toast, typeName, userName } from "./core.js";
import { icon } from "./icons.js";
import { registerDialog } from "./v-docs.js";

const open = (id) => go("#/doc/" + id);
const docLink = (d, label) => el("button", { class: "link", onclick: () => open(d.id || d.doc_id) }, label || d.title);

// ---------------- dashboard ----------------
route(/^\/dashboard$/, async (view) => {
  const [d, feed, follows] = await Promise.all([api("/api/dms/dashboard"), api("/api/dms/activity?limit=8").catch(() => ({ items: [] })), api("/api/dms/follows").catch(() => ({ items: [] }))]);
  const c = d.counts;
  const ACT = { registered: "등록했습니다", new_version: "새 버전을 올렸습니다", restored: "이전 버전을 복원했습니다", submitted: "결재를 요청했습니다", approved_step: "결재를 승인했습니다", rejected: "반려했습니다",
    commented: "댓글을 남겼습니다", moved: "문서를 이동했습니다", acl_changed: "권한을 변경했습니다", meta_changed: "정보를 수정했습니다", deleted: "삭제했습니다", expired: "만료되었습니다", checked_out: "체크아웃했습니다", legal_hold_on: "법적 보류를 지정했습니다", legal_hold_off: "법적 보류를 해제했습니다", access_requested: "접근 권한을 신청했습니다", access_granted: "접근 권한을 승인했습니다", access_denied: "접근 권한 신청을 거절했습니다",
    share_created: "외부 공유 링크를 만들었습니다", share_revoked: "외부 공유 링크를 폐지했습니다", linked: "관련 문서를 연결했습니다", unlinked: "문서 연결을 해제했습니다", workflow_cancelled: "결재를 회수했습니다", delegated: "결재를 위임했습니다",
    restored_from_trash: "휴지통에서 복원했습니다", expiry_changed: "만료일을 변경했습니다", checkout_cancelled: "체크아웃을 해제했습니다", comment_deleted: "댓글을 삭제했습니다" };
  const kpi = (n, label, ic, hot, href) => el("a", { class: "kpi" + (hot && n ? " hot" : ""), href: href || "#/dashboard" }, icon(ic, 26), el("b", {}, fmtNum(n)), el("span", {}, label));
  const bars = (items, key, label) => { const max = Math.max(1, ...items.map((i) => i.count)); return el("div", { class: "bars" }, items.length ? items.map((i) => el("div", { class: "bar" }, el("span", {}, label(i)), el("div", { class: "tr" }, el("i", { "data-w": Math.round((i.count / max) * 100) })), el("span", {}, i.count))) : empty("데이터 없음", "chart")); };
  const hour = new Date().getHours();
  const greet = hour < 12 ? "좋은 아침입니다" : hour < 18 ? "안녕하세요" : "수고 많으셨습니다";
  const todo = c.tasks ? `처리할 결재가 ${c.tasks}건 있습니다.` : c.expiring_soon ? `곧 만료되는 문서가 ${c.expiring_soon}건 있습니다.` : "오늘 처리할 긴급 업무가 없습니다.";
  view.replaceChildren(
    el("section", { class: "welcome" }, el("h1", {}, `${greet}, ${state.me.name}님`), el("p", {}, todo),
      el("div", { class: "row" }, el("button", { class: "primary", onclick: () => registerDialog(1).catch(fail) }, icon("plus", 16), "새 문서 등록"),
        el("button", { onclick: () => go("#/new") }, icon("file", 16), "양식으로 작성"), el("button", { onclick: () => go("#/chat") }, icon("sparkles", 16), "AI에게 질문"), el("button", { onclick: () => go("#/docs") }, icon("folder", 16), "문서함 열기"))),
    el("div", { class: "kpis" }, kpi(c.readable, "열람 가능한 문서", "folder", false, "#/docs"), kpi(c.mine, "내가 올린 문서", "user", false, "#/docs?mine=1"), kpi(c.drafts, "작성 중", "edit", false, "#/docs?mine=1&status=draft"),
      kpi(c.in_review, "결재 진행 중", "clock", false, "#/docs?mine=1&status=in_review"), kpi(c.tasks, "내가 처리할 결재", "task", true, "#/tasks"), kpi(c.expiring_soon, "만료 임박", "alert", true, "#/docs?mine=1"), kpi(c.unread, "읽지 않은 알림", "bell", true, "#/notifications")),
    el("div", { class: "grid2 sec" },
      el("div", { class: "card" }, el("h3", {}, icon("task", 15), "내가 처리할 결재"), d.tasks.length ? table(["문서", "요청자", "요청일"], d.tasks.map((t) => [docLink(t, `${t.doc_no} ${t.title}`), userName(t.requester), fmtDay(t.since)])) : empty("처리할 결재가 없습니다", "task")),
      el("div", { class: "card" }, el("h3", {}, icon("upload", 15), "내가 올린 결재"), d.requests.length ? table(["문서", "상태"], d.requests.map((t) => [docLink(t, `${t.doc_no} ${t.title}`), pill({ pending: "진행 중", approved: "승인", rejected: "반려", cancelled: "회수" }[t.status] || t.status, { approved: "ok", rejected: "bad", pending: "info" }[t.status] || "")])) : empty("올린 결재가 없습니다", "upload")),
      el("div", { class: "card" }, el("h3", {}, icon("alert", 15), "만료 예정 (30일 이내)"), d.expiring.length ? table(["문서", "만료일"], d.expiring.map((t) => [docLink(t), t.expires_at])) : empty("만료 예정 문서가 없습니다", "calendar")),
      el("div", { class: "card" }, el("h3", {}, icon("history", 15), "최근 본 문서"), d.recent.length ? table(["문서", "수정일"], d.recent.map((t) => [docLink(t), fmtDay(t.updated)])) : empty("아직 없습니다", "history")),
      el("div", { class: "card" }, el("h3", {}, icon("star", 15), "즐겨찾기"), d.favorites.length ? table(["문서", "상태"], d.favorites.map((t) => [docLink(t), statusPill(t.status)])) : empty("즐겨찾기한 문서가 없습니다", "star")),
      el("div", { class: "card" }, el("h3", {}, icon("history", 15), "최근 활동"), feed.items.length ? el("div", { class: "feed" }, feed.items.map((a) => el("div", { class: "feed-i" }, el("span", { class: "avatar" }, (a.user_name || "?").slice(0, 1)),
        el("div", { class: "feed-t" }, el("b", {}, a.user_name), ` ${ACT[a.action] || a.action} `, docLink({ id: a.doc_id }, `${a.doc_no} ${a.title}`), el("div", { class: "hint" }, fmtDate(a.ts)))))) : empty("최근 활동이 없습니다", "history")),
      el("div", { class: "card" }, el("h3", {}, icon("eye", 15), "구독 중인 문서"), follows.items.length ? table(["문서", "상태"], follows.items.map((t) => [docLink(t), statusPill(t.status)])) : empty("문서 화면의 '구독'을 누르면 변경 알림을 받습니다", "eye")),
      el("div", { class: "card" }, el("h3", {}, icon("chart", 15), "유형별 문서"), bars(d.by_type, "code", (i) => i.name), el("h3", { class: "sec" }, "상태별"), bars(d.by_status, "status", (i) => STATUS[i.status]?.[0] || i.status),
        el("p", { class: "hint sec" }, `저장 용량 ${fmtSize(d.storage_bytes)} (${d.storage_scope === "all" ? "전체" : "내 문서"})`))));
  for (const i of view.querySelectorAll("i[data-w]")) i.style.width = i.dataset.w + "%";     // widths set via CSSOM (the CSP forbids inline style attributes)
});

// ---------------- approvals ----------------
route(/^\/tasks$/, async (view) => {
  const [t, ab] = await Promise.all([api("/api/dms/tasks"), api("/api/dms/absence")]);
  const absence = ab.absence && ab.absence.to_ >= new Date().toISOString().slice(0, 10) ? ab.absence : null;
  const setAbsence = async () => {
    const dep = el("select", { id: "ab-dep" }, state.meta.users.filter((u) => u.id !== state.me.id).map((u) => el("option", { value: u.id }, `${u.name} (${u.dept || u.id})`)));
    const from = el("input", { type: "date", id: "ab-from", value: new Date().toISOString().slice(0, 10) }), to = el("input", { type: "date", id: "ab-to" }), why = el("input", { id: "ab-why", placeholder: "사유 (선택)" });
    await dialog("부재 설정 · 대결자 지정", () => el("div", {}, el("p", { class: "hint" }, "부재 기간 동안 내게 온 결재(진행 중 포함)는 대결자에게 넘어가며, 결재 이력에 '대결'로 남습니다. 최대 180일."), field("대결자", dep), el("div", { class: "grid2" }, field("시작일", from), field("종료일", to)), field("사유", why)),
      [{ label: "취소", value: false }, { label: "설정", primary: true, run: async () => { const r = await api("/api/dms/absence", { method: "PUT", body: { deputy: dep.value, from: from.value, to: to.value, reason: why.value } }); toast(`대결 설정 완료 (진행 중 ${r.reassigned}건 이관)`); go("#/tasks"); } }]);
  };
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "결재함"), el("span", { class: "grow" }),
    absence ? el("span", { class: "hint" }, `부재 중: ${absence.frm} ~ ${absence.to_} · 대결 ${userName(absence.deputy)}`) : "",
    absence ? el("button", { onclick: async () => { await api("/api/dms/absence", { method: "DELETE" }); go("#/tasks"); } }, "부재 해제") : el("button", { id: "ab-set", onclick: () => setAbsence().catch(fail) }, icon("clock", 16), "부재 설정")),
    el("div", { class: "card" }, el("h3", {}, "내가 처리할 결재"), t.tasks.length ? table(["문서번호", "제목", "요청자", "방식", "메모", "요청일", ""], t.tasks.map((x) => [el("span", { class: "ref" }, x.doc_no), x.title, userName(x.requester),
      x.mode === "parallel" ? "병렬" : "순차", x.note || "", fmtDate(x.since), el("button", { class: "sm primary", onclick: () => go(`#/doc/${x.doc_id}?tab=approval`) }, "검토하기")])) : empty("처리할 결재가 없습니다")),
    el("div", { class: "card sec" }, el("h3", {}, "내가 올린 결재"), t.requests.length ? table(["문서번호", "제목", "버전", "상태", "요청일"], t.requests.map((x) => [el("span", { class: "ref" }, x.doc_no), docLink(x), "v" + x.ver,
      pill({ pending: "진행 중", approved: "승인", rejected: "반려", cancelled: "회수" }[x.status] || x.status, { approved: "ok", rejected: "bad", pending: "info" }[x.status] || ""), fmtDate(x.created)])) : empty("올린 결재가 없습니다")));
});

// ---------------- trash ----------------
route(/^\/trash$/, async (view) => {
  const items = await api("/api/dms/trash");
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "휴지통"), el("span", { class: "hint" }, `${state.meta.trash_days}일 후 자동 정리됩니다 (보존기간·법적 보류 문서는 제외)`)),
    items.length ? table(["문서번호", "제목", "삭제한 사람", "삭제일", "정리 예정", ""], items.map((x) => [el("span", { class: "ref" }, x.doc_no), x.title, userName(x.deleted_by), fmtDay(x.deleted_at), x.purge_after,
      el("div", { class: "row" }, el("button", { class: "sm", onclick: async () => { try { await api(`/api/docs/${x.id}/trash/restore`, { method: "POST", body: {} }); toast("복원했습니다"); go("#/trash"); } catch (e) { fail(e); } } }, "복원"),
        state.me.is_admin ? el("button", { class: "sm danger", onclick: async () => { if (await confirmDialog(`${x.doc_no} 을(를) 영구 삭제할까요? 되돌릴 수 없습니다.`, "영구 삭제", true)) { try { await api(`/api/docs/${x.id}/purge`, { method: "DELETE" }); toast("영구 삭제했습니다"); go("#/trash"); } catch (e) { fail(e); } } } }, "영구 삭제") : "")])) : empty("휴지통이 비어 있습니다"));
});

// ---------------- notifications ----------------
route(/^\/notifications$/, async (view) => {
  const n = await api("/api/dms/notifications");
  const prefs = await api("/api/dms/prefs");
  const KIND = { approval_request: ["결재 요청", "info"], approved: ["승인", "ok"], rejected: ["반려", "bad"], comment: ["댓글", ""], mention: ["언급", "info"], expiring: ["만료 예정", "warn"], expired: ["만료", "bad"], cancelled: ["회수", ""], follow_update: ["구독 문서", "info"], access_request: ["권한 신청", "warn"], access_granted: ["권한 승인", "ok"], access_denied: ["권한 거절", "bad"], privacy_alert: ["개인정보", "bad"], disposal_request: ["폐기 신청", "warn"], disposal_result: ["폐기 결과", "info"], cc_notice: ["참조", ""] };
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "알림"), el("span", { class: "grow" }),
    prefs.email_available ? el("label", { class: "inline" }, el("input", { type: "checkbox", id: "pref-email", checked: prefs.email, onchange: async (e) => { try { await api("/api/dms/prefs", { method: "PUT", body: { email: e.target.checked } }); toast(e.target.checked ? "메일 알림을 켰습니다" : "메일 알림을 껐습니다"); } catch (x) { fail(x); } } }), "메일로도 받기") : "",
    el("button", { onclick: async () => { await api("/api/dms/notifications/read", { method: "POST", body: {} }); go("#/notifications"); await refreshBell(); } }, "모두 읽음")),
    n.items.length ? el("div", { class: "stack" }, n.items.map((x) => el("div", { class: "card row", "data-unread": x.read ? "0" : "1" }, pill(...(KIND[x.kind] || [x.kind, ""])), el("span", { class: "grow" }, x.read ? x.text : el("b", {}, x.text)), el("span", { class: "hint" }, fmtDate(x.ts)),
      x.doc_id ? el("button", { class: "sm", onclick: async () => { await api("/api/dms/notifications/read", { method: "POST", body: { ids: [x.id] } }); refreshBell(); go(`#/doc/${x.doc_id}` + (x.kind === "approval_request" ? "?tab=approval" : "")); } }, "열기") : ""))) : empty("알림이 없습니다"));
});
export async function refreshBell() {
  try { const n = await api("/api/dms/notifications?unread=1"); const b = document.getElementById("bell-n"); b.textContent = String(n.unread); b.hidden = !n.unread; } catch { /* ignore */ }
  try { const t = await api("/api/dms/tasks"); setNavBadge("tasks", t.tasks.length); } catch { /* ignore */ }
  try { const a = await api("/api/dms/access-requests"); setNavBadge("access", a.to_decide.length); } catch { /* ignore */ }
}

// ---------------- automation alerts ----------------
route(/^\/alerts$/, async (view) => {
  const list = await api("/api/alerts");
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "문서 ↔ DB 불일치")), el("p", { class: "hint" }, "문서에서 자동 추출한 계약금액·납기를 업무 DB와 대조한 결과입니다. 본인이 읽을 수 있는 문서와 컬럼만 대조합니다."),
    list.length ? el("div", { class: "stack" }, list.map((a) => el("div", { class: "card" }, el("div", { class: "row" }, el("b", {}, a.title), el("span", { class: "ref" }, a.doc_no || a.source), el("span", { class: "grow" }), el("button", { class: "sm", onclick: () => open(a.doc_id) }, "문서 열기")),
      a.findings.map((f) => el("p", { class: "flush" }, pill(f.severity === "high" ? "높음" : "중간", f.severity === "high" ? "bad" : "warn"), " " + f.message, " ", el("span", { class: "ref" }, f.row)))))) : empty("불일치 항목이 없습니다"));
});

// ---------------- AI Q&A ----------------
route(/^\/chat$/, async (view) => {
  const key = "history:" + state.me.id;
  let hist = []; try { hist = JSON.parse(sessionStorage.getItem(key) || "[]"); } catch { /* ignore */ }
  const log = el("div"), ta = el("textarea", { id: "q", rows: "2", placeholder: "질문을 입력하세요 (Enter 전송, Shift+Enter 줄바꿈). 열람 권한이 있는 승인 문서와 업무 DB만 근거로 사용합니다." });
  const send = el("button", { class: "primary", id: "send" });
  const KIND = { doc: "문서", row: "DB", metric: "집계" };
  function cites(textv, box) {
    const p = el("p");
    for (const part of textv.split(/(\[E\d+\])/)) { const m = part.match(/^\[(E\d+)\]$/); if (!m) { p.append(part); continue; }
      p.append(el("button", { class: "cite", type: "button", onclick: () => { const t = box.querySelector(`[data-id="${m[1]}"]`); if (t) { box.open = true; t.scrollIntoView({ block: "center" }); t.classList.add("hl"); setTimeout(() => t.classList.remove("hl"), 1400); } } }, m[1])); }
    return p;
  }
  function draw(q, a) {
    const box = el("details");
    box.append(el("summary", {}, `근거 ${a.evidence.length}건`));
    for (const e of a.evidence) box.append(el("div", { class: "ev " + e.kind, "data-id": e.id }, el("div", {}, el("span", { class: "kind " + e.kind }, KIND[e.kind] || e.kind), el("b", {}, `[${e.id}] ${e.title}`),
      e.doc_id ? el("button", { class: "link", onclick: () => open(e.doc_id) }, " 문서 열기") : ""), el("div", { class: "ref" }, e.ref), el("div", { class: "t" }, e.text),
      e.masked_columns.length ? el("div", { class: "mask" }, "권한 없음으로 가려진 컬럼: " + e.masked_columns.join(", ") ) : ""));
    const ans = el("div", { class: "a" + (a.refused ? " refused" : "") });
    if (a.refused) ans.append(el("p", {}, a.answer)); else a.answer.split("\n").forEach((l) => ans.append(cites(l, box)));
    ans.append(el("div", { class: "hint" }, a.refused ? "근거 없음" : `생성: ${a.generator === "llm" ? "LLM (인용·숫자 검증 통과)" : "근거 발췌"}`)); if (a.evidence.length) ans.append(box);
    const turn = el("div", { class: "turn" }, el("div", { class: "q" }, q), ans); log.append(turn); turn.scrollIntoView({ block: "end" });
  }
  hist.forEach((h) => draw(h.q, h.a));
  async function ask() {
    const q = ta.value.trim(); if (!q) return; send.disabled = true;
    try { const a = await api("/api/ask", { method: "POST", body: { question: q } }); draw(q, a); hist.push({ q, a }); try { sessionStorage.setItem(key, JSON.stringify(hist.slice(-15))); } catch { /* ignore */ } ta.value = ""; }
    catch (e) { draw(q, { answer: e.status === 429 ? "요청이 너무 많습니다. 잠시 후 다시 시도하세요." : "오류: " + e.message, refused: true, evidence: [] }); } finally { send.disabled = false; ta.focus(); }
  }
  send.addEventListener("click", ask);
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(); } });
  const intro = el("div", { class: "chat-hero" }, el("span", { class: "chat-logo" }, icon("sparkles", 28)), el("h1", {}, "무엇이 궁금하세요?"),
    el("p", { class: "hint" }, "내가 열람할 수 있는 승인 문서와 업무 DB만 근거로 답합니다. 모든 답변에는 출처가 붙고 감사 로그에 남습니다."),
    el("div", { class: "ex-grid" }, (state.cfg.examples || []).slice(0, 6).map((t) => el("button", { class: "ex card", onclick: () => { ta.value = t; ta.focus(); } }, icon("search", 16), el("span", {}, t)))));
  if (hist.length) intro.hidden = true;
  const sendBtn = send; send.replaceChildren(icon("send", 18)); send.setAttribute("aria-label", "질문 전송"); send.classList.add("send-btn");
  view.replaceChildren(el("div", { class: "chat-page" }, intro, log, el("div", { class: "composer" }, ta, sendBtn),
    el("p", { class: "hint chat-note" }, "결재 중인 초안과 만료·삭제된 문서는 답변에 쓰이지 않습니다. 답변은 근거 발췌이며 중요한 판단은 원문을 확인하세요.")));
  const hide = () => { intro.hidden = true; };
  sendBtn.addEventListener("click", hide);
});

// ---------------- admin ----------------
const adminOnly = (view) => { if (!state.me.is_admin) { view.replaceChildren(el("p", { class: "err" }, "관리자만 볼 수 있습니다")); return true; } };
route(/^\/admin\/audit$/, async (view, _m, params) => {
  if (adminOnly(view)) return;
  const f = { user: params.user || "", action: params.action || "", q: params.q || "", from: params.from || "", to: params.to || "" };
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
  const r = await api("/api/dms/admin/audit?" + qs);
  const inp = (k, type = "text", ph = "") => el("input", { id: "au-" + k, type, value: f[k], placeholder: ph, "aria-label": k });
  const inputs = { user: inp("user", "text", "사용자 ID"), action: inp("action", "text", "작업 (예: registered)"), q: inp("q", "text", "문서번호/키워드"), from: inp("from", "date"), to: inp("to", "date") };
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "감사 로그"), r.chain_ok ? pill("무결성 정상", "ok") : pill(`변조 감지 (id ${r.first_bad_id})`, "bad"), el("span", { class: "grow" }),
    el("button", { onclick: () => saveBlob("/api/dms/admin/audit.csv?" + qs, {}, "audit.csv").catch(fail) }, "CSV 내보내기")),
    el("form", { class: "card filters", onsubmit: (e) => { e.preventDefault(); go("#/admin/audit?" + new URLSearchParams(Object.entries(inputs).map(([k, i]) => [k, i.value]).filter(([, v]) => v))); } },
      Object.values(inputs).map((i) => el("div", {}, i)), el("button", { class: "primary" }, "조회")),
    el("p", { class: "hint" }, `${fmtNum(r.count)}건`), table(["#", "일시", "사용자", "내용", "대상", "구분"], r.entries.map((e) => [e.id, fmtDate(e.ts), e.user, e.what, el("span", { class: "ref" }, e.refs.join(" ")), e.kind])));
});
route(/^\/admin\/types$/, async (view) => {
  if (adminOnly(view)) return;
  const types = await api("/api/dms/config").then((c) => c.doc_types);
  const edit = (t) => {
    const v = t || { code: "", name: "", pattern: "", retention_years: 0, approval_required: false, default_approvers: [], active: true };
    const code = el("input", { id: "dt-code", value: v.code, disabled: !!t, placeholder: "예: PO" }), name = el("input", { id: "dt-name", value: v.name }), pat = el("input", { id: "dt-pat", value: v.pattern, placeholder: "PO-{year}-{seq:04}" });
    const years = el("input", { id: "dt-years", type: "number", min: "0", max: "100", value: String(v.retention_years) }), appr = el("input", { id: "dt-appr", type: "checkbox", checked: v.approval_required }), act = el("input", { id: "dt-act", type: "checkbox", checked: v.active });
    const FT = { text: "텍스트", number: "숫자", date: "날짜", select: "선택" };
    const fl = el("textarea", { id: "dt-fields", placeholder: "키|이름|형식(text/number/date/select)|필수(Y)|보기1,보기2\n예: partner|거래처|text|Y\n예: kind|구분|select||용역,물품" },
      (v.fields || []).map((f) => [f.key, f.label, f.type, f.required ? "Y" : "", f.options.join(",")].join("|")).join("\n"));
    const ap = el("input", { id: "dt-aps", value: v.default_approvers.join(", "), placeholder: "사용자 ID (쉼표)" });
    return dialog(t ? `문서유형 ${t.code}` : "새 문서유형", () => el("div", {}, field("코드 (영문 대문자 2~6자)", code), field("이름", name), field("번호 패턴", pat, "{year} 와 {seq} 만 사용: 예) CT-{year}-{seq:04} → CT-2026-0001"), field("보존 연수 (0=없음)", years),
      el("label", { class: "inline" }, appr, "결재를 거쳐야 게시"), field("기본 결재자", ap), field("사용자 정의 항목", fl, "한 줄에 하나: 키|이름|형식|필수|보기. 등록·수정 화면에 입력칸이 생기고 값 검증과 CSV 내보내기에 쓰입니다"), el("label", { class: "inline" }, act, "사용")),
      [{ label: "취소", value: false }, { label: "저장", primary: true, run: async () => { await api("/api/dms/admin/doctypes", { method: "PUT", body: { code: code.value, name: name.value, pattern: pat.value || undefined, retention_years: Number(years.value), approval_required: appr.checked,
        default_approvers: ap.value.split(",").map((x) => x.trim()).filter(Boolean), active: act.checked,
        fields: fl.value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const [key, label, type, req, opts] = l.split("|").map((x) => (x || "").trim()); return { key, label, type: type || "text", required: req.toUpperCase() === "Y", options: (opts || "").split(",").map((x) => x.trim()).filter(Boolean) }; }) } }); toast("저장했습니다"); location.reload(); } }]);
  };
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "문서유형 · 번호 · 보존"), el("span", { class: "grow" }), el("button", { class: "primary", onclick: () => edit(null).catch(fail) }, "+ 새 유형")),
    table(["코드", "이름", "번호 패턴", "보존", "결재", "기본 결재자", "상태", ""], types.map((t) => [t.code, t.name, el("span", { class: "ref" }, t.pattern), t.retention_years ? t.retention_years + "년" : "-", t.approval_required ? "필요" : "불필요",
      t.default_approvers.join(", ") || "-", pill(t.active ? "사용" : "중지", t.active ? "ok" : ""), el("button", { class: "sm", onclick: () => edit(t).catch(fail) }, "편집")])));
});
route(/^\/admin\/stats$/, async (view) => {
  if (adminOnly(view)) return;
  const s = await api("/api/dms/admin/stats");
  const k = (n, l) => el("div", { class: "kpi" }, el("b", {}, fmtNum(n)), el("span", {}, l));
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "현황 · 유지관리")),
    el("div", { class: "kpis" }, k(s.documents, "관리 문서"), k(s.external_documents, "외부 소스 문서"), k(s.versions, "버전"), k(s.trash, "휴지통"), k(s.pending_workflows, "진행 중 결재"), k(s.legal_holds, "법적 보류"), k(s.active_shares, "유효 공유 링크"), k(s.folders, "폴더")),
    el("p", { class: "hint sec" }, `저장 용량 ${fmtSize(s.storage_bytes)}`),
    el("div", { class: "card sec" }, el("h3", {}, "유지관리"), el("p", { class: "hint" }, "만료일이 지난 문서를 만료 처리하고(검색·AI에서 본문 제외), 만료 예정 알림을 보내고, 보존기간이 끝난 오래된 휴지통 문서를 영구 삭제합니다. 서버는 주기적으로 자동 실행합니다."),
      el("button", { class: "primary", id: "run-maint", onclick: async () => { try { const r = await api("/api/dms/admin/maintenance", { method: "POST", body: {} }); toast(`만료 ${r.expired} · 예정 알림 ${r.warned} · 영구삭제 ${r.purged}`); } catch (e) { fail(e); } } }, "지금 실행")));
});
