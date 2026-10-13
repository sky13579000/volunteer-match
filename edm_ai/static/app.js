"use strict";
const $ = (id) => document.getElementById(id);
const store = {
  get: (k) => { try { return sessionStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { sessionStorage.setItem(k, v); } catch { /* private mode */ } },
  del: (k) => { try { sessionStorage.removeItem(k); } catch { /* ignore */ } },
};
const KIND = { doc: "문서", row: "DB", metric: "집계" };
let cfg = {}, me = null;

function el(tag, props = {}, ...kids) {           // DOM builder: every string goes through textContent
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v; else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
  }
  for (const c of kids) n.append(c);
  return n;
}
function headers() {
  const h = {};
  if (cfg.auth_mode === "token") h.Authorization = "Bearer " + (store.get("token") || "");
  else h["X-User-Id"] = store.get("uid") || "";
  return h;
}
async function api(path, opts = {}) {
  const r = await fetch(path, { ...opts, headers: { ...headers(), ...(opts.headers || {}) } });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || r.statusText), { status: r.status });
  return data;
}
function show(view) {
  for (const v of ["login", "chat", "audit"]) $(v).hidden = v !== view;
  $("tab-chat").classList.toggle("on", view === "chat");
  $("tab-audit").classList.toggle("on", view === "audit");
}

async function boot() {
  cfg = await (await fetch("/api/config")).json();
  if (cfg.auth_mode === "token") { $("login-token").hidden = false; }
  else if (cfg.demo) {
    $("login-demo").hidden = false;
    const users = await (await fetch("/api/demo-users")).json();
    for (const u of users) $("demo-user").append(el("option", { value: u.id }, `${u.name} (${u.dept} / L${u.level})`));
  }
  if (!(store.get("token") || store.get("uid"))) return show("login");   // no stored credential: skip the probe
  try { me = await api("/api/me"); enter(); } catch { show("login"); }
}
function enter() {
  $("who").textContent = `${me.name} · ${me.dept || "-"} · 보안등급 ${me.level}`;
  $("logout").hidden = false;
  $("tab-audit").hidden = !me.is_admin;
  const ex = $("examples");
  ex.replaceChildren(...cfg.examples.map((t) => el("button", { type: "button", onclick: () => { $("q").value = t; $("q").focus(); } }, t)));
  show("chat");
  for (const t of JSON.parse(store.get("history:" + me.id) || "[]")) render(t.q, t.a);
}
$("login-go").addEventListener("click", async () => {
  $("login-err").textContent = "";
  if (cfg.auth_mode === "token") store.set("token", $("token").value.trim()); else store.set("uid", $("demo-user").value);
  try { me = await api("/api/me"); enter(); } catch (e) { $("login-err").textContent = "로그인 실패: " + e.message; }
});
$("logout").addEventListener("click", () => { store.del("token"); store.del("uid"); location.reload(); });
$("tab-chat").addEventListener("click", () => show("chat"));
$("tab-audit").addEventListener("click", async () => {
  show("audit");
  const d = await api("/api/audit");
  $("chain").replaceChildren(el("b", { class: d.chain_ok ? "ok" : "bad" }, d.chain_ok ? "✔ 감사 로그 무결성 정상" : `✖ 변조 감지 (id ${d.first_bad_id})`));
  $("audit-rows").replaceChildren(...d.entries.map((e) => el("tr", {},
    el("td", {}, e.ts), el("td", {}, e.user), el("td", {}, e.question), el("td", {}, e.evidence.join("\n")), el("td", {}, e.refused ? "거절" : e.generator))));
});

function citeify(text, evBox) {                  // turn [E#] into chips that jump to the evidence card
  const p = el("p");
  for (const part of text.split(/(\[E\d+\])/)) {
    const m = part.match(/^\[(E\d+)\]$/);
    if (!m) { p.append(part); continue; }
    p.append(el("button", { class: "cite", type: "button", onclick: () => {
      const t = evBox.querySelector(`[data-id="${m[1]}"]`);
      if (t) { evBox.open = true; t.scrollIntoView({ block: "center", behavior: "smooth" }); t.classList.add("hl"); setTimeout(() => t.classList.remove("hl"), 1500); }
    } }, m[1]));
  }
  return p;
}
function render(q, a) {
  const evBox = el("details");
  evBox.append(el("summary", {}, `근거 ${a.evidence.length}건`));
  for (const e of a.evidence) {
    const card = el("div", { class: "ev " + e.kind, "data-id": e.id },
      el("div", {}, el("span", { class: "badge " + e.kind }, KIND[e.kind] || e.kind), el("b", {}, `[${e.id}] ${e.title}`)),
      el("div", { class: "ref" }, e.ref), el("div", { class: "t" }, e.text));
    if (e.masked_columns.length) card.append(el("div", { class: "mask" }, "🔒 권한 없음(마스킹): " + e.masked_columns.join(", ")));
    evBox.append(card);
  }
  const ans = el("div", { class: "a" + (a.refused ? " refused" : "") });
  if (a.refused) ans.append(el("p", {}, "⚠ " + a.answer));
  else for (const line of a.answer.split("\n")) ans.append(citeify(line, evBox));
  ans.append(el("div", { class: "meta" }, a.refused ? "근거 없음" : `생성: ${a.generator === "llm" ? "LLM(인용 검증됨)" : "근거 발췌"}`));
  if (a.evidence.length) ans.append(evBox);
  const turn = el("div", { class: "turn" }, el("div", { class: "q" }, q), ans);
  $("log").append(turn);
  turn.scrollIntoView({ block: "end" });
}
$("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("q").value.trim();
  if (!q) return;
  $("send").disabled = true;
  try {
    const a = await api("/api/ask", { method: "POST", body: JSON.stringify({ question: q }) });
    render(q, a);
    const key = "history:" + me.id, h = JSON.parse(store.get(key) || "[]");
    h.push({ q, a }); store.set(key, JSON.stringify(h.slice(-20)));
    $("q").value = "";
  } catch (e) {
    render(q, { answer: e.status === 429 ? "요청이 너무 많습니다. 잠시 후 다시 시도하세요." : "오류: " + e.message, refused: true, evidence: [] });
    if (e.status === 401) location.reload();
  } finally { $("send").disabled = false; $("q").focus(); }
});
$("q").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("form").requestSubmit(); } });
boot();
