import { api, dialog, el, empty, fail, field, fileToB64, fmtDate, fmtSize, go, pill, route, state, table, toast } from "./core.js";
import { icon } from "./icons.js";

const LIC = { licensed: ["정식 라이선스", "ok"], evaluation: ["평가판", "warn"], expired: ["만료", "bad"], invalid: ["검증 실패", "bad"] };

route(/^\/admin\/ops$/, async (view) => {
  const [lic, bk] = await Promise.all([api("/api/license"), api("/api/dms/admin/backups")]);
  const run = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try { const r = await api("/api/dms/admin/backups/run", { method: "POST", body: {} }); toast(r.ok ? `백업 완료 (${fmtSize(r.bytes)})` : "백업 검증에 문제가 있습니다", r.ok ? "ok" : "bad"); go("#/admin/ops"); } catch (x) { fail(x); } finally { btn.disabled = false; }
  };
  const install = async () => {
    const ta = el("textarea", { id: "lic-text", rows: "8", placeholder: '{"payload": {...}, "signature": "..."}' });
    await dialog("라이선스 등록", () => el("div", {}, el("p", { class: "hint" }, "공급사가 발급한 license.json 의 내용을 붙여 넣으세요. 서명이 맞아야 등록됩니다."), field("라이선스 파일 내용", ta)),
      [{ label: "취소", value: false }, { label: "등록", primary: true, run: async () => { await api("/api/dms/admin/license", { method: "PUT", body: { text: ta.value } }); toast("등록했습니다"); go("#/admin/ops"); } }]);
  };
  const [ll, lt] = LIC[lic.mode] || [lic.mode, ""];
  const brandCard = (kind, title, ic, hint, has, url, idp) => {
    const file = el("input", { type: "file", id: idp + "-file", accept: ".png,.jpg,.jpeg,.gif,.webp,.svg", "aria-label": title });
    return el("div", { class: "card sec" }, el("div", { class: "row" }, icon(ic, 20), el("b", {}, title), el("span", { class: "grow" })),
      el("div", { class: "row sec" }, has ? el("img", { src: url, alt: "현재 " + title, class: "logo-preview" }) : el("span", { class: "hint" }, "기본값을 쓰는 중입니다"), el("span", { class: "grow" }), file,
        el("button", { class: "primary", id: idp + "-up", onclick: async () => {
          const f = file.files[0];
          if (!f) return toast("파일을 선택하세요", "bad");
          try { await api(`/api/dms/admin/branding/${kind}`, { method: "PUT", body: { filename: f.name, content_b64: await fileToB64(f) } }); toast("바꿨습니다"); location.reload(); } catch (x) { fail(x); }
        } }, "올리기"), has ? el("button", { onclick: async () => { await api(`/api/dms/admin/branding/${kind}`, { method: "DELETE" }); location.reload(); } }, "기본값으로") : ""),
      el("p", { class: "hint" }, hint));
  };
  const logoCard = brandCard("logo", "로고", "building", "ICC JEJU 홈페이지의 로고 파일(PNG·SVG, 가로형 권장, 1MB 이하, 배경 투명)을 올리면 로그인 화면·왼쪽 메뉴에 적용됩니다. 로고·사진의 사용 권한은 기관에서 확인하세요.", state.cfg.logo, "/brand-logo?v=" + state.cfg.v, "logo");
  const heroCard = brandCard("hero", "로그인 대표 이미지 (컨벤션센터 사진)", "folder", "로그인 화면 왼쪽 배경에 쓰입니다. 가로 1600px 이상의 JPG·PNG(3MB 이하)를 권장합니다. 올리지 않으면 기본 일러스트가 표시됩니다.", state.cfg.hero, "/brand-hero?v=" + state.cfg.hv, "hero");
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "백업 · 라이선스 · 로고")), logoCard, heroCard,
    el("div", { class: "card" }, el("div", { class: "row" }, icon("key", 20), el("b", {}, "라이선스"), pill(ll, lt), el("span", { class: "grow" }), el("button", { id: "lic-install", onclick: () => install().catch(fail) }, "라이선스 등록")),
      lic.customer ? el("div", { class: "statrow" }, el("div", { class: "stat" }, el("span", { class: "hint" }, "고객"), el("b", {}, lic.customer)), el("div", { class: "stat" }, el("span", { class: "hint" }, "에디션 / 사용자"), el("b", {}, `${lic.edition || "-"} · ${lic.users}/${lic.max_users || "무제한"}`)),
        el("div", { class: "stat" }, el("span", { class: "hint" }, "만료일"), el("b", {}, lic.expires), el("span", { class: "hint" }, `${lic.days_left}일 남음`))) : "",
      lic.message ? el("p", { class: lic.mode === "licensed" ? "hint" : "err" }, lic.message) : "",
      el("p", { class: "hint" }, "라이선스는 공급사의 전자서명(Ed25519)으로 검증합니다. 만료·초과 시에도 서비스는 멈추지 않고 안내만 표시합니다.")),
    el("div", { class: "card sec" }, el("div", { class: "row" }, icon("history", 20), el("b", {}, "백업"), bk.enabled ? pill(bk.every_hours ? `${bk.every_hours}시간마다 · ${bk.keep}개 보관` : "수동", "info") : pill("설정 안 됨", "warn"), el("span", { class: "grow" }),
      bk.enabled ? el("button", { class: "primary", id: "bk-run", onclick: run }, "지금 백업") : ""),
      !bk.enabled ? el("p", { class: "hint" }, '설정 backup: {"dir": "backups", "every_hours": 24, "keep": 7} 를 추가하면 정기 백업이 켜집니다.') : "",
      bk.runs.length ? table(["일시", "결과", "크기", "파일 수", "실행", "비고"], bk.runs.map((r) => [fmtDate(r.ts), pill(r.ok ? "성공" : "실패", r.ok ? "ok" : "bad"), fmtSize(r.bytes), r.files, r.by, r.note || ""])) : bk.enabled ? empty("백업 기록이 없습니다", "history") : "",
      el("p", { class: "hint" }, bk.note)),
    el("div", { class: "card sec" }, el("div", { class: "row" }, icon("chart", 20), el("b", {}, "모니터링 (/metrics)")),
      el("p", { class: "hint" }, "환경변수 EDM_AI_METRICS_TOKEN 을 설정하면 Prometheus 형식 지표(문서 수·저장 용량·대기 결재·AI 누출 점검·백업 경과 시간·라이선스 잔여일)를 Authorization: Bearer <토큰> 으로 가져갈 수 있습니다. 문서 제목·사용자 정보는 포함되지 않습니다.")));
});
