// Takes the screenshots used in docs/USER_MANUAL.md and docs/ADMIN_MANUAL.md from a running demo server (fresh workspace).
//   python3 examples/demo/build_demo.py && python3 -m edm_ai demo --no-browser --port 8791
//   EDM_URL=http://127.0.0.1:8791/ PLAYWRIGHT_PATH=<playwright module> node tools/make_manual_shots.js
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const B = process.env.EDM_URL || 'http://127.0.0.1:8080/';
const OUT = __dirname + '/../docs/manual';
const USER = [['01-dashboard', '#/dashboard'], ['02-docs', '#/docs'], ['03-chat', '#/chat'], ['04-tasks', '#/tasks'], ['05-verify', '#/verify'], ['06-calendar', '#/calendar'], ['07-access', '#/access']];
const ADMIN = [['10-records-disposal', '#/records'], ['11-records-privacy', '#/records?tab=privacy'], ['12-records-registry', '#/records?tab=registry'], ['13-trust', '#/admin/trust'], ['14-ops', '#/admin/ops'],
  ['15-sources', '#/admin/sources'], ['16-mail', '#/admin/mail'], ['17-integrations', '#/admin/integrations'], ['18-reports', '#/admin/reports'], ['19-types', '#/admin/types'], ['20-audit', '#/admin/audit']];
(async () => {
  const b = await chromium.launch({ ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const shoot = async (user, list, extra) => {
    const ctx = await b.newContext({ viewport: { width: 1360, height: 860 } });
    const p = await ctx.newPage();
    await p.goto(B); await p.selectOption('#demo-user', user); await p.click('#login-go'); await p.waitForSelector('#shell:not([hidden])');
    for (const [name, hash] of list) {
      await p.evaluate((h) => { location.hash = h; }, hash); await p.waitForTimeout(1200);
      await p.screenshot({ path: `${OUT}/${name}.png` }); console.log('shot', name);
    }
    if (extra) await extra(p);
    await ctx.close();
  };
  await shoot('kim', USER, async (p) => {
    await p.evaluate(() => { location.hash = '#/docs'; }); await p.waitForTimeout(800);
    await p.locator('.tbl tbody tr').first().click(); await p.waitForSelector('.tabs'); await p.waitForTimeout(600);
    await p.screenshot({ path: `${OUT}/08-doc.png` }); console.log('shot 08-doc');
  });
  await shoot('admin', ADMIN);
  await b.close();
})().catch((e) => { console.error(e); process.exit(1); });
