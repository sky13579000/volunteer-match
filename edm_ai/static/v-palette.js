import { api, dialog, el, go, state } from "./core.js";
import { icon } from "./icons.js";

/** Ctrl/⌘+K: jump to any page or document. Documents come from the normal permission-checked search. */
const PAGES = [["#/dashboard", "대시보드", "home"], ["#/docs", "문서함", "folder"], ["#/chat", "AI 질의응답", "sparkles"], ["#/new", "양식으로 새 문서 작성", "file"], ["#/tasks", "결재함", "task"],
  ["#/access", "권한 신청", "key"], ["#/verify", "사본 확인 (최신본인가?)", "check"], ["#/calendar", "일정 캘린더", "calendar"], ["#/notifications", "알림", "bell"], ["#/docs?favorites=1", "즐겨찾기", "star"], ["#/docs?mine=1", "내가 올린 문서", "user"], ["#/trash", "휴지통", "trash"]];
const ADMIN = [["#/records", "기록물 관리", "book"], ["#/admin/audit", "감사 로그", "shield"], ["#/admin/reports", "리포트", "chart"], ["#/admin/users", "사용자 · 조직", "users"], ["#/admin/integrations", "연동 관리 (API 키·웹훅)", "share"], ["#/admin/trust", "AI 신뢰성 센터", "shield"], ["#/admin/ops", "백업 · 라이선스", "settings"], ["#/admin/sources", "저장소 연동 (NAS)", "inbox"], ["#/admin/mail", "메일 연동", "mail"], ["#/admin/system", "시스템 상태", "settings"], ["#/admin/types", "문서유형 · 보존", "layers"]];

export function openPalette() {
  if (document.querySelector("dialog.palette")) return;
  const input = el("input", { class: "pal-in", placeholder: "페이지 이동 또는 문서 검색...", "aria-label": "명령 팔레트", autocomplete: "off" });
  const list = el("div", { class: "pal-list", role: "listbox" });
  const dlg = el("dialog", { class: "palette", "aria-label": "명령 팔레트" }, el("div", { class: "pal-top" }, icon("search", 18), input, el("span", { class: "kbd" }, "Esc")), list);
  let items = [], sel = 0, seq = 0;
  const draw = () => {
    list.replaceChildren(...(items.length ? items.map((it, i) => el("button", { type: "button", role: "option", class: "pal-i" + (i === sel ? " on" : ""), "aria-selected": String(i === sel),
      onmouseenter: () => { sel = i; mark(); }, onclick: () => pick(it) }, icon(it.ic, 18), el("span", { class: "pal-t" }, it.label), el("span", { class: "pal-k" }, it.kind))) : [el("div", { class: "empty-state" }, "일치하는 항목이 없습니다")]));
  };
  const mark = () => [...list.children].forEach((c, i) => { c.classList.toggle("on", i === sel); c.setAttribute("aria-selected", String(i === sel)); });
  const pick = (it) => { dlg.close(); dlg.remove(); go(it.href); };
  const pages = (q) => [...PAGES, ...(state.me?.is_admin ? ADMIN : [])].filter(([, l]) => !q || l.toLowerCase().includes(q.toLowerCase())).map(([href, label, ic]) => ({ href, label, ic, kind: "이동" }));
  async function refresh() {
    const q = input.value.trim(), my = ++seq;
    items = pages(q).slice(0, q ? 5 : 8); sel = 0; draw();
    if (q.length >= 2) {
      try {
        const r = await api("/api/docs?size=6&q=" + encodeURIComponent(q));
        if (my !== seq) return;
        items = [...items, ...r.items.map((d) => ({ href: "#/doc/" + d.id, label: `${d.doc_no}  ${d.title}`, ic: "file", kind: "문서" }))];
        if (items.length === 0) items = [{ href: "#/chat", label: `AI에게 질문: ${q}`, ic: "sparkles", kind: "AI" }];
        sel = 0; draw();
      } catch { /* keep page results */ }
    }
  }
  input.addEventListener("input", refresh);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); mark(); list.children[sel]?.scrollIntoView({ block: "nearest" }); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); list.children[sel]?.scrollIntoView({ block: "nearest" }); }
    else if (e.key === "Enter" && items[sel]) { e.preventDefault(); pick(items[sel]); }
  });
  dlg.addEventListener("close", () => dlg.remove());
  dlg.addEventListener("click", (e) => { if (e.target === dlg) { dlg.close(); } });
  document.body.append(dlg); dlg.showModal(); refresh(); input.focus();
}
