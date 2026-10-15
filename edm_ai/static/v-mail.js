import { api, dialog, el, empty, fail, fmtDate, fmtNum, go, pill, route, table } from "./core.js";
import { icon } from "./icons.js";

const LBL = { registered: "메일 등록", attachments: "첨부 등록", duplicate: "중복", unknown_sender: "사용자 미매칭", not_allowed: "허용되지 않은 발신자", blocked: "차단", error: "오류", seen: "확인한 메일" };
const ST = { registered: ["등록됨", "ok"], duplicate: ["중복", "warn"], blocked: ["차단", "bad"], error: ["오류", "bad"], not_allowed: ["허용되지 않은 발신자", "warn"], unknown_sender: ["사용자 미매칭", "warn"], too_large: ["용량 초과", "warn"] };
const REG = { message: "본문만", attachments: "첨부만", both: "본문 + 첨부" };

route(/^\/admin\/mail$/, async (view) => {
  const { items } = await api("/api/dms/admin/mail");
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
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "메일 연동"), el("span", { class: "hint" }, "IMAP 수집함 — 설정은 config.json 의 mail.imap (docs/INTEGRATION.md)")),
    items.length ? el("div", { class: "stack" }, items.map((a) => el("div", { class: "card src" },
      el("div", { class: "row" }, icon("mail", 20), el("b", {}, a.name), pill(REG[a.register] || a.register), a.running ? pill("수집 중", "info") : "", el("span", { class: "grow" }),
        el("button", { onclick: run(a, true) }, icon("eye", 16), "미리보기"), el("button", { class: "primary", onclick: run(a, false) }, icon("history", 16), "지금 수집")),
      el("div", { class: "hint ref" }, `${a.host} · ${a.mailbox}`),
      el("div", { class: "statrow" }, el("div", { class: "stat" }, el("span", { class: "hint" }, "등록된 메일"), el("b", {}, fmtNum(a.counts?.registered || 0))),
        el("div", { class: "stat" }, el("span", { class: "hint" }, "자동 수집"), el("b", {}, a.schedule_minutes ? `${a.schedule_minutes}분마다` : "수동")),
        el("div", { class: "stat" }, el("span", { class: "hint" }, "마지막 실행"), el("b", {}, a.last ? fmtDate(a.last.ts) : "-"), a.last ? pill(a.last.ok ? "성공" : "확인 필요", a.last.ok ? "ok" : "bad") : "")),
      el("div", { class: "row" }, el("button", { class: "sm", onclick: () => show(a) }, "메일별 결과 보기")),
      a.last?.stats?.error ? el("p", { class: "err" }, a.last.stats.error) : ""))) : el("div", { class: "card" }, empty("설정된 메일 계정이 없습니다. config.json 의 mail.imap 에 IMAP 계정을 추가하면 보낸 사람 이름으로 문서가 등록됩니다.", "inbox")),
    el("p", { class: "hint" }, "보낸 사람의 이메일이 사용자와 일치해야 등록됩니다. 규칙(제목 #태그·보낸 사람)으로 폴더·유형·보안등급을 자동 지정하며, 같은 메일은 Message-ID 로 중복 등록되지 않습니다."));
});
