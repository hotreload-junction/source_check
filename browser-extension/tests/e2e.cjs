const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
// End-to-end smoke test in a real Chromium (not part of tools/check: needs a browser and a running backend).
//   1. backend on :8000:  cd backend && DATABASE_URL=sqlite:///e2e.db uvicorn app.main:app --port 8000
//   2. E2E=1 VITE_API_BASE=http://localhost:8000 npx vite build      (-> dist/e2e, test build with access to every site)
//   3. node tests/e2e.cjs [out-dir]                                   (needs playwright; screenshots go to out-dir)
// The article page is served by Playwright under the demo case's real URL, so the backend answers from the demo files
// (no LLM needed). The selection check starts a real analysis: without LLM keys it ends in "claim extraction failed".
const os = require('os');
const SP = process.argv[2] || fs.mkdtempSync(path.join(os.tmpdir(), 'sc-e2e-'));
const ROOT = path.resolve(__dirname, '../..');
const EXT = path.join(ROOT, 'browser-extension/dist/e2e');
const ART = 'https://www.theguardian.com/world/2026/nov/14/spacex-not-honest-slowing-starshield-elon-musk-volodymyr-zelenskyy';
const demo = JSON.parse(fs.readFileSync(path.join(ROOT, 'backend/demo/results/starshield.json'), 'utf8'));
const res = demo.result || demo.analysis?.result || demo;
const md = res.article.text_markdown;
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
const body = md.split(/\n\n+/).map((p) => `<p>${esc(p).replace(/\[([^\]]*)\]\(([^)]*)\)/g, '<a href="$2">$1</a>')}</p>`).join('\n');
const HTML = `<html><head><meta charset="utf-8"><title>${esc(res.article.title)}</title></head><body><article><h1>${esc(res.article.title)}</h1>${body}</article></body></html>`;

(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(SP, 'profile-' + Date.now()), {
    channel: 'chromium', headless: true,
    args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
  });
  let [sw] = ctx.serviceWorkers();
  if (!sw) sw = await ctx.waitForEvent('serviceworker');
  const id = new URL(sw.url()).host;
  console.log('extension id', id);
  await ctx.route('https://www.theguardian.com/**', (r) => r.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: HTML }));
  const page = await ctx.newPage();
  await page.goto(ART);
  const tabId = await sw.evaluate(async (u) => (await chrome.tabs.query({ url: u }))[0]?.id, ART);
  console.log('tab', tabId);
  const panel = await ctx.newPage();
  await panel.setViewportSize({ width: 420, height: 900 });
  panel.on('console', (m) => { if (m.type() === 'error') console.log('panel console:', m.text()); });
  await panel.goto(`chrome-extension://${id}/sidepanel.html?tab=${tabId}`);
  await panel.getByText('Check this article').waitFor();
  await panel.screenshot({ path: path.join(SP, '1-home.png') });
  await panel.getByText('Check this article').click();
  await panel.waitForFunction(() => location.hash.startsWith('#/trace'), null, { timeout: 15000 });
  console.log('hash', await panel.evaluate(() => location.hash));
  await panel.waitForTimeout(5000);
  await panel.screenshot({ path: path.join(SP, '2-result.png') });

  // selection: select one sentence of the article and use "Check the selected claim"
  const sentence = await page.evaluate(() => {
    const p = [...document.querySelectorAll('article p')].find((x) => x.textContent.length > 120);
    const range = document.createRange();
    const t = p.firstChild; const txt = t.textContent; const end = Math.max(txt.indexOf('. ') + 1, 60);
    range.setStart(t, 0); range.setEnd(t, Math.min(end, txt.length));
    getSelection().removeAllRanges(); getSelection().addRange(range);
    return String(getSelection());
  });
  console.log('selected:', sentence.slice(0, 100));
  await panel.evaluate(() => { location.hash = '#/'; });
  await panel.getByText('Check the selected claim').click();
  await panel.waitForTimeout(2500);
  const st = await sw.evaluate(async (t) => (await chrome.storage.session.get(`check:${t}`))[`check:${t}`], tabId);
  console.log('state after selection:', JSON.stringify({ ...st, title: undefined }));
  await panel.waitForTimeout(3000);
  await panel.screenshot({ path: path.join(SP, '3-selection.png') });
  await ctx.close();
  console.log('screenshots in', SP);
})().catch((e) => { console.error(e); process.exit(1); });
