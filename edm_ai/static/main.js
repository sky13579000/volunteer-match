import { $, api, deptName, el, fail, go, parseHash, render, state } from "./core.js";
import "./v-docs.js";
import "./v-doc.js";
import "./v-cal.js";
import "./v-edit.js";
import "./v-social.js";
import { openPalette } from "./v-palette.js";
import "./v-reports.js";
import "./v-records.js";
import "./v-integrations.js";
import "./v-sources.js";
import "./v-mail.js";
import "./v-verify.js";
import "./v-insight.js";
import "./v-ops.js";
import { toggleNotifPopover } from "./v-notif.js";
import { refreshBell } from "./v-misc.js";
import { icon } from "./icons.js";

const NAV = [
  ["", [["#/dashboard", "대시보드", "home"], ["#/chat", "AI 질의응답", "sparkles"]]],
  ["문서", [["#/docs", "문서함", "folder"], ["#/docs?favorites=1", "즐겨찾기", "star"], ["#/docs?mine=1", "내가 올린 문서", "user"], ["#/verify", "사본 확인", "check"], ["#/trash", "휴지통", "trash"]]],
  ["업무", [["#/tasks", "결재함", "task", "tasks"], ["#/access", "권한 신청", "key", "access"], ["#/notifications", "알림", "bell"], ["#/calendar", "일정 캘린더", "calendar"], ["#/alerts", "문서↔DB 불일치", "alert"]]],
];
const ADMIN = ["관리", [["#/records", "기록물 관리", "book"], ["#/admin/audit", "감사 로그", "shield"], ["#/admin/types", "문서유형 · 번호 · 보존", "layers"], ["#/admin/reports", "리포트", "chart"], ["#/admin/users", "사용자 · 조직", "users"], ["#/admin/trust", "AI 신뢰성 센터", "shield"], ["#/admin/ops", "백업 · 라이선스", "settings"], ["#/admin/sources", "저장소 연동 (NAS)", "inbox"], ["#/admin/mail", "메일 연동", "mail"], ["#/admin/integrations", "연동 관리", "share"], ["#/admin/system", "시스템 상태", "settings"], ["#/admin/stats", "현황 · 유지관리", "chart"]]];

const RECORDS = ["기록물", [["#/records", "기록물 관리", "book"]]];
function buildNav() {
  const groups = [...NAV, ...(state.me.is_records && !state.me.is_admin ? [RECORDS] : []), ...(state.me.is_admin ? [ADMIN] : [])];
  $("#nav").replaceChildren(...groups.flatMap(([title, items]) => [title ? el("div", { class: "grp" }, title) : "",
    ...items.map(([href, label, ic, badge]) => el("a", { href, "data-badge": badge || false }, icon(ic || "file", 18), el("span", { class: "t" }, label), badge ? el("span", { class: "nb", id: "nb-" + badge, hidden: true }) : ""))]));
}
function applyTheme(t) {
  if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme");
  const dark = t === "dark" || (!t && matchMedia("(prefers-color-scheme: dark)").matches);
  $("#theme").replaceChildren(icon(dark ? "sun" : "moon", 16), dark ? "라이트" : "다크");
}
state.onRoute = (path, params) => {
  const cur = "#" + path + (Object.keys(params).length && [...document.querySelectorAll("#nav a")].some((a) => a.getAttribute("href") === "#" + path + "?" + new URLSearchParams(params)) ? "?" + new URLSearchParams(params) : "");
  for (const a of document.querySelectorAll("#nav a")) a.removeAttribute("aria-current");
  const exact = [...document.querySelectorAll("#nav a")].find((a) => a.getAttribute("href") === cur) || [...document.querySelectorAll("#nav a")].find((a) => a.getAttribute("href") === "#" + path.replace(/^(\/\w+).*$/, "$1") && !a.getAttribute("href").includes("?"));
  exact?.setAttribute("aria-current", "page");
};

async function enter() {
  state.meta = await api("/api/dms/config");
  $("#login").hidden = true;
  $("#shell").hidden = false;
  $("#ver").textContent = "ICC JEJU 플랫폼 v" + (state.cfg.version || "?");
  $("#who-name").textContent = state.me.name;
  $("#who-sub").textContent = `${deptName(state.me.dept)} · 보안등급 L${state.me.level}${state.me.is_admin ? " · 관리자" : ""}`;
  $("#avatar").textContent = (state.me.name || "?").slice(0, 1);
  buildNav();
  api("/api/license").then((l) => { const b = $("#lic-banner"); if (l.message && (l.mode !== "licensed" || l.days_left <= 30 || l.message.includes("한도"))) { b.textContent = l.message; b.hidden = false; } }).catch(() => {});
  refreshBell();
  setInterval(refreshBell, 60000);
  if (!location.hash) location.hash = "#/dashboard";
  await render();
}

function chrome() {
  for (const id of ["hero-mark", "brand-mark"]) $("#" + id).append(icon("building", id === "hero-mark" ? 22 : 19));
  $("#search-ic").append(icon("search", 16));
  $("#bell-ic").append(icon("bell", 19));
  $("#logout").append(icon("logout", 16), "로그아웃");
  const pts = [["shield", "권한이 곧 검색 범위 — 볼 수 없는 문서는 AI도 보지 못합니다"], ["book", "모든 답변에 출처 문서와 DB 행을 인용합니다"], ["history", "버전 · 결재 · 보존기간 · 감사 로그를 한 곳에서"]];
  $("#hero-points").append(...pts.map(([ic, t]) => el("li", {}, icon(ic, 18), t)));
  $("#hero-foot").textContent = "제주국제컨벤션센터 · 문서·AI 플랫폼";
  let t = null;
  try { t = localStorage.getItem("theme"); } catch { /* ignore */ }
  applyTheme(t);
  $("#theme").addEventListener("click", () => {
    const dark = document.documentElement.getAttribute("data-theme") === "dark" || (!document.documentElement.getAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
    const next = dark ? "light" : "dark";
    try { localStorage.setItem("theme", next); } catch { /* ignore */ }
    applyTheme(next);
  });
  $("#top-q").addEventListener("focus", () => {});
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && state.me) { e.preventDefault(); openPalette(); }
  });
  document.addEventListener("keydown", (e) => {                           // "/" focuses the search box (like most document tools)
    if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) && !document.querySelector("dialog[open]")) { e.preventDefault(); $("#top-q").focus(); }
  });
}

async function boot() {
  state.cfg = await (await fetch("/api/config")).json();
  chrome();
  $("#top-search").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#top-q").value.trim(); if (q) go("#/docs?q=" + encodeURIComponent(q)); });
  $("#bell").addEventListener("click", (e) => { e.stopPropagation(); toggleNotifPopover(); });
  $("#logout").addEventListener("click", () => {
    try { sessionStorage.clear(); } catch { /* ignore */ }
    if (state.cfg.auth_mode === "proxy" && state.cfg.logout_url) { location.href = state.cfg.logout_url; return; }
    location.hash = ""; location.reload();
  });
  window.addEventListener("hashchange", render);
  $("#login-ver").textContent = "v" + (state.cfg.version || "");
  if (state.cfg.auth_mode === "proxy") {          // single sign-on: no login form, the proxy already authenticated the user
    try { state.me = await api("/api/me"); return enter(); } catch { /* not registered */ }
    $("#login-err").textContent = "SSO 로그인은 되었지만 플랫폼 사용자로 등록되어 있지 않습니다. 관리자에게 계정 등록(조직도 동기화)을 요청하세요.";
    $("#login").hidden = false;
    $("#login-go").hidden = true;
    return;
  }
  if (state.cfg.auth_mode === "token") $("#login-token").hidden = false;
  else if (state.cfg.demo) {
    $("#login-demo").hidden = false;
    for (const u of await (await fetch("/api/demo-users")).json()) $("#demo-user").append(el("option", { value: u.id }, `${u.name} · ${u.dept_name || u.dept} · L${u.level}`));
  }
  $("#login-go").addEventListener("click", async () => {
    $("#login-err").textContent = "";
    try { if (state.cfg.auth_mode === "token") sessionStorage.setItem("token", $("#token").value.trim()); else sessionStorage.setItem("uid", $("#demo-user").value); } catch { /* ignore */ }
    try { state.me = await api("/api/me"); await enter(); } catch (e) { $("#login-err").textContent = "로그인 실패: " + e.message; }
  });
  let stored = false;
  try { stored = !!(sessionStorage.getItem("token") || sessionStorage.getItem("uid")); } catch { /* ignore */ }
  if (stored) { try { state.me = await api("/api/me"); return enter(); } catch { /* fall through to login */ } }
  $("#login").hidden = false;
}
boot().catch(fail);
