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
  for (const v of ["login", "chat", "docs", "alerts", "audit"]) $(v).hidden = v !== view;
  for (const t of ["chat", "docs", "alerts", "audit"]) $("tab-" + t).classList.toggle("on", view === t);
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
  $("tab-docs").hidden = false;
  $("tab-alerts").hidden = false;
  refreshAlertCount();
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
$("tab-docs").addEventListener("click", () => { show("docs"); loadDocs(); });
$("tab-alerts").addEventListener("click", () => { show("alerts"); loadAlerts(); });
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

// ===================== documents (EDM) =====================
const SEV = { high: ["높음", "bad"], medium: ["중간", "warn"] };
const ACL_FIELDS = [["roles", "역할 (쉼표)"], ["depts", "부서 (쉼표)"], ["users", "사용자 ID (쉼표)"], ["deny_users", "제외 사용자 ID"]];
let curDoc = null;

function aclForm(prefix, acl) {
  const box = el("div", { class: "acl-grid" });
  for (const [k, label] of ACL_FIELDS) {
    box.append(el("label", {}, label, el("input", { type: "text", id: `${prefix}-${k}`, value: (acl && acl[k] || []).join(", ") })));
  }
  box.append(el("label", {}, "보안등급", el("input", { type: "number", min: "0", max: "99", id: `${prefix}-level`, value: String(acl ? acl.level : 0) })));
  return box;
}
function readAcl(prefix) {
  const list = (k) => $(`${prefix}-${k}`).value.split(",").map((x) => x.trim()).filter(Boolean);
  return { roles: list("roles"), depts: list("depts"), users: list("users"), deny_users: list("deny_users"), level: Number($(`${prefix}-level`).value || 0) };
}
async function loadDocs(q) {
  const list = await api("/api/docs" + (q ? "?q=" + encodeURIComponent(q) : ""));
  $("doc-list").replaceChildren(...(list.length ? list.map((d) => el("li", {},
    el("button", { type: "button", class: "doc-item" + (d.readable ? "" : " dim") + (curDoc === d.id ? " on" : ""), onclick: () => showDoc(d) },
      el("b", {}, d.title), el("small", {}, d.source),
      el("small", {}, d.readable ? `청크 ${d.chunks}` + (d.owner ? ` · 소유 ${d.owner}` : "") : "읽기 권한 없음 (관리만 가능)")))) : [el("li", { class: "hint" }, "문서가 없습니다")]));
}
async function showDoc(d, note) {
  curDoc = d.id;
  const box = $("doc-detail");
  if (!d.readable) return renderDetail(d, null, note);
  try { const det = await api("/api/docs/" + d.id); renderDetail(det, det, note); } catch (e) { box.replaceChildren(el("p", { class: "err" }, e.message)); }
  loadDocs($("doc-q").value.trim());
}
function table(headers, rows) {
  return el("div", { class: "scroll" }, el("table", { class: "tbl" },
    el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", {}, h)))),
    el("tbody", {}, ...rows.map((r) => el("tr", {}, ...r.map((c) => el("td", {}, c)))))));
}
function sec(title, ...kids) { return el("div", { class: "sec" }, el("h3", {}, title), ...kids); }
function renderDetail(sum, det, note) {
  const box = $("doc-detail");
  const kids = [el("h2", { class: "flush" }, sum.title), el("div", { class: "ref" }, sum.source)];
  const pills = el("div", { class: "sec" });
  if (det) pills.append(el("span", { class: "pill" }, det.analysis.category));
  pills.append(el("span", { class: "pill" }, sum.managed ? "업로드 문서" : "수집 문서"));
  if (sum.owner) pills.append(el("span", { class: "pill" }, "소유 " + sum.owner));
  kids.push(pills);
  if (!det) {
    kids.push(el("p", { class: "hint" }, "이 문서의 내용을 읽을 권한이 없습니다. 관리자는 권한 변경과 삭제만 할 수 있습니다."));
  } else {
    if (det.analysis.summary) kids.push(sec("자동 요약", el("p", { class: "flush" }, det.analysis.summary)));
    const f = det.analysis.fields;
    if (Object.keys(f).length) {
      const dl = el("dl", { class: "dl" });
      const names = { contract_no: "계약번호", project: "프로젝트", amount: "계약금액", due_date: "납기" };
      for (const [k, v] of Object.entries(f)) dl.append(el("dt", {}, names[k] || k), el("dd", {}, typeof v === "number" ? v.toLocaleString() + "원" : v));
      kids.push(sec("자동 추출 항목", dl));
    }
    if (det.analysis.checks.length) {
      kids.push(sec("업무 DB 대조", table(["항목", "문서", "DB", "결과", "DB 행"], det.analysis.checks.map((c) => [c.field,
        typeof c.doc === "number" ? c.doc.toLocaleString() : c.doc, typeof c.db === "number" ? c.db.toLocaleString() : c.db,
        el("span", { class: "pill " + (c.status === "ok" ? "ok" : "bad") }, c.status === "ok" ? "일치" : "불일치"), c.row]))));
    }
    if (det.linked_rows.length) {
      kids.push(sec("연결된 업무 DB 행", ...det.linked_rows.map((r) => el("div", { class: "ev row" },
        el("b", {}, `${r.title} · ${r.key}`),
        el("div", { class: "t" }, Object.entries(r.values).map(([k, v]) => `${k}: ${v}`).join("\n")),
        r.masked.length ? el("div", { class: "mask" }, "권한 없음으로 가려진 컬럼: " + r.masked.join(", ")) : ""))));
    }
    kids.push(sec("본문", el("pre", { class: "body" }, det.text)));
    if (det.history.length) kids.push(sec("이력", table(["시각", "사용자", "작업", "내용"], det.history.map((h) => [h.ts.replace("T", " ").slice(0, 19), h.user, h.action, h.detail]))));
  }
  if (sum.can_edit) {
    const msg = el("p", { class: "hint", role: "status" }, note || "");
    const editor = el("div", { class: "stack" }, aclForm("ed", sum.acl),
      el("div", { class: "row" },
        el("button", { type: "button", class: "primary", onclick: async () => {
          try { const r = await api("/api/docs/" + sum.id + "/acl", { method: "PUT", body: JSON.stringify({ acl: readAcl("ed") }) });
            await loadDocs($("doc-q").value.trim());
            const note = r.note || "권한을 저장했습니다."; if (r.id) showDoc(r, note); else renderDetail(r, null, note);
          } catch (e) { msg.textContent = "저장 실패: " + e.message; } } }, "권한 저장"),
        sum.managed ? el("button", { type: "button", class: "btn-danger", onclick: async () => {
          if (!window.confirmDel) { window.confirmDel = sum.id; msg.textContent = "한 번 더 누르면 삭제합니다."; return; }
          window.confirmDel = null;
          try { await api("/api/docs/" + sum.id, { method: "DELETE" }); curDoc = null; $("doc-detail").replaceChildren(el("p", { class: "hint" }, "삭제했습니다.")); loadDocs(); }
          catch (e) { msg.textContent = "삭제 실패: " + e.message; } } }, "삭제") : ""),
      msg);
    kids.push(sec("접근 권한 (소유자·관리자)", editor));
  }
  box.replaceChildren(...kids);
}
$("doc-search").addEventListener("submit", (e) => { e.preventDefault(); loadDocs($("doc-q").value.trim()); });
$("up-default").addEventListener("change", () => {
  const box = $("up-acl"); box.hidden = $("up-default").checked;
  if (!box.hidden && !box.children.length) box.append(aclForm("up", { roles: [], depts: me.dept ? [me.dept] : [], users: [], deny_users: [], level: me.level }));
});
$("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("up-file").files[0], msg = $("up-msg");
  if (!file) return;
  $("up-go").disabled = true; msg.textContent = "올리는 중...";
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let bin = ""; for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    const body = { filename: file.name, content_b64: btoa(bin) };
    if (!$("up-default").checked) body.acl = readAcl("up");
    const d = await api("/api/docs", { method: "POST", body: JSON.stringify(body) });
    msg.textContent = "올렸습니다: " + d.source; $("upload-form").reset(); $("up-acl").hidden = true;
    await loadDocs(); showDoc(d); refreshAlertCount();
  } catch (err) { msg.textContent = "실패: " + err.message; } finally { $("up-go").disabled = false; }
});

// ===================== alerts (automation) =====================
async function refreshAlertCount() {
  try { const a = await api("/api/alerts"); const n = a.reduce((x, d) => x + d.findings.length, 0);
    $("alert-count").textContent = String(n); $("alert-count").hidden = !n; } catch { /* ignore */ }
}
async function loadAlerts() {
  const list = await api("/api/alerts");
  $("alert-list").replaceChildren(...(list.length ? list.map((a) => el("div", { class: "card alert-card" + (a.findings.some((f) => f.severity === "high") ? "" : " medium") },
    el("h4", {}, a.title), el("div", { class: "ref" }, a.source),
    ...a.findings.map((f) => el("p", {}, el("span", { class: "pill " + SEV[f.severity][1] }, SEV[f.severity][0]), f.message, " ", el("span", { class: "ref" }, f.row))),
    el("button", { type: "button", onclick: () => { show("docs"); loadDocs().then(() => showDoc({ id: a.doc_id, readable: true, managed: false, can_edit: false, title: a.title, source: a.source })); } }, "문서 열기")))
    : [el("p", { class: "hint" }, "불일치 항목이 없습니다.")]));
}

boot();
