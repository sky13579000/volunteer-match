// Browser e2e for the document-management UI.
// Needs a running demo server on a FRESH, seeded workspace:   python3 examples/demo/build_demo.py && python3 -m edm_ai demo --no-browser
// Run:  EDM_URL=http://127.0.0.1:8080/ PLAYWRIGHT_PATH=<playwright module> [CHROMIUM_PATH=...] node tests/e2e/ui.e2e.js
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const B = process.env.EDM_URL || 'http://127.0.0.1:8080/';
const FILES = process.env.E2E_FILES || __dirname + '/files';
const SHOTS = process.env.SHOTS || '.';
let fails = 0, passes = 0;
const ok = (name, cond, extra = '') => { cond ? passes++ : fails++; console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : '  ' + extra)); };
const errs = [];
// Browsers log every non-2xx fetch (404 for a hidden document, 409 for a legal hold ...) as an error. Those are expected;
// anything else (JS exceptions, CSP violations, failed module loads) must still fail the run.
const EXPECTED_HTTP = /Failed to load resource: the server responded with a status of 4\d\d/;

(async () => {
  const b = await chromium.launch({ ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  async function login(user, opts = {}) {
    const ctx = await b.newContext({ viewport: opts.viewport || { width: 1360, height: 900 }, acceptDownloads: true, colorScheme: opts.dark ? 'dark' : 'light' });
    const p = await ctx.newPage();
    p.on('pageerror', e => errs.push(user + ' pageerror: ' + e.message));
    p.on('console', m => m.type() === 'error' && !EXPECTED_HTTP.test(m.text()) && errs.push(user + ' console: ' + m.text().slice(0, 200)));
    await p.goto(B); await p.selectOption('#demo-user', user); await p.click('#login-go');
    await p.waitForSelector('#shell:not([hidden])'); await p.waitForSelector('#view h1, #view .kpis');
    // setting an identical hash fires no hashchange, so force a render the way a user pressing F5 would
    p.hash = async (h) => { await p.evaluate(x => { if (location.hash === x) window.dispatchEvent(new HashChangeEvent('hashchange')); else location.hash = x; }, h); await p.waitForTimeout(450); };
    p.dlg = () => p.locator('dialog[open]');
    return p;
  }
  const toastText = async (p) => (await p.locator('#toasts .toast').allTextContents()).join(' | ');

  // ===== kim: dashboard, register, approval request =====
  const kim = await login('kim');
  ok('dashboard shows KPI cards', (await kim.$$('.kpi')).length >= 6);
  ok('dashboard lists my approval requests', (await kim.textContent('#view')).includes('한빛전자 MES 2단계 제안서'));
  ok('sidebar has no admin group for sales user', !(await kim.textContent('#nav')).includes('감사 로그'));
  await kim.hash('#/docs');
  await kim.click('#btn-new'); await kim.waitForSelector('dialog[open] #f-file');
  await kim.setInputFiles('#f-file', FILES + '/contract.md');
  await kim.fill('#f-title', '<img src=x onerror=window.__xss=1> 계약 초안');
  await kim.selectOption('#f-type', 'CT'); await kim.fill('#f-tags', '계약, e2e');
  await kim.dlg().getByRole('button', { name: '등록', exact: true }).click();
  await kim.waitForSelector('.tabs');
  const docUrl = kim.url();
  ok('registered doc opens, number assigned', /CT-\d{4}-\d{4}/.test(await kim.textContent('#view')));
  ok('XSS payload not executed', !(await kim.evaluate(() => !!window.__xss)) && (await kim.$$('#view img')).length === 0);
  ok('contract type starts as draft', (await kim.textContent('.page-h')).includes('작성 중'));
  ok('registered doc is linked to the business DB', (await kim.textContent('#view')).includes('연결된 업무 DB 행'));
  await kim.getByRole('button', { name: '결재 요청' }).click(); await kim.waitForSelector('#ap-park');
  await kim.check('#ap-park'); await kim.dlg().getByRole('button', { name: '요청', exact: true }).click();
  await kim.waitForSelector('.step');
  ok('approval request shows pending step for park', (await kim.textContent('.steps')).includes('박재무') && (await kim.textContent('.steps')).includes('처리 대기'));
  ok('requester sees no approve button', (await kim.$$('button:has-text("승인")')).length === 0);

  // ===== park: task list, approve =====
  const park = await login('park');
  await park.waitForSelector('#bell-n:not([hidden])');
  await park.hash('#/tasks'); await park.waitForSelector('.tbl');
  ok('park sees the request in 결재함', (await park.textContent('#view')).includes('계약 초안'));
  await park.locator('tr', { hasText: '계약 초안' }).getByRole('button', { name: '검토하기' }).click();
  await park.waitForSelector('#ap-comment');
  await park.fill('#ap-comment', '확인했습니다');
  await park.getByRole('button', { name: '승인', exact: true }).click();
  await park.waitForSelector('#view .tbl, #view .hint');
  await park.waitForTimeout(500);
  ok('approval toast', (await toastText(park)).includes('승인했습니다'));
  await kim.hash('#/notifications');
  ok('kim is notified of approval', (await kim.textContent('#view')).includes('승인:'));
  await kim.goto(docUrl); await kim.waitForSelector('.tabs');
  ok('doc is now approved', (await kim.textContent('.page-h')).includes('승인'));

  // ===== versions + diff =====
  await kim.getByRole('button', { name: '새 버전 올리기' }).click(); await kim.waitForSelector('#nv-file');
  await kim.setInputFiles('#nv-file', FILES + '/contract_v2.md'); await kim.fill('#nv-comment', '제3조 신설');
  await kim.dlg().getByRole('button', { name: '올리기' }).click(); await kim.waitForTimeout(700);
  await kim.click('.tabs button:has-text("버전")'); await kim.waitForSelector('.tbl');
  ok('versions tab lists v2 and v1', (await kim.textContent('.tbl')).includes('v2') && (await kim.textContent('.tbl')).includes('v1'));
  ok('v1 stays the effective version until v2 is approved', (await kim.textContent('.tbl')).includes('★현행'));
  await kim.check('input[name=va][aria-label="v1 기준"]'); await kim.check('input[name=vb][aria-label="v2 비교"]');
  await kim.getByRole('button', { name: 'A ↔ B 비교' }).click(); await kim.waitForSelector('.diff');
  ok('diff shows the added clause', (await kim.$$eval('.diff .add', n => n.some(x => x.textContent.includes('제3조')))));

  // ===== comments with mention =====
  await kim.click('.tabs button:has-text("댓글")'); await kim.waitForSelector('#cm-text');
  await kim.fill('#cm-text', '@park 제3조 확인 부탁드립니다'); await kim.getByRole('button', { name: '댓글 등록' }).click(); await kim.waitForTimeout(700);
  await kim.waitForFunction(() => document.querySelector('#view')?.textContent.includes('제3조 확인'), null, { timeout: 5000 }).catch(() => {});
  ok('comment is listed', (await kim.textContent('#view')).includes('제3조 확인'));

  // ===== ACL edit: share to outsider choi and take it back =====
  const choi = await login('choi');
  const find = async (q) => { await choi.hash('#/docs?q=' + encodeURIComponent(q)); await choi.waitForTimeout(400); return (await choi.$$('.tbl tbody tr')).length; };
  ok('outsider cannot find kim\'s doc', (await find('e2e유니크토큰')) === 0);
  await kim.click('.tabs button:has-text("권한")'); await kim.waitForSelector('#acl-inh');
  await kim.getByRole('button', { name: '+ 권한 추가' }).click();
  const row = kim.locator('.acl-row').last();
  await row.locator('select').first().selectOption('user'); await row.locator('input').fill('choi');
  await kim.getByRole('button', { name: '권한 저장' }).click(); await kim.waitForTimeout(700);
  ok('ACL saved toast', (await toastText(kim)).includes('권한을 저장'));
  ok('after ACL change outsider finds the doc', (await find('e2e유니크토큰')) === 1);
  await kim.click('.tabs button:has-text("권한")'); await kim.waitForSelector('.acl-row');
  await kim.locator('.acl-row').last().getByRole('button').click(); await kim.getByRole('button', { name: '권한 저장' }).click(); await kim.waitForTimeout(700);
  ok('after removing the entry outsider loses it again', (await find('e2e유니크토큰')) === 0);

  // ===== search / facets / highlight / saved search =====
  await kim.hash('#/docs?q=' + encodeURIComponent('영업 계획'));
  ok('search highlights matches with <mark>', (await kim.$$('.snip mark')).length > 0);
  await kim.hash('#/docs'); await kim.waitForSelector('.chip');
  await kim.locator('.chip', { hasText: '계약서' }).first().click(); await kim.waitForTimeout(500);
  ok('facet chip filters by type', (await kim.$$eval('.tbl tbody tr', r => r.length)) >= 1 && !(await kim.textContent('.tbl')).includes('예산안'));
  await kim.getByRole('button', { name: '검색 저장' }).click(); await kim.waitForSelector('dialog[open] input');
  await kim.dlg().locator('input').fill('내 계약서'); await kim.dlg().getByRole('button', { name: '저장' }).click(); await kim.waitForTimeout(700);
  ok('saved search appears', (await kim.$$eval('select[aria-label="저장된 검색"] option', o => o.map(x => x.textContent))).includes('내 계약서'));

  // ===== bulk tag + ZIP import + trash/restore =====
  await kim.hash('#/docs?mine=1'); await kim.waitForSelector('.tbl tbody tr');
  await kim.locator('.tbl tbody tr input[type=checkbox]').nth(0).check(); await kim.locator('.tbl tbody tr input[type=checkbox]').nth(1).check();
  ok('bulk bar appears with count', (await kim.textContent('.bulkbar')).includes('2건 선택'));
  await kim.locator('.bulkbar').getByRole('button', { name: '태그 추가' }).click(); await kim.dlg().locator('input').fill('일괄태그');
  await kim.dlg().getByRole('button', { name: '추가' }).click(); await kim.waitForTimeout(800);
  ok('bulk tag applied', (await toastText(kim)).includes('2건 처리'));
  await kim.hash('#/docs'); await kim.getByRole('button', { name: 'ZIP 일괄 등록' }).click(); await kim.waitForSelector('dialog[open] input[type=file]');
  await kim.dlg().locator('input[type=file]').setInputFiles(FILES + '/bundle.zip');
  await kim.dlg().getByRole('button', { name: '등록 시작' }).click(); await kim.waitForSelector('dialog[open] .pill.ok');
  ok('zip import: created 2, skipped the .exe', (await kim.dlg().textContent()).includes('등록 2건') && (await kim.dlg().textContent()).includes('건너뜀 1건') && (await kim.dlg().textContent()).includes('bad.exe'));
  await kim.dlg().getByRole('button', { name: '닫기' }).click();
  await kim.hash('#/docs?q=e2ezipprobe'); await kim.waitForSelector('.tbl tbody tr');
  const zipRows = await kim.$$eval('.tbl tbody tr', r => r.length);
  ok('zip documents are searchable', zipRows === 2);
  await kim.locator('.tbl tbody tr').first().click(); await kim.waitForSelector('.tabs');
  await kim.getByRole('button', { name: '삭제' }).click(); await kim.dlg().getByRole('button', { name: '삭제' }).click(); await kim.waitForSelector('.kpis, .tbl');
  await kim.hash('#/trash'); await kim.waitForSelector('.tbl');
  ok('deleted doc is in the trash', (await kim.$$eval('.tbl tbody tr', r => r.length)) >= 1);
  await kim.getByRole('button', { name: '복원' }).first().click(); await kim.waitForTimeout(700);
  ok('restore works', (await toastText(kim)).includes('복원했습니다'));

  // ===== external share link =====
  await kim.hash('#/docs?q=' + encodeURIComponent('2026 영업 계획')); await kim.waitForSelector('.tbl tbody tr');
  await kim.locator('.tbl tbody tr', { hasText: 'GEN-' }).first().click(); await kim.waitForSelector('.tabs');
  await kim.click('.tabs button:has-text("외부 공유")'); await kim.waitForSelector('#sh-create');
  await kim.fill('#sh-pw', 'e2e-secret-pw'); await kim.selectOption('#sh-perm', 'download'); await kim.click('#sh-create');
  await kim.waitForSelector('dialog[open] #sh-url');
  const shareUrl = await kim.inputValue('#sh-url');
  ok('share link has an unguessable token', /\/s\/[A-Za-z0-9_-]{40,}$/.test(shareUrl));
  await kim.dlg().getByRole('button', { name: '닫기' }).click();
  const anon = await (await b.newContext({ acceptDownloads: true })).newPage();
  anon.on('pageerror', e => errs.push('anon pageerror: ' + e.message)); anon.on('console', m => m.type() === 'error' && !EXPECTED_HTTP.test(m.text()) && errs.push('anon console: ' + m.text().slice(0, 200)));
  await anon.goto(shareUrl); await anon.waitForSelector('#pw:not([hidden])');
  ok('anonymous visitor is asked for the password and sees nothing else', await anon.$eval('#doc', n => n.hidden));
  await anon.fill('#pw-input', 'wrong-pass'); await anon.click('#pw-form button'); await anon.waitForTimeout(500);
  ok('wrong password is rejected', (await anon.textContent('#err')).includes('비밀번호'));
  await anon.fill('#pw-input', 'e2e-secret-pw'); await anon.click('#pw-form button'); await anon.waitForSelector('#doc:not([hidden])');
  ok('correct password shows the document', (await anon.textContent('#title')).includes('영업 계획'));
  // Chromium's suggestedFilename() ignores non-ASCII names in headless mode, so assert on what the app controls: the anchor's download attribute
  await anon.evaluate(() => { const o = HTMLAnchorElement.prototype.click; HTMLAnchorElement.prototype.click = function () { window.__dl = this.getAttribute('download'); return o.call(this); }; });
  await Promise.all([anon.waitForEvent('download'), anon.click('#dl')]);
  ok('anonymous download works with the real file name', (await anon.evaluate(() => window.__dl)) === '영업계획.md');
  await kim.click('.tabs button:has-text("외부 공유")'); await kim.waitForSelector('.tbl');
  await kim.getByRole('button', { name: '폐지' }).first().click(); await kim.waitForTimeout(700);
  await anon.reload(); await anon.waitForSelector('#err:not(:empty)');
  ok('revoked link says it is invalid', (await anon.textContent('#err')).includes('올바르지 않거나'));

  // ===== AI chat links back to the document =====
  await kim.hash('#/chat'); await kim.fill('#q', '2026 영업 계획 목표 계약 건수'); await kim.click('#send'); await kim.waitForSelector('.a');
  await kim.click('.a summary'); await kim.waitForTimeout(300);
  ok('AI answer cites a managed document with an open-link', (await kim.$$('.ev .link')).length >= 1);

  // outsider asking the AI about the unpublished v2 clause gets nothing from kim's document
  await choi.hash('#/chat'); await choi.fill('#q', '제3조 신설 지체상금 e2e유니크토큰'); await choi.click('#send'); await choi.waitForSelector('.a');
  ok('AI never uses a document the asker cannot read', !(await choi.textContent('.a')).includes('계약 초안'));

  // ===== admin =====
  const adm = await login('admin');
  ok('admin sees the admin menu', (await adm.textContent('#nav')).includes('감사 로그'));
  await adm.hash('#/admin/audit'); await adm.waitForSelector('.tbl');
  ok('audit log chain verified', (await adm.textContent('.page-h')).includes('무결성 정상'));
  const [csv] = await Promise.all([adm.waitForEvent('download'), adm.getByRole('button', { name: 'CSV 내보내기' }).click()]);
  ok('audit CSV downloads', (await csv.suggestedFilename()) === 'audit.csv');
  await adm.fill('#au-action', 'registered'); await adm.getByRole('button', { name: '조회' }).click(); await adm.waitForTimeout(500);
  ok('audit filter works', (await adm.$$eval('.tbl tbody tr', r => r.length)) >= 1);
  await adm.hash('#/admin/types'); await adm.waitForSelector('.tbl');
  await adm.getByRole('button', { name: '+ 새 유형' }).click(); await adm.waitForSelector('#dt-code');
  await adm.fill('#dt-code', 'PO'); await adm.fill('#dt-name', '발주서'); await adm.fill('#dt-pat', 'PO-{year}-{seq:05}'); await adm.check('#dt-appr');
  await adm.dlg().getByRole('button', { name: '저장' }).click();
  await adm.waitForFunction(() => document.querySelector('#view .tbl')?.textContent.includes('PO-{year}-{seq:05}'), null, { timeout: 8000 }).catch(() => {});
  ok('new document type is listed', (await adm.textContent('#view .tbl')).includes('PO-{year}-{seq:05}'));
  await adm.hash('#/admin/stats'); await adm.click('#run-maint'); await adm.waitForTimeout(600);
  ok('maintenance runs', (await toastText(adm)).includes('만료'));
  await adm.hash('#/docs'); await adm.waitForSelector('.tbl tbody tr');
  await adm.locator('.tbl tbody tr', { hasText: '정보보안' }).first().click(); await adm.waitForSelector('.tabs');
  await adm.getByRole('button', { name: '법적 보류', exact: true }).click(); await adm.dlg().locator('input').fill('e2e'); await adm.dlg().getByRole('button', { name: '확인' }).click(); await adm.waitForTimeout(700);
  ok('legal hold shown', (await adm.textContent('.page-h')).includes('법적 보류'));
  await adm.getByRole('button', { name: '삭제' }).click(); await adm.dlg().getByRole('button', { name: '삭제' }).click(); await adm.waitForTimeout(700);
  ok('legal hold blocks deletion', (await toastText(adm)).includes('법적 보류'));

  // ===== outsider cannot reach hidden documents even by URL =====
  const lee = await login('lee');
  await lee.hash('#/docs?q=' + encodeURIComponent('채용 계획')); await lee.waitForSelector('.tbl tbody tr');
  await lee.locator('.tbl tbody tr').first().click(); await lee.waitForSelector('.tabs');
  const hrUrl = lee.url();
  await choi.goto(hrUrl); await choi.waitForTimeout(900);
  ok('hidden doc by direct URL looks like a missing page', (await choi.textContent('#view')).includes('찾을 수 없습니다'));
  ok('outsider sees no admin menu', !(await choi.textContent('#nav')).includes('관리'));

  // ===== responsive + dark =====
  const mob = await login('kim', { viewport: { width: 400, height: 800 } });
  for (const h of ['#/dashboard', '#/docs', '#/tasks']) {
    await mob.hash(h); await mob.waitForTimeout(400);
    ok('no horizontal page overflow on phone width ' + h, await mob.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  }
  const dark = await login('kim', { dark: true }); await dark.screenshot({ path: SHOTS + '/ui-dark.png' });
  ok('dark theme renders', (await dark.evaluate(() => getComputedStyle(document.body).backgroundColor)) !== 'rgb(244, 246, 248)');
  await kim.hash('#/dashboard'); await kim.waitForTimeout(500); await kim.screenshot({ path: SHOTS + '/ui-dash.png' });
  await kim.hash('#/docs'); await kim.waitForTimeout(500); await kim.screenshot({ path: SHOTS + '/ui-docs.png' });

  ok('no console / page errors (incl. CSP violations)', errs.length === 0, JSON.stringify(errs.slice(0, 4)));
  console.log(`\n${passes} passed, ${fails} failed`);
  await b.close();
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
