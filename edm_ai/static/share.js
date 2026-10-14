"use strict";
const $ = (id) => document.getElementById(id);
const token = location.pathname.split("/").pop();
let password = null;

async function call(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", body: JSON.stringify(body) });
  if (r.headers.get("Content-Disposition")) return r;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.error || r.statusText), { status: r.status });
  return data;
}
function show(d) {
  $("pw").hidden = true;
  $("doc").hidden = false;
  $("title").textContent = d.title;
  $("meta").textContent = `${d.doc_no} · ${d.filename} · ${(d.size / 1024).toFixed(1)} KB`;
  $("exp").textContent = "유효기간 " + d.expires.slice(0, 10) + " 까지";
  $("text").textContent = d.text || "(미리볼 수 있는 텍스트가 없습니다)";
  $("dl").hidden = d.perm !== "download";
  $("left").textContent = d.downloads_left === null ? "" : `남은 다운로드 ${d.downloads_left}회`;
}
async function boot() {
  try {
    const info = await call("/api/share/" + encodeURIComponent(token));
    if (info.requires_password) { $("pw").hidden = false; $("pw-input").focus(); } else show(info);
  } catch (e) { $("err").textContent = e.message; }
}
$("pw-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("err").textContent = "";
  password = $("pw-input").value;
  try { show(await call(`/api/share/${encodeURIComponent(token)}/access`, { password })); }
  catch (e) { $("err").textContent = e.message; password = null; }
});
$("dl").addEventListener("click", async () => {
  $("err").textContent = "";
  try {
    const r = await call(`/api/share/${encodeURIComponent(token)}/download`, { password });
    if (!(r instanceof Response)) return;
    const m = /filename\*=UTF-8''([^;]+)/.exec(r.headers.get("Content-Disposition") || "");
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = url; a.download = m ? decodeURIComponent(m[1]) : "download";
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    const left = $("left").textContent.match(/\d+/);
    if (left) $("left").textContent = `남은 다운로드 ${Math.max(0, Number(left[0]) - 1)}회`;
  } catch (e) { $("err").textContent = e.message; }
});
boot();
