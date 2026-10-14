import { api, deptName, el, empty, fmtHours, fmtNum, fmtSize, go, menu, pill, route, saveBlob, STATUS } from "./core.js";
import { barChart, donut, lineChart } from "./charts.js";
import { icon } from "./icons.js";

const kpi = (n, label, ic, hint = "") => el("div", { class: "kpi" }, icon(ic, 26), el("b", {}, n == null ? "-" : typeof n === "number" ? fmtNum(n) : n), el("span", {}, label), hint ? el("small", { class: "hint kpi-h" }, hint) : "");
const card = (title, ic, ...kids) => el("div", { class: "card" }, el("h3", {}, icon(ic, 15), title), ...kids);
const hbars = (items, label, val) => { const max = Math.max(1, ...items.map(val)); const box = el("div", { class: "bars" }, items.length ? items.map((i) => el("div", { class: "bar" }, el("span", {}, label(i)), el("div", { class: "tr" }, el("i", { "data-w": Math.round((val(i) / max) * 100) })), el("span", {}, fmtNum(val(i))))) : empty("데이터 없음", "chart")); return box; };
const legend = (items) => el("div", { class: "legend" }, items.map((it, i) => el("span", {}, el("i", { class: "dot s" + (i % 4) }), `${it.label} ${fmtNum(it.value)}`)));

route(/^\/admin\/reports$/, async (view, _m, params) => {
  const days = Number(params.days) || 90;
  const r = await api("/api/dms/admin/report?days=" + days);
  const k = r.kpis, a = r.approvals, ai = r.ai_usage, t = a.turnaround_hours;
  const dl = (section) => () => saveBlob(`/api/dms/admin/report.csv?section=${section}&days=${days}`, {}, `report-${section}.csv`);
  const period = el("select", { "aria-label": "기간", onchange: (e) => go("#/admin/reports?days=" + e.target.value) }, [[30, "최근 30일"], [90, "최근 90일"], [180, "최근 180일"], [365, "최근 1년"]].map(([v, l]) => el("option", { value: v, selected: v === days }, l)));
  const docRow = (x) => el("div", { class: "top-row" }, el("span", { class: "ref" }, x.doc_no), x.title ? el("button", { class: "link", onclick: () => go("#/doc/" + x.doc_id) }, x.title) : el("span", { class: "hint" }, "(열람 권한 없음)"), el("b", {}, fmtNum(x.count)));
  const stat = (label, value) => el("div", { class: "stat" }, el("span", { class: "hint" }, label), el("b", {}, value));
  view.replaceChildren(
    el("div", { class: "page-h" }, el("h1", {}, "리포트"), el("span", { class: "hint" }, `기준 ${r.generated.slice(0, 16).replace("T", " ")} UTC`), el("span", { class: "grow" }), period,
      menu("내보내기", [{ label: "월별 등록 (CSV)", icon: "download", run: dl("registrations") }, { label: "부서별 문서 (CSV)", icon: "download", run: dl("departments") }, { label: "유형별 문서 (CSV)", icon: "download", run: dl("types") },
        { label: "AI 질문 일별 (CSV)", icon: "download", run: dl("ai_daily") }, { label: "부서별 저장 용량 (CSV)", icon: "download", run: dl("storage") }, { label: "보존 종료 예정 (CSV)", icon: "download", run: dl("retention") }, "-",
        { label: "인쇄 / PDF 저장", icon: "file", run: () => window.print() }])),
    el("div", { class: "kpis" }, kpi(k.documents, "관리 문서", "folder", `외부 소스 ${fmtNum(k.external_documents)}`), kpi(k.registered_in_period, `기간 내 신규 등록`, "plus"), kpi(k.approvals_in_period, "기간 내 결재 요청", "task", `처리 중 ${k.pending_approvals}`),
      kpi(k.ai_questions, "AI 질문", "sparkles", k.ai_answer_rate == null ? "" : `근거 확보율 ${k.ai_answer_rate}%`), kpi(t ? fmtHours(t.median) : "-", "결재 중앙 소요", "clock", t ? `평균 ${fmtHours(t.avg)} · 90% ${fmtHours(t.p90)}` : "데이터 없음"), kpi(k.legal_holds, "법적 보류", "lock")),
    el("div", { class: "grid2 sec rep" },
      card("월별 문서 등록", "chart", barChart(r.registrations_by_month.map((x) => ({ label: x.month.slice(5) + "월", value: x.count })))),
      card("AI 질문 일별 추이 (최근 30일)", "sparkles", lineChart(r.ai_usage.daily.map((x) => ({ label: x.day.slice(5), value: x.count }))),
        el("div", { class: "statrow" }, stat("이용자", fmtNum(ai.users)), stat("근거 없음(지식 공백)", fmtNum(ai.no_evidence)), stat("LLM 답변", fmtNum(ai.llm_answers)))),
      card("부서별 문서", "users", hbars(r.documents_by_dept, (i) => deptName(i.dept), (i) => i.count)),
      card("유형별 문서", "layers", hbars(r.documents_by_type, (i) => i.name, (i) => i.count)),
      card("보안등급 분포", "shield", el("div", { class: "donut-row" }, donut(r.documents_by_level.map((x) => ({ label: x.level, value: x.count }))), legend(r.documents_by_level.map((x) => ({ label: x.level, value: x.count }))))),
      card("상태별 문서", "task", el("div", { class: "donut-row" }, donut(r.documents_by_status.map((x) => ({ label: STATUS[x.status]?.[0] || x.status, value: x.count }))), legend(r.documents_by_status.map((x) => ({ label: STATUS[x.status]?.[0] || x.status, value: x.count }))))),
      card("결재 현황", "task", el("div", { class: "statrow" }, stat("완료 건수", t ? fmtNum(t.n) : "0"), stat("중앙값", fmtHours(t?.median)), stat("90% 이내", fmtHours(t?.p90))),
        el("h3", { class: "sec" }, "처리 대기 기간"), hbars(a.pending_age, (i) => i.bucket, (i) => i.count), el("h3", { class: "sec" }, "결재자별 대기"),
        a.pending_by_approver.length ? hbars(a.pending_by_approver, (i) => i.name, (i) => i.count) : empty("대기 중인 결재가 없습니다", "task")),
      card("보존 · 만료", "calendar", el("div", { class: "statrow" }, stat("90일 내 보존 종료", fmtNum(r.retention.ending_90d)), stat("30일 내 만료", fmtNum(r.retention.expiring_30d)), stat("휴지통", fmtNum(r.retention.trash))),
        r.retention.ending_by_month.length ? barChart(r.retention.ending_by_month.map((x) => ({ label: x.month.slice(5) + "월", value: x.count })), { h: 160 }) : empty("90일 내 보존 종료 문서가 없습니다", "calendar")),
      card("권한 신청", "key", el("div", { class: "statrow" }, stat("신청", fmtNum(r.access_requests.total)), stat("처리 중", fmtNum(r.access_requests.pending)), stat("승인/거절", `${r.access_requests.approved}/${r.access_requests.denied}`),
        stat("평균 처리", fmtHours(r.access_requests.avg_decision_hours)))),
      card("외부 공유", "share", el("div", { class: "statrow" }, stat("유효 링크", fmtNum(r.sharing.active)), stat("7일 내 만료", fmtNum(r.sharing.expiring_7d)), stat("열람", fmtNum(r.sharing.views)), stat("다운로드", fmtNum(r.sharing.downloads)))),
      card("부서별 저장 용량", "inbox", hbars(r.storage_by_dept, (i) => i.dept, (i) => i.bytes).childNodes.length ? el("div", { class: "bars" }, r.storage_by_dept.map((x) => el("div", { class: "top-row" }, el("span", {}, deptName(x.dept)), el("b", {}, fmtSize(x.bytes))))) : empty("데이터 없음", "inbox")),
      card("활동이 많은 문서 TOP", "history", r.active_documents.length ? el("div", { class: "stack" }, r.active_documents.map(docRow)) : empty("기간 내 활동이 없습니다", "history")),
      card("다운로드 TOP", "download", r.most_downloaded.length ? el("div", { class: "stack" }, r.most_downloaded.map(docRow)) : empty("기간 내 다운로드가 없습니다", "download"))));
  for (const i of view.querySelectorAll("i[data-w]")) i.style.width = i.dataset.w + "%";
});
