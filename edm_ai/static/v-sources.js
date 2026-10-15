import { api, confirmDialog, dialog, el, empty, fail, field, fmtDate, fmtNum, go, pill, route, saveBlob, state, table, toast } from "./core.js";
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
  const [{ items }, conns] = await Promise.all([api("/api/dms/admin/sources"), api("/api/dms/admin/connections")]);
  const spec = (n) => conns.sources.find((c) => c.name === n);
  const run = (s, dry) => async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try { const r = await api(`/api/dms/admin/sources/${encodeURIComponent(s.name)}/sync`, { method: "POST", body: { dry_run: dry } }); await resultDialog(dry ? `${s.name} · 미리보기` : `${s.name} · 동기화 결과`, r); go("#/admin/sources"); } catch (x) { fail(x); } finally { btn.disabled = false; }
  };
  const showItems = async (s) => {
    const r = await api(`/api/dms/admin/sources/${encodeURIComponent(s.name)}/items`);
    await dialog(`${s.name} · 파일별 결과`, () => r.items.length ? table(["경로", "상태", "비고"], r.items.map((i) => [el("span", { class: "ref" }, i.rel), pill(...(ST[i.status] || [i.status, ""])), i.note || ""])) : empty("기록이 없습니다", "inbox"));
  };
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "저장소 연동"), el("span", { class: "grow" }), el("button", { class: "primary", id: "src-add", onclick: () => sourceDialog(null).catch(fail) }, icon("plus", 16), "연결 추가")),
    items.length ? el("div", { class: "stack" }, items.map((s) => el("div", { class: "card src" },
      el("div", { class: "row" }, icon("inbox", 20), el("b", {}, s.name), pill(KIND[s.type] || s.type), pill(...MODE[s.mode]), s.running ? pill("동기화 중", "info") : "", el("span", { class: "grow" }),
        el("button", { onclick: () => spec(s.name) && sourceDialog(spec(s.name)).catch(fail) }, icon("settings", 16), "설정"), el("button", { onclick: run(s, true) }, icon("eye", 16), "미리보기"), el("button", { class: "primary", onclick: run(s, false) }, icon("history", 16), "지금 동기화")),
      el("div", { class: "hint ref" }, s.where), el("div", { class: "statrow" }, el("div", { class: "stat" }, el("span", { class: "hint" }, s.mode === "migrate" ? "이관된 문서" : "연결된 문서"), el("b", {}, fmtNum(s.documents))),
        el("div", { class: "stat" }, el("span", { class: "hint" }, "자동 동기화"), el("b", {}, s.schedule_minutes ? `${s.schedule_minutes}분마다` : "수동")),
        el("div", { class: "stat" }, el("span", { class: "hint" }, "마지막 실행"), el("b", {}, s.last ? fmtDate(s.last.ts) : "-"), s.last ? pill(s.last.ok ? "성공" : "확인 필요", s.last.ok ? "ok" : "bad") : "")),
      s.mode === "migrate" ? el("div", { class: "row" }, el("button", { class: "sm", onclick: () => showItems(s) }, "파일별 결과 보기")) : "",
      s.last?.stats?.error ? el("p", { class: "err" }, s.last.stats.error) : ""))) : el("div", { class: "card" }, empty("연결된 저장소가 없습니다. 위의 '연결 추가'로 NAS 경로, FTP, WebDAV 를 연결하세요.", "inbox")),
    el("p", { class: "hint" }, "원본 유지: 파일은 NAS에 그대로 두고 검색·AI·연결만 합니다(문서를 열면 항상 현재의 원본). 이관: 문서함으로 복사해 버전·결재·권한·보존을 적용합니다. 원본은 어떤 경우에도 수정·삭제하지 않습니다."));
});

// ---------- add / edit a storage connection from the screen ----------
export async function sourceDialog(existing) {
  const e = existing || {};
  const inp = (id, val = "", ph = "") => el("input", { id, value: val ?? "", placeholder: ph, "aria-label": id });
  const name = inp("cs-name", e.name, "예: 본부NAS"), path = inp("cs-path", e.path, "\\\\nas01\\share\\문서  또는  Z:\\문서  또는  /mnt/nas"), host = inp("cs-host", e.host, "ftp.example.com"), port = inp("cs-port", e.port || "", "21"), url = inp("cs-url", e.url, "https://nas.example/webdav/");
  const user = inp("cs-user", e.user), pw = el("input", { id: "cs-pw", type: "password", autocomplete: "new-password", placeholder: e.has_password ? "(저장됨 — 비워 두면 유지)" : "" });
  const type = el("select", { id: "cs-type" }, [["path", "NAS · 파일서버 폴더 (UNC/드라이브/마운트)"], ["ftp", "FTP"], ["webdav", "WebDAV"]].map(([v, l]) => el("option", { value: v, selected: v === (e.type || "path") }, l)));
  const mode = el("select", { id: "cs-mode" }, [["reference", "원본 유지 (색인·AI 검색만, 파일은 NAS에 그대로)"], ["migrate", "이관 (문서함으로 복사 — 버전·결재·권한·보존 적용)"]].map(([v, l]) => el("option", { value: v, selected: v === (e.mode || "reference") }, l)));
  const include = inp("cs-inc", (e.include || []).join(", "), "*.docx, *.hwp, *.pdf (비우면 지원 형식 전체)"), mb = inp("cs-mb", e.max_mb || "", "100"), sched = inp("cs-sched", e.schedule_minutes || "", "60 (비우면 수동)");
  const depts = inp("cs-depts", (e.acl?.depts || []).join(", "), "mice, finance (부서 ID, 비우면 관리자만)"), level = inp("cs-level", e.acl?.level ?? 1, "1"), folder = inp("cs-folder", e.folder, "이관할 문서함 폴더 이름");
  const rows = { path: [field("폴더 경로", path, "서버 PC에서 열리는 경로여야 합니다")], ftp: [el("div", { class: "grid2" }, field("FTP 서버", host), field("포트", port)), field("시작 폴더", inp("cs-ftppath", e.type === "ftp" ? e.path : "", "/share"))], webdav: [field("WebDAV 주소", url)] };
  const typeBox = el("div", {}), creds = el("div", { class: "grid2" }, field("계정", user), field("비밀번호", pw));
  const tls = el("input", { type: "checkbox", id: "cs-tls", checked: !!e.tls });
  const sync = () => { typeBox.replaceChildren(...rows[type.value], type.value === "ftp" ? el("label", { class: "inline" }, tls, "FTPS(TLS) 사용") : ""); creds.hidden = type.value === "path"; };
  type.addEventListener("change", sync); sync();
  const build = () => {
    const t = type.value, o = { name: name.value.trim(), type: t, mode: mode.value, include: include.value, max_mb: mb.value || undefined, schedule_minutes: sched.value || undefined,
      acl: { depts: depts.value.split(",").map((x) => x.trim()).filter(Boolean), level: Number(level.value || 1) } };
    if (!o.acl.depts.length) o.acl = { roles: ["admin"], level: 99 };
    if (t === "path") o.path = path.value.trim();
    if (t === "ftp") { o.host = host.value.trim(); o.port = port.value || undefined; o.tls = tls.checked; o.path = document.getElementById("cs-ftppath").value || "/"; }
    if (t === "webdav") o.url = url.value.trim();
    if (t !== "path") { o.user = user.value; o.password = pw.value; }
    if (o.mode === "migrate") { o.folder = folder.value.trim() || o.name; o.owner = "admin"; }
    return o;
  };
  const result = el("div", { class: "sec", id: "cs-result", "aria-live": "polite" });
  await dialog(existing ? `저장소 편집 · ${e.name}` : "저장소 연결 추가", () => el("div", { class: "stack" },
    el("p", { class: "hint" }, "NAS·파일서버는 서버 PC에서 그 경로가 열려야 합니다(윈도우: UNC \\\\서버\\공유 또는 연결된 드라이브). 먼저 '연결 테스트'로 확인하세요."),
    field("이름", name), field("종류", type), typeBox, creds, field("처리 방식", mode), el("div", { class: "grid2" }, field("파일 형식(선택)", include), field("최대 크기 MB", mb)),
    el("div", { class: "grid2" }, field("자동 동기화(분)", sched), field("읽을 수 있는 부서", depts)), field("보안등급 (이 등급 이하 사용자)", level), field("이관 폴더 이름 (이관 방식)", folder), result),
    [{ label: "취소", value: false },
      ...(existing && e.origin === "ui" ? [{ label: "삭제", danger: true, run: async () => { if (!(await confirmDialog(`'${e.name}' 연결을 삭제할까요? 이미 가져온 문서는 남습니다.`, "삭제", true))) return false; await api(`/api/dms/admin/connections/source/${encodeURIComponent(e.name)}`, { method: "DELETE" }); toast("삭제했습니다"); go("#/admin/sources"); } }] : []),
      { label: "연결 테스트", run: async () => { result.replaceChildren(el("span", { class: "hint" }, "확인 중…")); const r = await api("/api/dms/admin/connections/source/test", { method: "POST", body: build() }); result.replaceChildren(pill(r.ok ? "성공" : "실패", r.ok ? "ok" : "bad"), " ", r.message, r.sample?.length ? el("pre", { class: "body" }, r.sample.join("\n")) : ""); return false; } },
      { label: "저장", primary: true, run: async () => { await api("/api/dms/admin/connections/source", { method: "PUT", body: build() }); toast("저장했습니다. '지금 동기화'로 문서를 가져오세요"); go("#/admin/sources"); } }]);
}
