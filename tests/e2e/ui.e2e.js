// Browser e2e. Needs a running demo server on a FRESH workspace:  python3 -m edm_ai demo --no-browser
// Run:  PLAYWRIGHT_PATH=<path to playwright module> [CHROMIUM_PATH=...] node tests/e2e/ui.e2e.js
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const B = process.env.EDM_URL || 'http://127.0.0.1:8080/';
const FILES = process.env.E2E_FILES || __dirname + '/files';
(async () => {
  const b = await chromium.launch({ ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const errs = [];
  async function login(user) {
    const p = await (await b.newContext({ viewport: { width: 1200, height: 1000 } })).newPage();
    p.on('pageerror', e => errs.push(user + ': ' + e.message));
    p.on('console', m => m.type() === 'error' && errs.push(user + ' console: ' + m.text()));
    await p.goto(B); await p.selectOption('#demo-user', user); await p.click('#login-go');
    await p.waitForSelector('#chat:not([hidden])'); return p;
  }
  const ok = (name, cond) => console.log((cond ? 'PASS ' : 'FAIL ') + name);

  // jung (consulting): alert badge + alert tab + doc detail with DB comparison
  const jung = await login('jung');
  await jung.waitForSelector('#alert-count:not([hidden])');
  ok('alert badge shown for consulting user', (await jung.textContent('#alert-count')) === '2');
  await jung.click('#tab-alerts'); await jung.waitForSelector('.alert-card');
  ok('alert mentions amount mismatch', (await jung.textContent('#alert-list')).includes('150,000,000원이 DB 값 120,000,000원'));
  await jung.click('.alert-card button'); await jung.waitForSelector('#doc-detail h2');
  await jung.waitForSelector('#doc-detail .tbl');
  ok('doc detail shows mismatch pill', (await jung.textContent('#doc-detail')).includes('불일치'));
  ok('doc detail shows linked DB row', (await jung.textContent('#doc-detail')).includes('고객사'));
  await jung.screenshot({ path: (process.env.SHOTS || '.') + '/e2e-alert-detail.png' });

  // kim (sales): no alerts, cannot see HR docs; upload with hostile name/title
  const kim = await login('kim');
  ok('no alert badge for sales user', await kim.$eval('#alert-count', n => n.hidden));
  await kim.click('#tab-docs'); await kim.waitForSelector('.doc-item');
  const titles = await kim.$$eval('.doc-item b', n => n.map(x => x.textContent));
  ok('sales list hides HR doc', !titles.some(t => t.includes('성과평가')));
  await kim.click('#upload-box summary');
  await kim.setInputFiles('#up-file', FILES + '/memo<b>x.md');
  await kim.click('#up-go'); await kim.waitForFunction(() => document.querySelector('#up-msg').textContent.startsWith('올렸습니다'));
  ok('uploaded doc appears + detail open', (await kim.textContent('#doc-detail')).includes('신규 메모'));
  ok('XSS payload not executed', !(await kim.evaluate(() => !!window.__xss)));
  ok('no <img> injected', (await kim.$$('#doc-detail img, #doc-list img')).length === 0);
  ok('owner sees ACL editor', !!(await kim.$('#ed-level')));
  // search finds it
  await kim.fill('#doc-q', 'e2e유니크토큰'); await kim.click('#doc-search button');
  await kim.waitForTimeout(400);
  ok('search finds uploaded doc', (await kim.$$eval('.doc-item b', n => n.length)) === 1);
  // choi cannot see it
  const choi = await login('choi');
  await choi.click('#tab-docs'); await choi.waitForSelector('.doc-item');
  await choi.fill('#doc-q', 'e2e유니크토큰'); await choi.click('#doc-search button'); await choi.waitForTimeout(400);
  ok('external user cannot find sales upload', (await choi.$$eval('.doc-item', n => n.length)) === 0);
  // owner opens up ACL to external level-1 -> choi can find it
  await kim.fill('#ed-depts', 'sales, external'); await kim.fill('#ed-level', '1'); await kim.click('text=권한 저장');
  await kim.waitForFunction(() => document.querySelector('#doc-detail .hint[role=status]')?.textContent.includes('저장'));
  await choi.click('#doc-search button'); await choi.waitForTimeout(400);
  ok('after ACL change external user finds it', (await choi.$$eval('.doc-item', n => n.length)) === 1);
  await choi.click('.doc-item');await choi.waitForSelector('#doc-detail h2');
  ok('non-owner has no ACL editor', !(await choi.$('#ed-level')));
  await kim.screenshot({ path: (process.env.SHOTS || '.') + '/e2e-docs.png' });
  // two-step delete
  await kim.click('text=삭제'); ok('delete needs confirmation', (await kim.textContent('#doc-detail')).includes('한 번 더'));
  await kim.click('text=삭제'); await kim.waitForFunction(() => document.querySelector('#doc-detail').textContent.includes('삭제했습니다'));
  await kim.fill('#doc-q', 'e2e유니크토큰'); await kim.click('#doc-search button'); await kim.waitForTimeout(400);
  ok('deleted doc is gone', (await kim.$$eval('.doc-item', n => n.length)) === 0);

  // admin: sees everything incl. unreadable marker, audit shows edm actions
  const adm = await login('admin'); await adm.click('#tab-docs'); await adm.waitForSelector('.doc-item');
  ok('admin list marks unreadable docs', (await adm.$$('.doc-item.dim')).length > 0);
  await adm.click('#tab-audit'); await adm.waitForSelector('#audit-rows tr');
  { const t = await adm.textContent('#audit-rows'); ok('audit records upload/ACL/delete', ['[문서 업로드]', '[권한 변경]', '[문서 삭제]'].every(x => t.includes(x))); }
  console.log('page errors:', errs.length ? errs : 'none');
  await b.close();
})();
