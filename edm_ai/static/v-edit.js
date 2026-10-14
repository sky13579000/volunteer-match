import { api, el, empty, fail, field, go, renderMd, route, state, toast } from "./core.js";
import { icon } from "./icons.js";
import { folderOptions } from "./v-docs.js";

/** Split editor: textarea on the left, live preview on the right. */
function editor(initial, onChange) {
  const ta = el("textarea", { class: "md-src", "aria-label": "본문 (Markdown)", spellcheck: "false" });
  ta.value = initial;
  const prev = el("div", { class: "md-prev card" });
  const sync = () => { prev.replaceChildren(renderMd(ta.value)); onChange?.(ta.value); };
  ta.addEventListener("input", sync);
  const tool = (label, fn) => el("button", { type: "button", class: "sm", onclick: () => { const s = ta.selectionStart, e = ta.selectionEnd; const r = fn(ta.value.slice(s, e)); ta.setRangeText(r, s, e, "select"); ta.focus(); sync(); } }, label);
  const bar = el("div", { class: "row md-tools" }, tool("H2", (t) => `\n## ${t || "제목"}\n`), tool("굵게", (t) => `**${t || "텍스트"}**`), tool("목록", (t) => `\n- ${t || "항목"}\n`),
    tool("체크", (t) => `\n- [ ] ${t || "항목"}\n`), tool("표", () => "\n| 항목 | 내용 |\n|---|---|\n|  |  |\n"));
  sync();
  return { node: el("div", {}, bar, el("div", { class: "md-split" }, ta, prev)), get: () => ta.value };
}

route(/^\/new$/, async (view, _m, params) => {
  const [{ templates }, tree] = await Promise.all([api("/api/dms/templates"), api("/api/dms/folders")]);
  const folders = folderOptions(tree, { registerable: true });
  const chosen = templates.find((t) => t.id === params.template);
  if (!chosen) {
    view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "양식으로 새 문서 작성"), el("span", { class: "grow" }), el("button", { onclick: () => go("#/docs") }, "취소")),
      el("p", { class: "hint" }, "양식을 고르면 입력 폼과 편집기가 열립니다. 저장하면 문서번호가 부여되고 문서유형의 규칙(결재 등)이 그대로 적용됩니다."),
      el("div", { class: "grid2" }, templates.map((t) => el("button", { class: "tpl card", onclick: () => go("#/new?template=" + t.id) }, el("span", { class: "tpl-ic" }, icon("file", 22)),
        el("span", {}, el("b", {}, t.name), el("span", { class: "hint tpl-d" }, t.description))))),
      templates.length ? "" : empty("사용 가능한 양식이 없습니다"));
    return;
  }
  const values = {};
  const title = el("input", { id: "n-title", value: chosen.name });
  const folder = el("select", { id: "n-folder", "aria-label": "폴더" }, folders.map((o) => el("option", { value: o.id }, o.label)));
  let ed;
  const refresh = async () => {
    try { const r = await api("/api/dms/templates/render", { method: "POST", body: { template_id: chosen.id, values, title: title.value } }); ed.set(r.text); } catch (e) { fail(e); }
  };
  const fields = chosen.fields.map((f) => { const inp = el("input", { placeholder: f.placeholder || "", "aria-label": f.label }); inp.addEventListener("change", () => { values[f.key] = inp.value; refresh(); }); return field(f.label, inp); });
  const base = await api("/api/dms/templates/render", { method: "POST", body: { template_id: chosen.id, values, title: chosen.name } });
  const e = editor(base.text);
  ed = { set: (t) => { const ta = e.node.querySelector("textarea"); ta.value = t; ta.dispatchEvent(new Event("input")); } };
  const save = el("button", { class: "primary", onclick: async (ev) => {
    ev.target.disabled = true;
    try {
      const stem = title.value.trim().replace(/[\\/:*?"<>|\s]+/g, "_").slice(0, 60) || "문서";
      const bytes = new TextEncoder().encode(e.get());
      let bin = ""; for (let k = 0; k < bytes.length; k += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(k, k + 0x8000));
      const d = await api("/api/docs", { method: "POST", body: { filename: stem + ".md", content_b64: btoa(bin), folder_id: Number(folder.value), doc_type: base.doc_type, title: title.value.trim(), tags: base.tags } });
      toast(`저장했습니다: ${d.doc_no}` + (d.status === "draft" ? " (결재 요청이 필요합니다)" : "")); go("#/doc/" + d.id);
    } catch (x) { fail(x); } finally { ev.target.disabled = false; }
  } }, icon("check", 16), "문서로 저장");
  view.replaceChildren(
    el("div", { class: "page-h" }, el("h1", {}, chosen.name), el("span", { class: "grow" }), el("button", { onclick: () => go("#/new") }, "다른 양식"), save),
    el("div", { class: "card" }, el("div", { class: "grid2" }, field("제목", title), field("저장 폴더", folder)), el("div", { class: "grid2" }, fields)),
    el("div", { class: "sec" }, e.node));
});

route(/^\/doc\/(\d+)\/edit$/, async (view, m) => {
  const id = Number(m[1]);
  const info = await api(`/api/docs/${id}/text`);
  const comment = el("input", { placeholder: "변경 내용 (선택)", id: "e-comment" });
  const e = editor(info.text);
  const save = el("button", { class: "primary", onclick: async (ev) => {
    ev.target.disabled = true;
    try { await api(`/api/docs/${id}/text`, { method: "PUT", body: { text: e.get(), comment: comment.value } }); toast("새 버전으로 저장했습니다"); go("#/doc/" + id); }
    catch (x) { fail(x); } finally { ev.target.disabled = false; }
  } }, icon("check", 16), "새 버전으로 저장");
  view.replaceChildren(
    el("div", { class: "page-h" }, el("h1", {}, "편집: " + info.title), el("span", { class: "hint" }, `${info.doc_no} · 현재 v${info.ver} → v${info.ver + 1}`), el("span", { class: "grow" }),
      el("button", { onclick: () => go("#/doc/" + id) }, "취소"), save),
    info.locked_by && info.locked_by !== state.me.id ? el("p", { class: "err" }, `${info.locked_by} 님이 체크아웃 중이라 저장할 수 없습니다.`) : "",
    el("div", { class: "card" }, field("변경 내용", comment)), el("div", { class: "sec" }, e.node));
});
