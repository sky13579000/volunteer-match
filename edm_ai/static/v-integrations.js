import { api, dialog, el, empty, fail, field, fmtDate, go, pill, route, state, table, toast } from "./core.js";
import { icon } from "./icons.js";

const ST = { active: ["사용 중", "ok"], revoked: ["폐기", "bad"], expired: ["만료", "warn"] };

route(/^\/admin\/integrations$/, async (view) => {
  const [keys, hooks] = await Promise.all([api("/api/dms/admin/api-keys"), api("/api/dms/admin/webhooks")]);
  const create = () => {
    const name = el("input", { id: "k-name", placeholder: "예: 그룹웨어 연동", "aria-label": "키 이름" });
    const user = el("select", { id: "k-user", "aria-label": "대리 사용자" }, state.meta.users.map((u) => el("option", { value: u.id }, `${u.name} (${u.id})`)));
    const scope = el("select", { id: "k-scope", "aria-label": "범위" }, Object.entries(keys.scopes).map(([v, l]) => el("option", { value: v }, l)));
    const days = el("input", { id: "k-days", type: "number", min: "1", max: "365", value: "90", "aria-label": "유효기간(일)" });
    return dialog("API 키 발급", () => el("div", {}, el("p", { class: "hint" }, "키는 선택한 사용자의 권한 그대로 동작합니다. 관리 기능에는 사용할 수 없고, 발급 즉시 한 번만 표시됩니다."),
      field("이름", name), field("대리 사용자", user, "연동 전용 계정을 만들어 최소 권한으로 쓰는 것을 권장합니다"), field("범위", scope), field("유효기간(일)", days)),
    [{ label: "취소", value: false }, { label: "발급", primary: true, run: async (close) => {
      const r = await api("/api/dms/admin/api-keys", { method: "POST", body: { name: name.value, user: user.value, scope: scope.value, days: Number(days.value) } });
      close(true);
      await dialog("키가 발급되었습니다", () => el("div", {}, el("p", { class: "err" }, r.note), el("pre", { class: "body key-once", id: "key-once" }, r.key),
        el("p", { class: "hint" }, "사용: HTTP 헤더 Authorization: Bearer <키>  ·  만료 " + fmtDate(r.expires))), [{ label: "복사", run: async () => { try { await navigator.clipboard.writeText(r.key); toast("복사했습니다"); } catch { toast("복사할 수 없습니다. 직접 선택해 복사하세요", "bad"); } return false; } }, { label: "닫기", primary: true }]);
      go("#/admin/integrations"); return false; } }]);
  };
  const revoke = async (k) => { const ok = await dialog("키 폐기", () => el("p", { class: "flush" }, `'${k.name}' 키를 폐기합니다. 이 키를 쓰는 연동은 즉시 중단됩니다.`), [{ label: "취소", value: false }, { label: "폐기", danger: true, value: true }]);
    if (ok) { try { await api("/api/dms/admin/api-keys/" + k.id, { method: "DELETE" }); toast("폐기했습니다"); go("#/admin/integrations"); } catch (e) { fail(e); } } };
  view.replaceChildren(el("div", { class: "page-h" }, el("h1", {}, "연동 관리"), el("span", { class: "hint" }, "API 키 · 이벤트 웹훅 · 그룹웨어 수신은 docs/INTEGRATION.md")),
    el("div", { class: "card" }, el("div", { class: "row" }, el("h3", { class: "flush" }, icon("key", 15), "API 키"), el("span", { class: "grow" }), el("button", { class: "primary", id: "k-new", onclick: create }, icon("plus", 16), "키 발급")),
      keys.items.length ? table(["이름", "대리 사용자", "범위", "접두어", "상태", "만료", "최근 사용", ""], keys.items.map((k) => [k.name, k.user_id, keys.scopes[k.scope], el("span", { class: "ref" }, k.prefix + "…"), pill(...(ST[k.status] || [k.status, ""])), fmtDate(k.expires), k.last_used ? fmtDate(k.last_used) : "-",
        k.status === "active" ? el("button", { class: "sm danger", onclick: () => revoke(k) }, "폐기") : ""])) : empty("발급된 키가 없습니다", "key")),
    el("div", { class: "card sec" }, el("div", { class: "row" }, el("h3", { class: "flush" }, icon("share", 15), "이벤트 웹훅 (송신)"), el("span", { class: "grow" }), el("span", { class: "hint" }, `성공 ${hooks.sent} · 실패 ${hooks.failed} · 대기 ${hooks.queued}`),
      hooks.hooks.length ? el("button", { onclick: async () => { try { await api("/api/dms/admin/webhooks/test", { method: "POST", body: {} }); toast("시험 이벤트(ping)를 보냈습니다"); setTimeout(() => go("#/admin/integrations"), 1500); } catch (e) { fail(e); } } }, "연결 시험") : ""),
      hooks.hooks.length ? table(["이름", "대상", "이벤트"], hooks.hooks.map((h) => [h.name, el("span", { class: "ref" }, h.url), h.events.join(", ")])) : empty("설정된 웹훅이 없습니다. config.json 의 events.webhooks 에 대상 URL 과 비밀값(환경변수)을 지정하세요.", "share"),
      hooks.recent.length ? el("div", { class: "sec" }, el("h3", {}, "최근 전송"), table(["일시", "대상", "이벤트", "결과", "시도", "비고"], hooks.recent.map((d) => [fmtDate(d.ts), d.hook, d.event, pill(d.status === "ok" ? "성공" : "실패", d.status === "ok" ? "ok" : "bad"), d.attempts, d.error || (d.http ? "HTTP " + d.http : "")]))) : ""));
});
