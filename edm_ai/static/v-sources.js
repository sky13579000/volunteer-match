import { api, dialog, el, empty, fail, fmtDate, fmtNum, go, pill, route, saveBlob, table, toast } from "./core.js";
import { icon } from "./icons.js";

const KIND = { path: "NAS · 파일서버 경로", ftp: "FTP", webdav: "WebDAV" };
const MODE = { reference: ["원본 유지 (가상 중앙화)", "info"], migrate: ["이관 (복사)", "warn"] };
const ST = { migrated: ["이관됨", "ok"], updated: ["새 버전", "ok"], unchanged: ["변경 없음", ""], duplicate: ["중복", "warn"], error: ["오류", "bad"], too_large: ["용량 초과", "warn"], blocked: ["차단 형식", "bad"], source_deleted: ["원본 삭제됨", "warn"] };

function resultDialog(title, r) {
  const rows = Object.entries(r).filter(([k, v]) => typeof v === "number" && v > 0 && k !== "pruned_elsewhere").map(([k, v]) => [{ added: "추가·변경", unchanged: "변경 없음", migrated: "이관", updated: "새 버전", duplicate: "중복", error: "오류", too_large: "용량 초과", blocked: "차단", excluded: "제외", source_deleted: "원본 삭제됨" }[k] || k, v]);
  const errs = [...(r.errors || []), ...(r.skipped || [])].slice(0, 30);
  return dialog(title, () => el("div", {}, r.error ? el("p", { class: "err" }, r.error) : "", r.dry_run ? el("p", { class: "hint" }, "미리보기입니다. 실제로는 아무것도 바꾸지 않았습니다.") : "",
    rows.length ? table(["항목", "건수"], rows) : empty("변경 사항이 없습니다", "inbox"), errs.length ? el("div", { class: "sec" }, el("h3", {}, "확인이 필요한 파일"), el("pre", { class: "body" }, errs.join("\n"))) : ""));
}

route(/^\/admin\/sources$/, async (view) => {
  const { items } = await api("/api/dms/admin/sources");
  const run = (s, dry) => async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try { const r = await api(`/api/dms/admin/sources/${encodeURIComponent(s.name)}/sync`, { method: "POST", body: { dry_run: dry } }); await resultDialog(dry ? `${s.name} · 미리보기` : `${s.name} · 동기화 결과`, r); go("#/admin/sources"); } catch (x) { fail(x); } finally { btn.disabled = false; }
  };
  const showItems = async (s) => {
    const r = await api(`/api/dms/admin/sources/${encodeURIComponent(s.name)}/items`);
    await dialog(`${s.name} · 파일별 결과`, () => r.items.length ? table(["경로", "상태", "비고"], r.items.map((i) => [el("span", { class: "ref" }, i.rel), pill(...(ST[i.status] || [i.status, ""])), i.note || ""])) : empty("기록이 없습니다", "inbox"));
  };
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "저장소 연동"), el("span", { class: "hint" }, "NAS · 파일서버 · FTP · WebDAV — 설정은 config.json 의 sources (docs/INTEGRATION.md)")),
    items.length ? el("div", { class: "stack" }, items.map((s) => el("div", { class: "card src" },
      el("div", { class: "row" }, icon("inbox", 20), el("b", {}, s.name), pill(KIND[s.type] || s.type), pill(...MODE[s.mode]), s.running ? pill("동기화 중", "info") : "", el("span", { class: "grow" }),
        el("button", { onclick: run(s, true) }, icon("eye", 16), "미리보기"), el("button", { class: "primary", onclick: run(s, false) }, icon("history", 16), "지금 동기화")),
      el("div", { class: "hint ref" }, s.where), el("div", { class: "statrow" }, el("div", { class: "stat" }, el("span", { class: "hint" }, s.mode === "migrate" ? "이관된 문서" : "연결된 문서"), el("b", {}, fmtNum(s.documents))),
        el("div", { class: "stat" }, el("span", { class: "hint" }, "자동 동기화"), el("b", {}, s.schedule_minutes ? `${s.schedule_minutes}분마다` : "수동")),
        el("div", { class: "stat" }, el("span", { class: "hint" }, "마지막 실행"), el("b", {}, s.last ? fmtDate(s.last.ts) : "-"), s.last ? pill(s.last.ok ? "성공" : "확인 필요", s.last.ok ? "ok" : "bad") : "")),
      s.mode === "migrate" ? el("div", { class: "row" }, el("button", { class: "sm", onclick: () => showItems(s) }, "파일별 결과 보기")) : "",
      s.last?.stats?.error ? el("p", { class: "err" }, s.last.stats.error) : ""))) : el("div", { class: "card" }, empty("설정된 저장소가 없습니다. config.json 의 sources 에 NAS 경로(UNC 또는 마운트), FTP, WebDAV 를 추가하세요.", "inbox")),
    el("p", { class: "hint" }, "원본 유지: 파일은 NAS에 그대로 두고 검색·AI·연결만 합니다(문서를 열면 항상 현재의 원본). 이관: 문서함으로 복사해 버전·결재·권한·보존을 적용합니다. 원본은 어떤 경우에도 수정·삭제하지 않습니다."));
});
