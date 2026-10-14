import { api, dialog, el, empty, fail, field, fmtDate, fmtDay, fmtNum, go, pill, route, setNavBadge, state, table, toast } from "./core.js";
import { icon } from "./icons.js";

const ROLE = { viewer: "열람", editor: "편집" };
const STATE = { pending: ["처리 중", "info"], approved: ["승인", "ok"], denied: ["거절", "bad"] };

// ---------------- access requests ----------------
export function requestDialog(docNo, defaultRole = "viewer") {
  const ref = el("input", { id: "ar-ref", value: docNo || "", placeholder: "예: CT-2026-0001 (문서번호)" });
  const role = el("select", { id: "ar-role" }, Object.entries(ROLE).map(([v, l]) => el("option", { value: v, selected: v === defaultRole }, l)));
  const reason = el("textarea", { id: "ar-reason", maxlength: "500", placeholder: "필요한 이유를 적어 주세요" });
  return dialog("접근 권한 신청", () => el("div", {}, el("p", { class: "hint" }, "문서 담당자(소유자·관리자)에게 요청이 전달됩니다. 승인되면 알림을 받고 문서가 열립니다."),
    field("문서번호", ref), field("요청 권한", role), field("사유", reason)),
  [{ label: "취소", value: false }, { label: "신청", primary: true, run: async () => {
    const r = await api("/api/dms/access-requests", { method: "POST", body: { ref: ref.value, role: role.value, reason: reason.value } });
    toast(r.message); if (r.doc_id) go("#/doc/" + r.doc_id); else refreshAccess();
  } }]);
}
export async function refreshAccess() {
  try { const r = await api("/api/dms/access-requests"); setNavBadge("access", r.to_decide.length); } catch { /* ignore */ }
}

route(/^\/access$/, async (view) => {
  const r = await api("/api/dms/access-requests");
  setNavBadge("access", r.to_decide.length);
  const decide = async (item, approve) => {
    const note = approve ? "" : (await askNote());
    if (note === false) return;
    try { await api(`/api/dms/access-requests/${item.id}/decide`, { method: "POST", body: { approve, note } }); toast(approve ? "승인했습니다" : "거절했습니다"); go("#/access"); } catch (e) { fail(e); }
  };
  const askNote = () => new Promise((resolve) => { const inp = el("input", { placeholder: "거절 사유 (선택)" });
    dialog("신청 거절", () => el("div", {}, field("사유", inp)), [{ label: "취소", value: false }, { label: "거절", danger: true, run: () => { resolve(inp.value); } }]).then((v) => v === false && resolve(false)); });
  view.replaceChildren(
    el("div", { class: "page-h" }, el("h1", {}, "권한 신청"), el("span", { class: "grow" }), el("button", { class: "primary", onclick: () => requestDialog("") }, icon("key", 16), "새 신청")),
    el("div", { class: "grid2" },
      el("div", { class: "card" }, el("h3", {}, icon("inbox", 15), `내가 처리할 신청 (${r.to_decide.length})`),
        r.to_decide.length ? el("div", { class: "stack" }, r.to_decide.map((x) => el("div", { class: "req" },
          el("div", {}, el("b", {}, x.requester_name), ` 님이 `, el("button", { class: "link", onclick: () => go("#/doc/" + x.doc_id) }, `${x.doc_no}${x.title ? " " + x.title : ""}`), ` ${ROLE[x.role]} 권한 신청`),
          x.reason ? el("div", { class: "hint" }, "사유: " + x.reason) : "", el("div", { class: "row" }, el("button", { class: "sm primary", onclick: () => decide(x, true) }, "승인"), el("button", { class: "sm danger", onclick: () => decide(x, false) }, "거절"),
            el("span", { class: "hint" }, fmtDate(x.created)))))) : empty("처리할 신청이 없습니다", "inbox")),
      el("div", { class: "card" }, el("h3", {}, icon("user", 15), "내 신청 내역"),
        r.mine.length ? table(["문서", "권한", "상태", "신청일"], r.mine.map((x) => [x.doc_id ? el("button", { class: "link", onclick: () => go("#/doc/" + x.doc_id) }, x.title || x.ref) : x.ref, ROLE[x.role], pill(...(STATE[x.status] || [x.status, ""])), fmtDay(x.created)]))
          : empty("신청 내역이 없습니다", "key"))));
});

// ---------------- admin: users + system ----------------
route(/^\/admin\/users$/, async (view) => {
  const u = await api("/api/dms/admin/users");
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "사용자 · 조직"), el("span", { class: "hint" }, `${u.users.length}명 · 원본 ${u.source}`)),
    el("p", { class: "hint" }, u.hint),
    el("div", { class: "chips" }, u.departments.map((d) => el("span", { class: "chip on" }, `${d.dept} ${d.users}`))),
    el("div", { class: "card sec" }, table(["ID", "이름", "부서", "역할", "보안등급", "이메일", "외부 연동", "문서", "결재 대기"],
      u.users.map((x) => [x.id, x.name, x.dept, x.roles.join(", ") || "-", "L" + x.level, x.has_email ? pill("있음", "ok") : pill("없음", "warn"), x.external_ids.join(", ") || "-", x.documents, x.pending_approvals]))));
});

const dur = (s) => (s >= 86400 ? `${Math.floor(s / 86400)}일 ` : "") + `${Math.floor((s % 86400) / 3600)}시간 ${Math.floor((s % 3600) / 60)}분`;
route(/^\/admin\/system$/, async (view) => {
  const s = await api("/api/dms/admin/system");
  const row = (k, v) => [k, v];
  const yn = (b, y = "정상", n = "미설정") => pill(b ? y : n, b ? "ok" : "warn");
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "시스템 상태"), el("span", { class: "hint" }, `v${s.version} · 가동 ${dur(s.uptime_seconds)}`)),
    el("div", { class: "kpis" }, [["검색 색인 문서", s.index.documents], ["색인 조각", s.index.chunks], ["감사 기록", s.audit.entries], ["처리 중 결재", s.queues.pending_workflows], ["권한 신청 대기", s.queues.pending_access_requests]]
      .map(([l, n]) => el("div", { class: "kpi" }, el("b", {}, fmtNum(n)), el("span", {}, l)))),
    el("div", { class: "card sec" }, table(["항목", "상태"], [
      row("인증 방식", el("code", {}, s.auth_mode)),
      row("감사 로그 무결성(해시 체인)", s.audit.chain_ok ? pill("정상", "ok") : pill("변조 의심: #" + s.audit.broken_at, "bad")),
      row("LLM", s.llm.configured ? pill("연결됨 " + (s.llm.model || ""), "ok") : pill("미사용 (근거 발췌 답변)", "")),
      row("임베딩(벡터 검색)", yn(s.index.embedder, "사용", "퍼지 벡터 사용")),
      row("업무 DB", s.structured_db.connected ? pill(`연결됨 · ${s.structured_db.tables.join(", ")}`, "ok") : pill("미연결", "warn")),
      row("메일 알림", s.mail.enabled ? pill(`사용 · 발송 ${s.mail.sent} / 실패 ${s.mail.failed} / 대기 ${s.mail.queued}`, s.mail.failed ? "warn" : "ok") : pill("미설정", "")),
      row("외부 공유 링크", yn(s.external_share, "허용", "꺼짐")),
      ...Object.entries(s.adapters).map(([k, v]) => row(`연동: ${k}`, yn(v.enabled, "활성", "비활성 (비밀값 환경변수 없음)"))),
      row("색인 저장소 크기", `${(s.store_bytes / 1048576).toFixed(1)} MB`)])));
});
