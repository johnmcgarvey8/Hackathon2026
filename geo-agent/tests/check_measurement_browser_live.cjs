const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('../.data/browser-check/node_modules/playwright');

const baseUrl = process.env.GEO_BROWSER_BASE_URL || 'http://127.0.0.1:8091';
const token = process.env.GEO_BROWSER_TOKEN;
assert.ok(token, 'GEO_BROWSER_TOKEN is required.');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
    const errors = [];
    const unexpectedRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.origin !== baseUrl && url.protocol !== 'blob:') unexpectedRequests.push(url.href);
    });

    await page.goto(`${baseUrl}/measurements`);
    await page.getByLabel('Local access token', { exact: true }).fill(token);
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByRole('heading', { name: 'New measurement' }).waitFor();
    await page.getByLabel('Public URL').fill('https://clarity.microsoft.com/');
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await page.locator('#run-detail .state').getByText('draft', { exact: true }).waitFor();
    assert.equal(await page.locator('.run-heading a').innerText(), 'https://clarity.microsoft.com/');

    assert.equal(await page.getByLabel('Local access token', { exact: true }).inputValue(), '');
    assert.equal(await page.evaluate(() => JSON.stringify({ local: localStorage, session: sessionStorage }).includes('ui-test-token')), false);
    assert.equal(await page.locator('body').textContent().then(text => text.includes(token)), false);

    await page.locator('#prepare-confirm').check();
    await page.getByRole('button', { name: 'Prepare query packet' }).click();
    await page.getByRole('button', { name: 'Approve exact queries' }).waitFor({ timeout: 20000 });
    assert.equal(await page.locator('.query').count(), 5);
    await page.getByRole('button', { name: 'Edit query packet' }).click();
    await page.locator('[data-query-id="q-1"] [data-field="chat_query"]').fill('Which behavioural analytics tools support product teams?');
    await page.getByRole('button', { name: 'Save query packet' }).click();
    await page.getByText('Which behavioural analytics tools support product teams?', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Approve exact queries' }).click();
    await page.locator('#evaluate-confirm').check();
    await page.locator('#include-recommendations').check();
    await page.getByRole('button', { name: 'Start measurement' }).click();
    await page.getByRole('button', { name: 'Create ZIP export' }).waitFor({ timeout: 30000 });
    await page.getByRole('heading', { name: 'Provenance and limits' }).waitFor();
    await page.getByText('Controlled simulation. The page, retrieval packets, answers, and score are test data; they do not measure public product performance.', { exact: true }).waitFor();
    assert.equal(await page.locator('.result').count(), 15);
    assert.equal(await page.getByText(/synthetic evidence$/).count(), 16);
    assert.equal(await page.getByText('100%', { exact: true }).count(), 1);

    await page.locator('.result details summary').first().click();
    await page.getByRole('button', { name: 'Inspect saved evidence' }).first().click();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await page.getByRole('button', { name: 'Create ZIP export' }).click();
    await page.getByRole('button', { name: 'Download ZIP' }).waitFor();
    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download ZIP' }).click();
    const download = await downloadEvent;
    const bytes = await fs.readFile(await download.path());
    assert.equal(bytes.subarray(0, 2).toString('ascii'), 'PK');
    await page.screenshot({ path: path.resolve(__dirname, '../.data/measurement-real-desktop.png'), fullPage: true });

    await page.reload();
    await page.getByLabel('Local access token', { exact: true }).fill(token);
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByRole('button', { name: 'Download ZIP' }).waitFor({ timeout: 10000 });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.resolve(__dirname, '../.data/measurement-real-mobile.png'), fullPage: true });

    assert.deepEqual(errors, []);
    assert.deepEqual(unexpectedRequests, []);
    console.log('PASS: durable mock workflow completed in the browser with visible provenance, a valid ZIP, recovery, and no external requests.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });