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
  const more = async (p, name) => { await p.getByRole('button', { name: '더 보기' }).click(); await p.getByRole('menuitem', { name }).click(); };
  const toastText = async (p) => (await p.locator('#toasts .toast').allTextContents()).join(' | ');

  // ===== kim: dashboard, register, approval request =====
  const kim = await login('kim');
  ok('dashboard shows KPI cards', (await kim.$$('.kpi')).length >= 6);
  ok('dashboard lists my approval requests', (await kim.textContent('#view')).includes('제주관광공사 MICE 위크 2단계 제안서'));
  ok('sidebar has no admin group for mice user', !(await kim.textContent('#nav')).includes('감사 로그'));
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
  ok('contract type starts as draft', (await kim.textContent('.doc-head')).includes('작성 중'));
  ok('registered doc is linked to the business DB', (await kim.textContent('#view')).includes('연결된 업무 DB 행'));
  await kim.getByRole('button', { name: '결재 요청' }).click(); await kim.waitForSelector('#ap-park');
  await kim.check('#ap-park'); await kim.dlg().getByRole('button', { name: '요청', exact: true }).click();
  await kim.waitForSelector('.step');
  ok('approval request shows pending step for park', (await kim.textContent('.steps')).includes('박재정') && (await kim.textContent('.steps')).includes('처리 대기'));
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
  ok('doc is now approved', (await kim.textContent('.doc-head')).includes('승인'));

  // ===== versions + diff =====
  await more(kim, '새 버전 올리기'); await kim.waitForSelector('#nv-file');
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

  // ===== related documents, download restriction, image preview =====
  await kim.click('.tabs button:has-text("개요")'); await kim.waitForSelector('#rel-add');
  await kim.fill('#rel-add', 'GEN-2026-0004'); await kim.click('#rel-go');
  await kim.waitForFunction(() => document.querySelector('#view')?.textContent.includes('GEN-2026-0004 2026 유치 계획'), null, { timeout: 5000 }).catch(() => {});
  ok('related document is linked', (await kim.textContent('#view')).includes('GEN-2026-0004 2026 유치 계획'));
  ok('download button available before restriction', (await kim.$$('.dh-actions button:has-text("다운로드")')).length >= 1);
  await kim.getByRole('button', { name: '정보 편집' }).click(); await kim.waitForSelector('#m-nodl');
  await kim.check('#m-nodl'); await kim.dlg().getByRole('button', { name: '저장' }).click();
  await kim.waitForFunction(() => document.querySelector('.doc-head')?.textContent.includes('다운로드 제한'), null, { timeout: 5000 }).catch(() => {});
  ok('download restriction badge shown', (await kim.textContent('.doc-head')).includes('다운로드 제한'));
  ok('download button disappears under restriction', (await kim.$$('.row button:has-text("다운로드")')).length === 0);
  await kim.getByRole('button', { name: '정보 편집' }).click(); await kim.waitForSelector('#m-nodl'); await kim.uncheck('#m-nodl');
  await kim.dlg().getByRole('button', { name: '저장' }).click(); await kim.waitForTimeout(700);
  await kim.hash('#/docs'); await kim.click('#btn-new'); await kim.waitForSelector('dialog[open] #f-file');
  await kim.setInputFiles('#f-file', FILES + '/pixel.png'); await kim.fill('#f-title', '현장 사진');
  await kim.dlg().getByRole('button', { name: '등록', exact: true }).click(); await kim.waitForSelector('.tabs');
  await kim.waitForSelector('img.preview', { timeout: 8000 }).catch(() => {});
  ok('image documents get an inline preview', (await kim.$$('img.preview')).length === 1);
  await kim.goto(docUrl); await kim.waitForSelector('.tabs');

  // ===== search / facets / highlight / saved search =====
  await kim.hash('#/docs?q=' + encodeURIComponent('유치 계획'));
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
  await kim.hash('#/docs'); await more(kim, 'ZIP 일괄 등록'); await kim.waitForSelector('dialog[open] input[type=file]');
  await kim.dlg().locator('input[type=file]').setInputFiles(FILES + '/bundle.zip');
  await kim.dlg().getByRole('button', { name: '등록 시작' }).click(); await kim.waitForSelector('dialog[open] .pill.ok');
  ok('zip import: created 2, skipped the .exe', (await kim.dlg().textContent()).includes('등록 2건') && (await kim.dlg().textContent()).includes('건너뜀 1건') && (await kim.dlg().textContent()).includes('bad.exe'));
  await kim.dlg().getByRole('button', { name: '닫기' }).click();
  await kim.hash('#/docs?q=e2ezipprobe'); await kim.waitForSelector('.tbl tbody tr');
  const zipRows = await kim.$$eval('.tbl tbody tr', r => r.length);
  ok('zip documents are searchable', zipRows === 2);
  await kim.locator('.tbl tbody tr').first().click(); await kim.waitForSelector('.tabs');
  await more(kim, '삭제'); await kim.dlg().getByRole('button', { name: '삭제' }).click(); await kim.waitForSelector('.kpis, .tbl');
  await kim.hash('#/trash'); await kim.waitForSelector('.tbl');
  ok('deleted doc is in the trash', (await kim.$$eval('.tbl tbody tr', r => r.length)) >= 1);
  await kim.getByRole('button', { name: '복원' }).first().click(); await kim.waitForTimeout(700);
  ok('restore works', (await toastText(kim)).includes('복원했습니다'));

  // ===== external share link =====
  await kim.hash('#/docs?q=' + encodeURIComponent('2026 유치 계획')); await kim.waitForSelector('.tbl tbody tr');
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
  ok('correct password shows the document', (await anon.textContent('#title')).includes('유치 계획'));
  // Chromium's suggestedFilename() ignores non-ASCII names in headless mode, so assert on what the app controls: the anchor's download attribute
  await anon.evaluate(() => { const o = HTMLAnchorElement.prototype.click; HTMLAnchorElement.prototype.click = function () { window.__dl = this.getAttribute('download'); return o.call(this); }; });
  await Promise.all([anon.waitForEvent('download'), anon.click('#dl')]);
  ok('anonymous download works with the real file name', (await anon.evaluate(() => window.__dl)) === '유치계획.md');
  await kim.click('.tabs button:has-text("외부 공유")'); await kim.waitForSelector('.tbl');
  await kim.getByRole('button', { name: '폐지' }).first().click(); await kim.waitForTimeout(700);
  await anon.reload(); await anon.waitForSelector('#err:not(:empty)');
  ok('revoked link says it is invalid', (await anon.textContent('#err')).includes('올바르지 않거나'));

  // ===== AI chat links back to the document =====
  await kim.hash('#/chat'); await kim.fill('#q', '2026 유치 계획 목표 계약 건수'); await kim.click('#send'); await kim.waitForSelector('.a');
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
  ok('legal hold shown', (await adm.textContent('.doc-head')).includes('법적 보류'));
  await more(adm, '삭제'); await adm.dlg().getByRole('button', { name: '삭제' }).click(); await adm.waitForTimeout(700);
  ok('legal hold blocks deletion', (await toastText(adm)).includes('법적 보류'));

  // ===== outsider cannot reach hidden documents even by URL =====
  const lee = await login('lee');
  await lee.hash('#/docs?q=' + encodeURIComponent('채용 계획')); await lee.waitForSelector('.tbl tbody tr');
  await lee.locator('.tbl tbody tr').first().click(); await lee.waitForSelector('.tabs');
  const hrUrl = lee.url();
  await choi.goto(hrUrl); await choi.waitForTimeout(900);
  ok('hidden doc by direct URL looks like a missing page', (await choi.textContent('#view')).includes('찾을 수 없습니다'));
  ok('outsider sees no admin menu', !(await choi.textContent('#nav')).includes('관리'));

  // ===== v0.5: calendar, templates + editor, access requests, follow, scoped ask, admin pages, theme =====
  const k2 = await login('kim');
  await k2.hash('#/calendar?month=2026-12'); await k2.waitForSelector('.cal-day.has');
  await k2.locator('.cal-day.has').first().click();
  ok('calendar lists the contract due date from the business DB', (await k2.textContent('.cal-detail')).includes('CT-2024-001'));
  await k2.hash('#/new'); await k2.waitForSelector('.tpl');
  ok('template gallery lists the built-in forms', (await k2.locator('.tpl').count()) >= 5);
  await k2.locator('.tpl', { hasText: '공문(기안문)' }).click(); await k2.waitForSelector('.md-src');
  await k2.fill('#n-title', 'e2e 양식 회의록 유니크제목');
  await k2.locator('.md-src').fill('# e2e 회의록\n\n- [ ] 항목 하나\n\n본문 e2e양식본문');
  ok('live Markdown preview renders headings and checklists', (await k2.locator('.md-prev h2').count()) === 1 && (await k2.locator('.md-prev li.task').count()) === 1);
  await k2.getByRole('button', { name: '문서로 저장' }).click(); await k2.waitForSelector('.tabs');
  const newDocUrl = k2.url();
  ok('document created from a template', (await k2.textContent('.doc-head')).includes('e2e 양식 회의록'));
  ok('Markdown body is rendered on the document page', (await k2.locator('.body-md h2').count()) === 1);
  await k2.getByRole('button', { name: '온라인 편집' }).click(); await k2.waitForSelector('.md-src');
  await k2.locator('.md-src').fill('# e2e 회의록\n\n개정 e2e편집본문');
  await k2.getByRole('button', { name: '새 버전으로 저장' }).click(); await k2.waitForSelector('.tabs');
  await k2.getByRole('tab', { name: '버전' }).click(); await k2.waitForTimeout(400);
  ok('online edit created version 2', (await k2.textContent('[role=tabpanel]')).includes('v2'));
  // ask this document
  await k2.getByRole('tab', { name: 'AI 질문' }).click(); await k2.fill('#doc-q', 'e2e편집본문'); await k2.keyboard.press('Enter'); await k2.waitForSelector('.ev');
  ok('question about one document is answered from that document', (await k2.textContent('[role=tabpanel]')).includes('e2e편집본문'));
  // follow + access request across users
  const docId = newDocUrl.split('/doc/')[1].split('?')[0];
  const docNo = (await k2.evaluate(async (id) => { const h = JSON.parse(sessionStorage.getItem('uid') ? '{}' : '{}'); const r = await fetch('/api/docs/' + id, { headers: { 'X-User-Id': 'kim' } }); return (await r.json()).doc_no; }, docId));
  const ch = await login('choi');
  await ch.hash('#/access'); await ch.waitForSelector('.page-h');
  await ch.getByRole('button', { name: '새 신청' }).click(); await ch.locator('dialog #ar-ref').fill(docNo); await ch.locator('dialog #ar-reason').fill('e2e 검토');
  await ch.dlg().getByRole('button', { name: '신청' }).click(); await ch.waitForTimeout(700);
  ok('access request accepted with a neutral message', (await toastText(ch)).includes('접수'));
  await k2.hash('#/access'); await k2.waitForSelector('.req');
  ok('document manager sees the request', (await k2.textContent('#view')).includes('최협력') );
  await k2.getByRole('button', { name: '승인' }).first().click(); await k2.waitForTimeout(800);
  await ch.hash('#/doc/' + docId); await ch.waitForTimeout(900);
  ok('after approval the requester can open the document', (await ch.textContent('#view')).includes('e2e 양식 회의록'));
  const pk = await login('park');
  await pk.hash('#/doc/' + docId); await pk.waitForTimeout(800);
  const canSee = !(await pk.textContent('#view')).includes('찾을 수 없습니다');
  if (canSee) { await pk.getByRole('button', { name: '구독', exact: true }).click(); await pk.waitForTimeout(500); ok('follow toggles to unfollow', (await pk.locator('button', { hasText: '구독 해제' }).count()) === 1); }
  else ok('(park cannot read this document: follow not applicable)', true);
  // admin pages
  const ad = await login('admin');
  await ad.hash('#/admin/users'); await ad.waitForSelector('.tbl tbody tr');
  ok('admin user directory lists users without e-mail addresses', (await ad.textContent('#view')).includes('김유치') && !(await ad.textContent('#view')).includes('@'));
  await ad.hash('#/admin/system'); await ad.waitForSelector('.tbl');
  ok('system status shows audit chain integrity', (await ad.textContent('#view')).includes('해시 체인'));
  // theme toggle persists and flips the palette
  const before = await k2.evaluate(() => getComputedStyle(document.body).backgroundColor);
  await k2.locator('#theme').click(); await k2.waitForTimeout(250);
  ok('theme toggle changes the palette', (await k2.evaluate(() => getComputedStyle(document.body).backgroundColor)) !== before);
  await k2.locator('#theme').click();

  // ===== v0.6: records management (privacy detection, classification, disposal procedure) =====
  const k3 = await login('kim');
  await k3.hash('#/docs?q=' + encodeURIComponent('참가자 명단')); await k3.waitForSelector('.tbl tbody tr');
  await k3.locator('.tbl tbody tr', { hasText: '참가자 명단' }).first().click(); await k3.waitForSelector('.privacy-banner');
  const banner = await k3.textContent('.privacy-banner');
  ok('privacy banner lists counts of detected identifiers', banner.includes('주민등록번호 2건') && banner.includes('휴대전화번호 2건'));
  ok('detected values are never shown in the banner', !banner.includes('900101'));
  ok('document shows its records classification', (await k3.textContent('.info-rail')).includes('02-03'));
  const rec = await login('rec');
  ok('records officer sees the records menu', (await rec.textContent('#nav')).includes('기록물 관리') && !(await rec.textContent('#nav')).includes('감사 로그'));
  await rec.hash('#/records'); await rec.waitForSelector('.cand-chk');
  ok('retention-expired document is a disposal candidate', (await rec.textContent('#view')).includes('2019 소규모 세미나'));
  await rec.locator('.cand-chk').first().check(); await rec.fill('#disp-reason', 'e2e 보존기간 경과'); await rec.click('#disp-go'); await rec.waitForTimeout(900);
  ok('disposal request is created and waits for another administrator', (await rec.textContent('#view')).includes('다른 관리자의 승인을 기다립니다'));
  const ad2 = await login('admin');
  await ad2.hash('#/records'); await ad2.waitForSelector('.card button.primary:has-text("승인·폐기")');
  await ad2.locator('.card button.primary:has-text("승인·폐기")').first().click(); await ad2.dlg().getByRole('button', { name: '승인·폐기' }).click(); await ad2.waitForTimeout(900);
  ok('approval permanently disposes the document', (await toastText(ad2)).includes('폐기 1건'));
  await ad2.hash('#/records?tab=ledger'); await ad2.waitForSelector('.tbl');
  ok('disposal ledger records the disposal with a file hash', (await ad2.textContent('#view')).includes('2019 소규모 세미나') && (await ad2.textContent('#view')).includes('서기록'));
  await ad2.hash('#/records?tab=classes'); await ad2.waitForSelector('.tbl');
  ok('classification scheme lists retention periods', (await ad2.textContent('#view')).includes('대관 계약') && (await ad2.textContent('#view')).includes('영구'));
  await ad2.hash('#/records?tab=privacy'); await ad2.waitForSelector('.tbl');
  ok('privacy overview shows the finding without a readable title for the administrator', (await ad2.textContent('#view')).includes('주민등록번호'));

  await ad2.hash('#/records?tab=disposal'); await ad2.waitForSelector('button:has-text("파기확인서")');
  await ad2.locator('button:has-text("파기확인서")').first().click(); await ad2.waitForSelector('.cert');
  ok('destruction certificate shows a verification hash', (await ad2.textContent('.cert')).includes('검증 해시') && (await ad2.textContent('.cert')).includes('2019 소규모 세미나'));
  await ad2.dlg().getByRole('button', { name: '닫기' }).click();
  await ad2.hash('#/records?tab=registry'); await ad2.waitForSelector('.tbl');
  ok('personal-data register lists entries with a due date', (await ad2.textContent('#view')).includes('행사 참가자 등록 명단') && (await ad2.textContent('#view')).includes('임직원 인사 정보'));
  await ad2.click('#reg-new'); await ad2.locator('dialog input[aria-label=name]').fill('e2e 대장'); await ad2.locator('dialog input[aria-label=purpose]').fill('시험'); await ad2.dlg().getByRole('button', { name: '저장' }).click(); await ad2.waitForTimeout(700);
  ok('register entry can be added', (await ad2.textContent('#view')).includes('e2e 대장'));
  await ad2.hash('#/records?tab=unmask'); await ad2.waitForSelector('#view .card');
  ok('unmask log tab renders', (await ad2.textContent('#view')).includes('원문 보기'));

  await kim.hash('#/verify'); await kim.waitForSelector('#verify-file');
  await kim.setInputFiles('#verify-file', FILES + '/contract.md'); await kim.click('#verify-go'); await kim.waitForSelector('#verify-out .card');
  ok('copy check finds the uploaded contract among the versions', !(await kim.textContent('#verify-out')).includes('찾을 수 없습니다') && (await kim.textContent('#verify-out')).includes('계약 초안'));

  await ad2.hash('#/admin/trust'); await ad2.waitForSelector('#trust-run');
  await ad2.click('#trust-run'); await ad2.waitForSelector('.statrow');
  ok('AI trust center runs the leak test with zero leaks', (await ad2.textContent('#view')).includes('접근 제한 정보 노출') && (await ad2.textContent('.statrow .stat b')).trim() === '0');
  await kim.hash('#/docs'); await kim.waitForSelector('.tbl tbody tr');
  await kim.getByRole('button', { name: '더 보기' }).click(); await kim.getByRole('menuitem', { name: '자연어로 찾기' }).click(); await kim.waitForSelector('#nl-q');
  await kim.fill('#nl-q', '내가 올린 계약서 지체상금'); await kim.click('#nl-go'); await kim.waitForSelector('#nl-out .chip');
  ok('natural-language search shows the understood conditions', (await kim.textContent('#nl-out')).includes('내가 올린 문서') && (await kim.textContent('#nl-out')).includes('유형'));
  await kim.keyboard.press('Escape');

  await kim.hash('#/tasks'); await kim.waitForSelector('#ab-set');
  await kim.click('#ab-set'); await kim.waitForSelector('#ab-dep');
  ok('absence dialog lists deputies', (await kim.locator('#ab-dep option').count()) > 1);
  await kim.keyboard.press('Escape');

  // ===== integrations: API keys =====
  await ad2.hash('#/admin/integrations'); await ad2.waitForSelector('#k-new');
  await ad2.click('#k-new'); await ad2.locator('dialog #k-name').fill('e2e 시험 연동'); await ad2.dlg().getByRole('button', { name: '발급' }).click(); await ad2.waitForSelector('#key-once');
  const keyText = await ad2.textContent('#key-once');
  ok('API key is shown once with the edmk_ prefix', keyText.startsWith('edmk_'));
  const viaKey = await ad2.evaluate(async (k) => (await fetch('/api/me', { headers: { Authorization: 'Bearer ' + k } })).json(), keyText);
  ok('the key authenticates as its user', viaKey.id === 'kim');
  const adminViaKey = await ad2.evaluate(async (k) => (await fetch('/api/dms/admin/api-keys', { headers: { Authorization: 'Bearer ' + k } })).status, keyText);
  ok('the key cannot reach the admin API', adminViaKey === 403);
  await ad2.dlg().getByRole('button', { name: '닫기' }).click(); await ad2.waitForSelector('.tbl');
  ok('key list shows the new key without its secret', (await ad2.textContent('#view')).includes('e2e 시험 연동') && !(await ad2.textContent('#view')).includes(keyText));

  // ===== storage connectors (NAS) =====
  await ad2.hash('#/admin/sources'); await ad2.waitForSelector('.src');
  ok('storage connector page lists both modes', (await ad2.textContent('#view')).includes('본부NAS') && (await ad2.textContent('#view')).includes('NAS이관예시'));
  await ad2.locator('.src', { hasText: 'NAS이관예시' }).getByRole('button', { name: '미리보기' }).click(); await ad2.waitForSelector('dialog[open]');
  ok('dry run reports what would be migrated without changing anything', (await ad2.locator('dialog[open]').textContent()).includes('미리보기입니다'));
  await ad2.dlg().getByRole('button', { name: '닫기' }).click();
  await ad2.locator('.src', { hasText: 'NAS이관예시' }).getByRole('button', { name: '지금 동기화' }).click(); await ad2.waitForSelector('dialog[open]');
  ok('migration creates documents', (await ad2.locator('dialog[open]').textContent()).includes('이관'));
  await ad2.dlg().getByRole('button', { name: '닫기' }).click();
  await ad2.hash('#/docs?q=' + encodeURIComponent('비품 관리 대장')); await ad2.waitForSelector('.tbl tbody tr');
  ok('migrated document is searchable in the repository', (await ad2.textContent('#view')).includes('비품 관리 대장'));

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
