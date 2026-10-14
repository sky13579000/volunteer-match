import { icon } from "./icons.js";
// Shared UI toolkit. Every piece of server data goes through textContent / DOM nodes, never innerHTML.
export const state = { cfg: {}, me: null, meta: null };

export const $ = (sel, root = document) => root.querySelector(sel);

export function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === false || v === null || v === undefined) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "value" || k === "checked" || k === "disabled" || k === "selected") n[k] = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) if (c !== null && c !== undefined && c !== false) n.append(c);
  return n;
}
export const text = (s) => document.createTextNode(String(s ?? ""));

// ---------- formatting ----------
export const fmtDate = (iso) => (iso ? iso.replace("T", " ").slice(0, 16) : "");
export const fmtDay = (iso) => (iso ? iso.slice(0, 10) : "");
export const fmtSize = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
export const fmtNum = (n) => Number(n).toLocaleString("ko-KR");
export const STATUS = { draft: ["작성 중", "warn"], in_review: ["결재 중", "info"], approved: ["승인", "ok"], rejected: ["반려", "bad"], expired: ["만료", ""], superseded: ["이전 버전", ""] };
export const pill = (label, cls = "") => el("span", { class: "pill " + cls }, label);
export const statusPill = (s) => { const [l, c] = STATUS[s] || [s, ""]; return pill(l, c); };
export const typeName = (code) => (state.meta?.doc_types.find((t) => t.code === code)?.name) || (code === "EXT" ? "외부 문서" : code);
export const userName = (id) => state.meta?.users.find((u) => u.id === id)?.name || id;

// ---------- api ----------
export function authHeaders() {
  const h = {};
  try {
    if (state.cfg.auth_mode === "proxy") return h;            // identity comes from the SSO proxy, never from the browser
    if (state.cfg.auth_mode === "token") h.Authorization = "Bearer " + (sessionStorage.getItem("token") || "");
    else h["X-User-Id"] = sessionStorage.getItem("uid") || "";
  } catch { /* storage blocked */ }
  return h;
}
export async function api(path, { method = "GET", body, raw = false } = {}) {
  const r = await fetch(path, { method, headers: authHeaders(), body: body === undefined ? undefined : JSON.stringify(body) });
  if (raw && r.ok) return r;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || r.statusText), { status: r.status });
  return data;
}
export async function saveBlob(path, opts, fallbackName = "download") {
  const r = await api(path, { ...opts, raw: true });
  const m = /filename\*=UTF-8''([^;]+)/.exec(r.headers.get("Content-Disposition") || "");
  const url = URL.createObjectURL(await r.blob());
  const a = el("a", { href: url, download: m ? decodeURIComponent(m[1]) : fallbackName });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
export async function fileToB64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

// ---------- feedback ----------
export function toast(msg, kind = "") {
  const t = el("div", { class: "toast " + kind, role: "status" }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 6000 : 3500);
}
export const fail = (e) => toast(e.message || String(e), "bad");

/** Modal dialog. `build(close)` returns the body node; actions: [{label, primary, danger, run}] (run returns false to stay open). */
export function dialog(title, build, actions = [{ label: "닫기" }]) {
  return new Promise((resolve) => {
    const dlg = el("dialog", { "aria-label": title });
    const close = (v) => { dlg.close(); dlg.remove(); resolve(v); };
    const body = el("div", { class: "dlg-b" }, build(close));
    const foot = el("div", { class: "dlg-f" }, actions.map((a) => el("button", { class: (a.primary ? "primary " : "") + (a.danger ? "danger" : ""), type: "button",
      onclick: async (ev) => { ev.target.disabled = true; try { const r = a.run ? await a.run(close) : undefined; if (r !== false) close(a.value ?? true); } catch (e) { fail(e); } finally { ev.target.disabled = false; } } }, a.label)));
    dlg.append(el("div", { class: "dlg-h" }, title), body, foot);
    dlg.addEventListener("cancel", () => resolve(false));
    document.body.append(dlg);
    dlg.showModal();
  });
}
export const confirmDialog = (msg, label = "확인", danger = false) =>
  dialog("확인", () => el("p", { class: "flush" }, msg), [{ label: "취소", value: false }, { label, primary: !danger, danger, value: true }]);

// ---------- router ----------
const routes = [];
export const route = (re, fn) => routes.push([re, fn]);
export const go = (hash) => { if (location.hash === hash) render(); else location.hash = hash; };
export function parseHash() {
  const h = location.hash.replace(/^#/, "") || "/dashboard";
  const [path, qs] = h.split("?");
  return { path, params: Object.fromEntries(new URLSearchParams(qs || "")) };
}
export async function render() {
  const { path, params } = parseHash();
  const view = $("#view");
  for (const [re, fn] of routes) {
    const m = re.exec(path);
    if (m) {
      state.onRoute?.(path, params);
      view.replaceChildren(el("p", { class: "hint" }, "불러오는 중..."));
      try { await fn(view, m, params); } catch (e) { view.replaceChildren(el("p", { class: "err" }, e.message)); if (e.status === 401) location.reload(); }
      view.focus({ preventScroll: true });
      return;
    }
  }
  view.replaceChildren(el("p", { class: "err" }, "페이지를 찾을 수 없습니다"));
}

// ---------- small components ----------
export function table(headers, rows, { onRow, cls = "" } = {}) {
  return el("div", { class: "scroll" }, el("table", { class: "tbl " + cls },
    el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h)))),
    el("tbody", {}, rows.map((r) => { const cells = r.cells || r; return el("tr", { class: onRow ? "click" : "", onclick: onRow && (() => onRow(r)) },
      cells.map((c, i) => el("td", { class: typeof c === "number" ? "num" : "" }, typeof c === "number" ? fmtNum(c) : c))); }))));
}
export const field = (label, input, hint) => el("div", {}, el("label", {}, label), input, hint && el("div", { class: "hint" }, hint));
export const empty = (msg, ic = "inbox") => el("div", { class: "empty-state" }, icon(ic, 34), el("span", {}, msg));
export function setNavBadge(name, n) { const b = document.getElementById("nb-" + name); if (b) { b.textContent = String(n); b.hidden = !n; } }

/** Permission entries editor. entries: [{type: user|dept|role|all|deny, id, role}] */
export function aclEditor(entries, { allowDeny = true } = {}) {
  const rows = el("div");
  const users = state.meta?.users || [];
  const ids = { user: users.map((u) => u.id), dept: [...new Set(users.map((u) => u.dept).filter(Boolean))], role: ["admin", "finance", "hr"], all: ["*"], deny: users.map((u) => u.id) };
  const dl = el("datalist", { id: "acl-ids" }, [...new Set(Object.values(ids).flat())].map((v) => el("option", { value: v })));
  function add(e = { type: "user", id: "", role: "viewer" }) {
    const type = el("select", { "aria-label": "대상 종류" }, [["user", "사용자"], ["dept", "부서"], ["role", "역할"], ["all", "전체 직원"], ...(allowDeny ? [["deny", "제외"]] : [])]
      .map(([v, l]) => el("option", { value: v, selected: v === e.type }, l)));
    const id = el("input", { value: e.id === "*" ? "" : e.id, list: "acl-ids", placeholder: "ID", "aria-label": "대상 ID" });
    const role = el("select", { "aria-label": "권한" }, [["viewer", "열람"], ["editor", "편집"], ["manager", "관리"]].map(([v, l]) => el("option", { value: v, selected: v === e.role }, l)));
    const sync = () => { id.disabled = type.value === "all"; role.disabled = type.value === "deny"; };
    type.addEventListener("change", sync); sync();
    const row = el("div", { class: "acl-row" }, type, id, role, el("button", { type: "button", class: "sm", "aria-label": "삭제", onclick: () => row.remove() }, "✕"));
    row.get = () => ({ type: type.value, id: type.value === "all" ? "*" : id.value.trim(), ...(type.value === "deny" ? {} : { role: role.value }) });
    rows.append(row);
  }
  entries.forEach(add);
  const node = el("div", {}, dl, rows, el("button", { type: "button", class: "sm", onclick: () => add() }, "+ 권한 추가"));
  node.get = () => [...rows.children].map((r) => r.get()).filter((e) => e.type === "all" || e.id);
  return node;
}

// ---------- minimal, safe Markdown -> DOM (headings, lists, checklists, tables, quotes, code, bold/italic/code/links) ----------
function inline(text) {
  const out = [];
  const re = /(\*\*([^*]+)\*\*|\*([^*\n]+)\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\))/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[2]) out.push(el("strong", {}, m[2]));
    else if (m[3]) out.push(el("em", {}, m[3]));
    else if (m[4]) out.push(el("code", {}, m[4]));
    else out.push(el("a", { href: m[6], target: "_blank", rel: "noopener noreferrer" }, m[5]));
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
export function renderMd(src) {
  const root = el("div", { class: "md" });
  const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  const isTableSep = (s) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(s || "");
  const cells = (s) => s.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  while (i < lines.length) {
    const ln = lines[i];
    if (!ln.trim()) { i++; continue; }
    if (/^```/.test(ln)) {
      const buf = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++; root.append(el("pre", { class: "code" }, buf.join("\n"))); continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(ln);
    if (h) { root.append(el("h" + (h[1].length + 1), {}, inline(h[2]))); i++; continue; }
    if (/^\s*(---|\*\*\*)\s*$/.test(ln)) { root.append(el("hr")); i++; continue; }
    if (ln.includes("|") && isTableSep(lines[i + 1])) {
      const head = cells(ln); i += 2; const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      root.append(el("div", { class: "scroll" }, el("table", { class: "tbl" }, el("thead", {}, el("tr", {}, head.map((c) => el("th", {}, inline(c))))),
        el("tbody", {}, rows.map((r) => el("tr", {}, head.map((_, k) => el("td", {}, inline(r[k] || ""))))))))); continue;
    }
    if (/^>\s?/.test(ln)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ""));
      root.append(el("blockquote", {}, inline(buf.join(" ")))); continue;
    }
    const li = /^(\s*)([-*]|\d+\.)\s+(.*)$/;
    if (li.test(ln)) {
      const ordered = /\d+\./.test(li.exec(ln)[2]); const items = [];
      while (i < lines.length && li.test(lines[i])) {
        const t = li.exec(lines[i++])[3]; const cb = /^\[( |x|X)\]\s+(.*)$/.exec(t);
        items.push(cb ? el("li", { class: "task" }, el("input", { type: "checkbox", disabled: true, checked: cb[1] !== " " }), " ", inline(cb[2])) : el("li", {}, inline(t)));
      }
      root.append(el(ordered ? "ol" : "ul", {}, items)); continue;
    }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|>\s?|\s*([-*]|\d+\.)\s+)/.test(lines[i]) && !(lines[i].includes("|") && isTableSep(lines[i + 1]))) buf.push(lines[i++]);
    root.append(el("p", {}, inline(buf.join("\n"))));
  }
  return root;
}

// ---------- presentation helpers ----------
export const avatar = (name, cls = "") => el("span", { class: "avatar sm " + cls, "aria-hidden": "true" }, String(name || "?").slice(0, 1));
export function relTime(iso) {
  if (!iso) return "";
  const t = new Date(iso.length <= 10 ? iso + "T00:00:00Z" : iso).getTime(), s = (Date.now() - t) / 1000;
  if (!isFinite(s)) return iso;
  if (s < 90) return "방금 전"; if (s < 3600) return `${Math.round(s / 60)}분 전`; if (s < 86400) return `${Math.round(s / 3600)}시간 전`;
  if (s < 86400 * 8) return `${Math.round(s / 86400)}일 전`; return fmtDay(iso);
}
const EXT = { pdf: ["pdf", "red"], doc: ["file", "blue"], docx: ["file", "blue"], hwp: ["file", "blue"], xls: ["chart", "green"], xlsx: ["chart", "green"], csv: ["chart", "green"], md: ["file", "slate"], txt: ["file", "slate"],
  eml: ["inbox", "orange"], png: ["eye", "purple"], jpg: ["eye", "purple"], jpeg: ["eye", "purple"], zip: ["layers", "slate"] };
/** Coloured file-type tile. Pass a filename or an extension. */
export function fileTile(name, size = 38) {
  const ext = (String(name || "").split(".").pop() || "").toLowerCase();
  const [ic, tone] = EXT[ext] || ["file", "slate"];
  return el("span", { class: "ftile " + tone, "data-ext": ext.slice(0, 4).toUpperCase() }, icon(ic, Math.round(size * 0.5)));
}
/** Small dropdown menu anchored to a button. items: [{label, icon, run, danger}] or "-" for a divider. */
export function menu(label, items, { ic = "chevron", cls = "" } = {}) {
  const wrap = el("div", { class: "menu-wrap" });
  const pop = el("div", { class: "menu", role: "menu", hidden: true });
  const close = () => { pop.hidden = true; document.removeEventListener("click", onDoc, true); };
  const onDoc = (e) => { if (!wrap.contains(e.target)) close(); };
  for (const it of items) {
    if (it === "-") { pop.append(el("hr")); continue; }
    pop.append(el("button", { type: "button", role: "menuitem", class: "menu-i" + (it.danger ? " danger" : ""), onclick: () => { close(); Promise.resolve(it.run()).catch(fail); } }, it.icon ? icon(it.icon, 16) : "", it.label));
  }
  const btn = el("button", { type: "button", class: cls, "aria-haspopup": "menu", onclick: () => { if (pop.hidden) { pop.hidden = false; document.addEventListener("click", onDoc, true); } else close(); } }, label, icon("chevron", 14));
  wrap.append(btn, pop);
  return wrap;
}

export const deptName = (id) => state.meta?.departments?.[id] || id || "-";
export const fmtHours = (h) => (h == null ? "-" : h < 1 ? "1시간 미만" : h < 48 ? `${h}시간` : `${Math.round(h / 24)}일`);
