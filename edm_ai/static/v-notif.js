import { api, el, fmtDate, go, pill } from "./core.js";
import { icon } from "./icons.js";
import { refreshBell } from "./v-misc.js";

const KIND = { approval_request: ["결재 요청", "info"], approved: ["승인", "ok"], rejected: ["반려", "bad"], comment: ["댓글", ""], mention: ["언급", "info"], expiring: ["만료 예정", "warn"], expired: ["만료", "bad"], cancelled: ["회수", ""],
  follow_update: ["구독", "info"], access_request: ["권한 신청", "warn"], access_granted: ["권한 승인", "ok"], access_denied: ["권한 거절", "bad"], privacy_alert: ["개인정보", "bad"], disposal_request: ["폐기 신청", "warn"], disposal_result: ["폐기 결과", "info"] };

let pop = null;
const close = () => { pop?.remove(); pop = null; document.removeEventListener("click", outside, true); document.removeEventListener("keydown", onKey, true); };
const outside = (e) => { if (pop && !pop.contains(e.target) && !e.target.closest("#bell")) close(); };
const onKey = (e) => { if (e.key === "Escape") close(); };

/** Bell dropdown: the latest notifications without leaving the current page. */
export async function toggleNotifPopover() {
  if (pop) return close();
  pop = el("div", { class: "notif-pop", role: "dialog", "aria-label": "알림" }, el("div", { class: "np-h" }, icon("bell", 16), "알림"), el("div", { class: "np-l" }, el("p", { class: "hint np-f" }, "불러오는 중...")));
  document.body.append(pop);
  document.addEventListener("click", outside, true); document.addEventListener("keydown", onKey, true);
  try {
    const n = await api("/api/dms/notifications?limit=8");
    const head = pop.querySelector(".np-h");
    head.append(el("span", { class: "grow" }), n.unread ? el("button", { class: "link", onclick: async () => { await api("/api/dms/notifications/read", { method: "POST", body: {} }); await refreshBell(); close(); toggleNotifPopover(); } }, "모두 읽음") : "");
    pop.querySelector(".np-l").replaceChildren(...(n.items.length ? n.items.map((x) => el("button", { type: "button", class: "np-i" + (x.read ? "" : " unread"), onclick: async () => {
      await api("/api/dms/notifications/read", { method: "POST", body: { ids: [x.id] } }); refreshBell(); close();
      if (x.doc_id) go(`#/doc/${x.doc_id}` + (x.kind === "approval_request" ? "?tab=approval" : "")); else go("#/notifications");
    } }, pill(...(KIND[x.kind] || [x.kind, ""])), el("div", { class: "t" }, x.read ? x.text : el("b", {}, x.text), el("div", { class: "hint" }, fmtDate(x.ts)))))
      : [el("p", { class: "hint np-f" }, "새 알림이 없습니다")]));
    pop.append(el("div", { class: "np-f" }, el("button", { class: "link", onclick: () => { close(); go("#/notifications"); } }, "모든 알림 보기")));
  } catch (e) { pop.querySelector(".np-l").replaceChildren(el("p", { class: "err np-f" }, e.message)); }
}
