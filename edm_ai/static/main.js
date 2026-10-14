import { $, api, el, fail, go, parseHash, render, state } from "./core.js";
import "./v-docs.js";
import "./v-doc.js";
import { refreshBell } from "./v-misc.js";

const NAV = [
  ["", [["#/dashboard", "대시보드"], ["#/chat", "AI 질의응답"]]],
  ["문서", [["#/docs", "문서함"], ["#/docs?favorites=1", "즐겨찾기"], ["#/docs?mine=1", "내가 올린 문서"], ["#/trash", "휴지통"]]],
  ["업무", [["#/tasks", "결재함", "tasks"], ["#/notifications", "알림"], ["#/alerts", "문서↔DB 불일치"]]],
];
const ADMIN = ["관리", [["#/admin/audit", "감사 로그"], ["#/admin/types", "문서유형 · 번호 · 보존"], ["#/admin/stats", "현황 · 유지관리"]]];

function buildNav() {
  const groups = state.me.is_admin ? [...NAV, ADMIN] : NAV;
  $("#nav").replaceChildren(...groups.flatMap(([title, items]) => [title ? el("div", { class: "grp" }, title) : "", ...items.map(([href, label]) => el("a", { href }, label))]));
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
  $("#ver").textContent = "EDM-AI v" + (state.cfg.version || "?");
  $("#who").textContent = `${state.me.name} · ${state.me.dept || "-"} · L${state.me.level}`;
  buildNav();
  refreshBell();
  setInterval(refreshBell, 60000);
  if (!location.hash) location.hash = "#/dashboard";
  await render();
}

async function boot() {
  state.cfg = await (await fetch("/api/config")).json();
  $("#top-search").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#top-q").value.trim(); if (q) go("#/docs?q=" + encodeURIComponent(q)); });
  $("#bell").addEventListener("click", () => go("#/notifications"));
  $("#logout").addEventListener("click", () => {
    try { sessionStorage.clear(); } catch { /* ignore */ }
    if (state.cfg.auth_mode === "proxy" && state.cfg.logout_url) { location.href = state.cfg.logout_url; return; }
    location.hash = ""; location.reload();
  });
  window.addEventListener("hashchange", render);
  $("#login-ver").textContent = "v" + (state.cfg.version || "");
  if (state.cfg.auth_mode === "proxy") {          // single sign-on: no login form, the proxy already authenticated the user
    try { state.me = await api("/api/me"); return enter(); } catch { /* not registered */ }
    $("#login-err").textContent = "SSO 로그인은 되었지만 EDM-AI 사용자로 등록되어 있지 않습니다. 관리자에게 계정 등록(조직도 동기화)을 요청하세요.";
    $("#login").hidden = false;
    $("#login-go").hidden = true;
    return;
  }
  if (state.cfg.auth_mode === "token") $("#login-token").hidden = false;
  else if (state.cfg.demo) {
    $("#login-demo").hidden = false;
    for (const u of await (await fetch("/api/demo-users")).json()) $("#demo-user").append(el("option", { value: u.id }, `${u.name} (${u.dept} / L${u.level})`));
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
