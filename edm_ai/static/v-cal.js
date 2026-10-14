import { api, el, empty, fmtDay, go, pill, route } from "./core.js";
import { icon } from "./icons.js";

const KIND = { due: ["납기(문서)", "info"], db_due: ["납기(DB)", "info"], expiry: ["만료", "bad"], retention: ["보존 종료", "warn"], task: ["결재 도착", "ok"] };
const DOW = ["일", "월", "화", "수", "목", "금", "토"];
const ym = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;

route(/^\/calendar$/, async (view, _m, params) => {
  const month = /^\d{4}-\d{2}$/.test(params.month || "") ? params.month : ym(new Date());
  const [y, mo] = month.split("-").map(Number);
  const data = await api("/api/dms/calendar?month=" + month);
  const byDay = {};
  for (const e of data.events) (byDay[e.date] ||= []).push(e);
  const shift = (n) => ym(new Date(y, mo - 1 + n, 1));
  const today = new Date().toISOString().slice(0, 10);
  const offset = new Date(y, mo - 1, 1).getDay();
  const days = new Date(y, mo, 0).getDate();
  const detail = el("div", { class: "card cal-detail" });
  const show = (day) => {
    const list = byDay[day] || [];
    detail.replaceChildren(el("h3", {}, icon("calendar", 15), fmtDay(day)), list.length ? el("div", { class: "stack" }, list.map(evRow)) : empty("이 날의 일정이 없습니다", "calendar"));
    for (const c of view.querySelectorAll(".cal-day")) c.classList.toggle("sel", c.dataset.day === day);
  };
  const evRow = (e) => el("div", { class: "cal-ev-row" }, pill(...(KIND[e.kind] || [e.label, ""])),
    e.doc_id ? el("button", { class: "link", onclick: () => go("#/doc/" + e.doc_id) }, `${e.doc_no} ${e.title}`) : el("span", {}, `${e.doc_no} ${e.title}`));
  const cells = [];
  for (let i = 0; i < offset; i++) cells.push(el("div", { class: "cal-day off" }));
  for (let d = 1; d <= days; d++) {
    const day = `${month}-${String(d).padStart(2, "0")}`;
    const ev = byDay[day] || [];
    cells.push(el("button", { class: "cal-day" + (day === today ? " today" : "") + (ev.length ? " has" : ""), "data-day": day, onclick: () => show(day), "aria-label": `${d}일 일정 ${ev.length}건` },
      el("span", { class: "cal-n" }, String(d)), ev.slice(0, 3).map((e) => el("span", { class: "cal-chip " + e.kind }, e.title)), ev.length > 3 ? el("span", { class: "cal-more" }, `+${ev.length - 3}건`) : ""));
  }
  const total = data.events.length;
  view.replaceChildren(
    el("div", { class: "page-h" }, el("h1", {}, "일정 캘린더"), el("span", { class: "hint" }, `${y}년 ${mo}월 · 일정 ${total}건`), el("span", { class: "grow" }),
      el("button", { onclick: () => go("#/calendar?month=" + shift(-1)), "aria-label": "이전 달" }, "◀"), el("button", { onclick: () => go("#/calendar") }, "오늘"),
      el("button", { onclick: () => go("#/calendar?month=" + shift(1)), "aria-label": "다음 달" }, "▶")),
    el("div", { class: "cal-wrap" },
      el("div", { class: "card cal" }, el("div", { class: "cal-grid cal-head" }, DOW.map((n) => el("div", {}, n))), el("div", { class: "cal-grid" }, cells),
        el("div", { class: "row sec" }, Object.entries(KIND).map(([k, [l]]) => el("span", { class: "cal-chip " + k }, l)))),
      detail));
  show(byDay[today] ? today : (data.events[0]?.date || `${month}-01`));
});
