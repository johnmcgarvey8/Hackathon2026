const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('../.data/browser-check/node_modules/playwright');

const baseUrl = process.env.GEO_BROWSER_BASE_URL || 'http://127.0.0.1:8091';
const token = process.env.GEO_BROWSER_TOKEN;
assert.ok(token, 'GEO_BROWSER_TOKEN is required.');

(async () => {
  const preflight = await fetch(`${baseUrl}/api/v2/policy`, { headers: { Authorization: `Bearer ${token}` } });
  assert.equal(preflight.status, 200, 'Mock policy preflight must succeed before creating any runs.');
  assert.equal((await preflight.json()).execution_mode, 'mock', 'This browser check must never run against a live policy.');
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
    assert.equal(await page.locator('#brief-form input').count(), 3);
    assert.equal(await page.locator('#brief-form input[type=checkbox]').count(), 0);
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await page.locator('#run-detail .state').getByText('draft', { exact: true }).waitFor();
    assert.equal(await page.locator('.run-heading a').innerText(), 'https://clarity.microsoft.com/');

    assert.equal(await page.getByLabel('Local access token', { exact: true }).inputValue(), '');
    assert.equal(await page.evaluate(() => JSON.stringify({ local: localStorage, session: sessionStorage }).includes('ui-test-token')), false);
    assert.equal(await page.locator('body').textContent().then(text => text.includes(token)), false);

    await page.locator('#prepare-confirm').check();
    await page.getByRole('button', { name: 'Prepare query packet' }).click();
    await page.getByRole('button', { name: 'Approve exact queries' }).waitFor({ timeout: 20000 });
    await page.locator('.saved-brand-context:visible').getByText(/Microsoft Clarity.*Inferred from page \+ model knowledge/).waitFor();
    const runId = new URL(page.url()).searchParams.get('run');
    const assessmentResponse = await fetch(`${baseUrl}/api/v2/runs/${runId}/evidence-assessment`, {headers: {Authorization: `Bearer ${token}`}});
    assert.equal(assessmentResponse.status, 200);
    const savedAssessment = await assessmentResponse.json();
    assert.equal(savedAssessment.brand_definition.source, 'page-analysis');
    assert.equal(savedAssessment.brand_definition.definition.name, 'Microsoft Clarity');
    assert.deepEqual(savedAssessment.brand_definition.definition.domains, ['clarity.microsoft.com']);
    assert.equal(await page.locator('.query').count(), 5);
    await page.getByRole('button', { name: 'Edit query packet' }).click();
    await page.locator('[data-query-id="q-1"] [data-field="chat_query"]').fill('Which behavioural analytics tools support product teams?');
    await page.getByRole('button', { name: 'Save query packet' }).click();
    await page.getByText('Which behavioural analytics tools support product teams?', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Approve exact queries' }).click();
    await page.locator('#evaluate-confirm').check();
    assert.equal(await page.locator('#include-recommendations').count(), 0);
    await page.getByRole('button', { name: 'Start measurement' }).click();
    await page.getByRole('tab', {name: 'Grounding recommendations', exact: true}).waitFor({timeout: 30000});
    assert.equal(await page.locator('#stage-4').getAttribute('aria-pressed'), 'true');
    const savedRunResponse = await fetch(`${baseUrl}/api/v2/runs/${runId}`, {headers: {Authorization: `Bearer ${token}`}});
    const savedRun = await savedRunResponse.json();
    assert.equal(savedRun.recommendations, null);
    const strategyResponse = await fetch(`${baseUrl}/api/v2/runs/${runId}/content-strategy`, {headers: {Authorization: `Bearer ${token}`}});
    assert.equal(strategyResponse.status, 200);
    const strategy = await strategyResponse.json();
    assert.equal(strategy.report.completed_answers, 15);
    assert.equal(strategy.report.retrieved_queries, 5);
    assert.equal(strategy.report.provenance, 'synthetic');
    await page.getByRole('tab', {name: 'LLM recommendations', exact: true}).click();
    await page.getByRole('heading', {name: 'What the LLM cited and expressed', exact: true}).waitFor();
    const strategyDownloadEvent = page.waitForEvent('download');
    await page.locator('#download-strategy-detail').click();
    const strategyBytes = await fs.readFile(await (await strategyDownloadEvent).path());
    assert.equal(strategyBytes.subarray(0, 2).toString('ascii'), 'PK');
    await page.screenshot({path: path.resolve(__dirname, '../.data/recommendations-real-desktop.png'), fullPage: true});
    await page.getByRole('button', {name: 'Continue to export', exact: true}).click();
    await page.getByRole('button', { name: 'Create ZIP export' }).waitFor({ timeout: 30000 });
    await page.getByRole('heading', { name: 'Provenance and limits' }).waitFor();
    await page.getByText('Controlled simulation. The page, retrieval packets, answers, and score are test data; they do not measure public product performance.', { exact: true }).waitFor();
    await page.locator('#assessment-tab-grounding').waitFor({state: 'attached'});
    assert.equal(await page.locator('.result').count(), 0);
    assert.equal(await page.getByText(/synthetic evidence$/).count(), 1);
    assert.equal(await page.getByText('100%', { exact: true }).count(), 1);

    await page.getByRole('button', {name: 'Review measurement', exact: true}).click();
    await page.getByRole('tab', {name: 'Grounding evidence', exact: true}).click();
    assert.ok(await page.locator('#assessment-view .assessment-source').count() > 0);
    await page.getByRole('tab', {name: 'Answer and citations', exact: true}).click();
    assert.ok(await page.locator('#assessment-view .assessment-answer').innerText());
    await page.getByRole('tab', {name: 'Source trace', exact: true}).click();
    await page.getByText('Selection reason not recorded', {exact: true}).first().waitFor();
    await page.getByRole('button', { name: 'Inspect saved evidence' }).first().click();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await page.getByRole('button', {name: 'Export', exact: true}).click();
    const assessmentDownloadEvent = page.waitForEvent('download');
    await page.getByRole('button', {name: 'Download assessment ZIP', exact: true}).click();
    const assessmentDownload = await assessmentDownloadEvent;
    const assessmentBytes = await fs.readFile(await assessmentDownload.path());
    assert.equal(assessmentBytes.subarray(0, 2).toString('ascii'), 'PK');
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
    await page.getByRole('button', {name: 'Review measurement', exact: true}).click();
    await page.getByRole('tab', {name: 'Source trace', exact: true}).click();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.resolve(__dirname, '../.data/measurement-real-mobile.png'), fullPage: true });

    assert.deepEqual(errors, []);
    assert.deepEqual(unexpectedRequests, []);
    console.log('PASS: durable mock six-stage workflow, automatic recommendations, three valid ZIP downloads, recovery, and no external requests.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });