import { api, confirmDialog, dialog, el, empty, fail, field, fmtDate, fmtNum, go, pill, route, table, toast } from "./core.js";
import { icon } from "./icons.js";

const LBL = { registered: "메일 등록", attachments: "첨부 등록", duplicate: "중복", unknown_sender: "사용자 미매칭", not_allowed: "허용되지 않은 발신자", blocked: "차단", error: "오류", seen: "확인한 메일" };
const ST = { registered: ["등록됨", "ok"], duplicate: ["중복", "warn"], blocked: ["차단", "bad"], error: ["오류", "bad"], not_allowed: ["허용되지 않은 발신자", "warn"], unknown_sender: ["사용자 미매칭", "warn"], too_large: ["용량 초과", "warn"] };
const REG = { message: "본문만", attachments: "첨부만", both: "본문 + 첨부" };

route(/^\/admin\/mail$/, async (view) => {
  const [{ items }, conns] = await Promise.all([api("/api/dms/admin/mail"), api("/api/dms/admin/connections")]);
  const spec = (n) => conns.mail.find((c) => c.name === n);
  const run = (a, dry) => async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const r = await api(`/api/dms/admin/mail/${encodeURIComponent(a.name)}/run`, { method: "POST", body: { dry_run: dry } });
      const rows = Object.entries(r).filter(([, v]) => typeof v === "number" && v > 0).map(([k, v]) => [LBL[k] || k, v]);
      await dialog(`${a.name} · ${dry ? "미리보기" : "수집 결과"}`, () => el("div", {}, r.error ? el("p", { class: "err" }, r.error) : "", dry ? el("p", { class: "hint" }, "미리보기입니다. 메일을 읽음 처리하거나 등록하지 않았습니다.") : "", rows.length ? table(["항목", "건수"], rows) : empty("새 메일이 없습니다", "inbox")));
      go("#/admin/mail");
    } catch (x) { fail(x); } finally { btn.disabled = false; }
  };
  const show = async (a) => {
    const r = await api(`/api/dms/admin/mail/${encodeURIComponent(a.name)}/items`);
    await dialog(`${a.name} · 메일별 결과`, () => r.items.length ? table(["제목", "보낸 사람", "상태", "문서", "비고"], r.items.map((i) => [i.subject || "(제목 없음)", i.sender, pill(...(ST[i.status] || [i.status, ""])), (i.docs || []).length ? `${i.docs.length}건` : "", i.note || ""])) : empty("기록이 없습니다", "inbox"));
  };
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "메일 연동"), el("span", { class: "grow" }), el("button", { class: "primary", id: "mail-add", onclick: () => mailDialog(null).catch(fail) }, icon("plus", 16), "메일 계정 추가")),
    items.length ? el("div", { class: "stack" }, items.map((a) => el("div", { class: "card src" },
      el("div", { class: "row" }, icon("mail", 20), el("b", {}, a.name), pill(REG[a.register] || a.register), a.running ? pill("수집 중", "info") : "", el("span", { class: "grow" }),
        el("button", { onclick: () => spec(a.name) && mailDialog(spec(a.name)).catch(fail) }, icon("settings", 16), "설정"), el("button", { onclick: run(a, true) }, icon("eye", 16), "미리보기"), el("button", { class: "primary", onclick: run(a, false) }, icon("history", 16), "지금 수집")),
      el("div", { class: "hint ref" }, `${a.host} · ${a.mailbox}`),
      el("div", { class: "statrow" }, el("div", { class: "stat" }, el("span", { class: "hint" }, "등록된 메일"), el("b", {}, fmtNum(a.counts?.registered || 0))),
        el("div", { class: "stat" }, el("span", { class: "hint" }, "자동 수집"), el("b", {}, a.schedule_minutes ? `${a.schedule_minutes}분마다` : "수동")),
        el("div", { class: "stat" }, el("span", { class: "hint" }, "마지막 실행"), el("b", {}, a.last ? fmtDate(a.last.ts) : "-"), a.last ? pill(a.last.ok ? "성공" : "확인 필요", a.last.ok ? "ok" : "bad") : "")),
      el("div", { class: "row" }, el("button", { class: "sm", onclick: () => show(a) }, "메일별 결과 보기")),
      a.last?.stats?.error ? el("p", { class: "err" }, a.last.stats.error) : ""))) : el("div", { class: "card" }, empty("연결된 메일 계정이 없습니다. 위의 '메일 계정 추가'로 IMAP 계정을 연결하면 보낸 사람 이름으로 문서가 등록됩니다.", "inbox")),
    el("p", { class: "hint" }, "보낸 사람의 이메일이 사용자와 일치해야 등록됩니다. 규칙(제목 #태그·보낸 사람)으로 폴더·유형·보안등급을 자동 지정하며, 같은 메일은 Message-ID 로 중복 등록되지 않습니다."));
});

export async function mailDialog(existing) {
  const e = existing || {};
  const inp = (id, val = "", ph = "") => el("input", { id, value: val ?? "", placeholder: ph, "aria-label": id });
  const name = inp("cm-name", e.name, "예: 문서수신함"), host = inp("cm-host", e.host, "mail.example.com"), port = inp("cm-port", e.port || "", "993 (SSL) / 143"), user = inp("cm-user", e.user, "edm@iccjeju.example");
  const pw = el("input", { id: "cm-pw", type: "password", autocomplete: "new-password", placeholder: e.has_password ? "(저장됨 — 비워 두면 유지)" : "" });
  const ssl = el("input", { type: "checkbox", id: "cm-ssl", checked: e.ssl !== false });
  const box = inp("cm-box", e.mailbox || "INBOX"), folder = inp("cm-folder", e.folder, "메일수신 (문서함 폴더 이름)"), allowed = inp("cm-allowed", (e.allowed_senders || []).join(", "), "*@iccjeju.example (비우면 모두)"), sched = inp("cm-sched", e.schedule_minutes || "", "10 (비우면 수동)"), moveTo = inp("cm-move", e.move_to, "Processed (처리 후 옮길 메일함, 선택)");
  const reg = el("select", { id: "cm-reg" }, [["both", "본문 + 첨부"], ["message", "본문(.eml)만"], ["attachments", "첨부만"]].map(([v, l]) => el("option", { value: v, selected: v === (e.register || "both") }, l)));
  const build = () => ({ name: name.value.trim(), host: host.value.trim(), port: port.value || undefined, ssl: ssl.checked, user: user.value.trim(), password: pw.value, mailbox: box.value || "INBOX", register: reg.value,
    folder: folder.value.trim() || undefined, allowed_senders: allowed.value, schedule_minutes: sched.value || undefined, move_to: moveTo.value.trim() || undefined, unknown_sender: "skip" });
  const result = el("div", { class: "sec", id: "cm-result", "aria-live": "polite" });
  await dialog(existing ? `메일 계정 편집 · ${e.name}` : "메일 계정 추가", () => el("div", { class: "stack" },
    el("p", { class: "hint" }, "IMAP 수집함입니다. 직원이 이 주소로 메일을 전달(참조)하면 보낸 사람 이름으로 문서가 등록됩니다. Gmail·네이버 등은 '앱 비밀번호'와 IMAP 사용 설정이 필요합니다."),
    field("이름", name), el("div", { class: "grid2" }, field("메일 서버(IMAP)", host), field("포트", port)), el("label", { class: "inline" }, ssl, "SSL 사용 (대부분 필요)"),
    el("div", { class: "grid2" }, field("계정", user), field("비밀번호", pw)), el("div", { class: "grid2" }, field("메일함", box), field("등록 방식", reg)),
    field("등록할 문서함 폴더", folder), field("허용 발신자 패턴", allowed), el("div", { class: "grid2" }, field("자동 수집(분)", sched), field("처리 후 이동", moveTo)), result),
    [{ label: "취소", value: false },
      ...(existing && e.origin === "ui" ? [{ label: "삭제", danger: true, run: async () => { if (!(await confirmDialog(`'${e.name}' 메일 계정을 삭제할까요?`, "삭제", true))) return false; await api(`/api/dms/admin/connections/mail/${encodeURIComponent(e.name)}`, { method: "DELETE" }); toast("삭제했습니다"); go("#/admin/mail"); } }] : []),
      { label: "연결 테스트", run: async () => { result.replaceChildren(el("span", { class: "hint" }, "확인 중…")); const r = await api("/api/dms/admin/connections/mail/test", { method: "POST", body: build() }); result.replaceChildren(pill(r.ok ? "성공" : "실패", r.ok ? "ok" : "bad"), " ", r.message); return false; } },
      { label: "저장", primary: true, run: async () => { await api("/api/dms/admin/connections/mail", { method: "PUT", body: build() }); toast("저장했습니다"); go("#/admin/mail"); } }]);
}
