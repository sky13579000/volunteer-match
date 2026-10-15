import { api, el, empty, fail, fileToB64, fmtSize, go, pill, route, table } from "./core.js";
import { icon } from "./icons.js";

const STATE = { latest: ["최신본입니다", "ok", "check"], outdated: ["이전 버전입니다", "warn", "alert"], modified: ["수정된 사본입니다", "warn", "alert"], unknown: ["찾을 수 없습니다", "", "search"] };

route(/^\/verify$/, async (view) => {
  const out = el("div", { class: "sec", id: "verify-out", "aria-live": "polite" });
  const file = el("input", { type: "file", id: "verify-file", "aria-label": "확인할 파일" });
  const sha = el("input", { id: "verify-sha", placeholder: "또는 SHA-256 64자리를 붙여 넣기", "aria-label": "SHA-256" });
  const show = (r) => {
    const [label, tone, ic] = STATE[r.state];
    out.replaceChildren(el("div", { class: "card verify-" + r.state }, el("div", { class: "row" }, icon(ic, 22), el("h3", { class: "flush" }, label), pill(r.state === "latest" ? "OK" : r.state === "unknown" ? "?" : "확인 필요", tone)),
      el("p", {}, r.message), el("p", { class: "hint ref" }, "SHA-256 " + r.sha256),
      r.matches.length ? table(["문서번호", "제목", "이 파일", "현재 최신", "근거", ""], r.matches.map((m) => [el("span", { class: "ref" }, m.doc_no), m.title, "v" + m.ver, m.latest_ver ? "v" + m.latest_ver : "-", m.via,
        el("button", { class: "sm", onclick: () => go("#/doc/" + m.doc_id) }, "문서 열기")])) : empty("일치하는 문서가 없습니다", "search")));
  };
  const go_ = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      if (file.files[0]) show(await api("/api/dms/verify-copy", { method: "POST", body: { filename: file.files[0].name, content_b64: await fileToB64(file.files[0]) } }));
      else if (sha.value.trim()) show(await api("/api/dms/verify-copy", { method: "POST", body: { sha256: sha.value.trim() } }));
      else throw new Error("파일을 선택하거나 해시를 입력하세요");
    } catch (x) { fail(x); } finally { btn.disabled = false; }
  };
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "사본 확인"), el("span", { class: "hint" }, "메일·USB·공유 폴더로 받은 파일이 최신본인지 확인합니다")),
    el("div", { class: "card" }, el("div", { class: "row" }, icon("upload", 18), file, el("button", { class: "primary", id: "verify-go", onclick: go_ }, icon("search", 16), "최신본 확인")), el("div", { class: "sec" }, sha),
      el("p", { class: "hint" }, "파일 내용(SHA-256)을 이 플랫폼의 모든 버전과 비교합니다. 플랫폼에서 내려받은 Office·PDF 사본에는 문서번호·버전 표식이 들어 있어, 내용이 수정된 사본도 어느 문서에서 나왔는지 알 수 있습니다. 열람 권한이 없는 문서는 결과에 나오지 않습니다.")), out);
});
