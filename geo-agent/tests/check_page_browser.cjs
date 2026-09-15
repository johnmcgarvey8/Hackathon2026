const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('../.data/browser-check/node_modules/playwright');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const finding = { text: 'The page describes a farm shop. <img src=x onerror=alert(1)>', basis: 'observed', evidence: [{ passage_id: 'page-1', quote: 'Organic farm shop' }] };
    const records = [];
    const submittedKeys = new Map();
    let proposals = 0;
    let starts = 0;
    let loseNextResponse = false;
    let run = null;
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === '/chat') return route.continue();
      if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
      assert.equal(request.headers().authorization, 'Bearer dummy-browser-only');
      if (url.pathname === '/chat-policy') return route.fulfill({ json: { policy: { run_id: 'saved-run' }, budget: { remaining: 17, limit: 36 } } });
      if (url.pathname === '/analysis-policy') return route.fulfill({ json: { budget: { analyses_remaining: 10 - records.length, evaluations_remaining: 10 - proposals } } });
      if (url.pathname === '/page-analyses' && request.method() === 'POST') {
        const body = request.postDataJSON();
        let record = submittedKeys.get(body.idempotency_key);
        if (!record) {
          const failed = body.url.includes('unindexed');
          record = { analysis_id: `analysis-${records.length + 1}`, url: body.url, status: failed ? 'failed' : 'completed', created_at: '2026-09-14', retrieval_status: failed ? 'failed' : 'completed', analysis_status: failed ? 'not-started' : 'completed', error: failed ? 'Web IQ returned HTTP 404; no automatic retry' : null, evaluation: null, snapshot: failed ? null : { title: 'Farm shop', captured_at: '2026-09-14', crawled_at: null }, passages: failed ? [] : [{ passage_id: 'page-1', text: 'Organic farm shop' }], limits: ['Indexed excerpt only; not a citation score.'], report: failed ? null : { purpose: finding, audience: { ...finding, basis: 'inferred' }, entities: [finding], questions_answered: [finding], observations: [finding], improvements: [{ hypothesis: 'Clarify the visitor details', rationale: 'The excerpt is brief.', evidence: finding.evidence, verification: 'Review the full page first.' }] } };
          records.push(record); submittedKeys.set(body.idempotency_key, record);
        }
        if (loseNextResponse) { loseNextResponse = false; return route.abort('failed'); }
        return route.fulfill({ json: record });
      }
      if (url.pathname === '/page-analyses') return route.fulfill({ json: records });
      if (url.pathname.endsWith('/evaluation')) {
        const body = request.postDataJSON(); assert.equal(body.confirm_query_generation, true); proposals++;
        assert.equal(starts, 0);
        records[0].evaluation = { run_id: 'run-test', status: 'awaiting-query-approval' };
        run = { run_id: 'run-test', revision: 1, approval_hash: 'exact-hash', state: 'awaiting-query-approval', approval: null, results: [], scores: null, inputs: { brief: body, queries: Array.from({ length: 5 }, (_, index) => ({ query_id: `q-${index + 1}`, text: `Discovery query ${index + 1}`, intent: 'Visitor discovery', branded: false })) } };
        return route.fulfill({ json: records[0].evaluation });
      }
      if (url.pathname.startsWith('/page-analyses/')) return route.fulfill({ json: records.find(record => url.pathname.endsWith(record.analysis_id)) });
      if (url.pathname.endsWith('/query-approval')) {
        assert.deepEqual(request.postDataJSON(), { expected_revision: 1, input_hash: 'exact-hash' });
        run.approval = { input_hash: 'exact-hash' }; return route.fulfill({ json: run });
      }
      if (url.pathname.endsWith('/start')) {
        assert.ok(run.approval); starts++; run.state = 'recommendations-ready';
        run.results = run.inputs.queries.map(query => ({ query_id: query.query_id, status: 'completed', answer: 'Saved answer', sources: [] }));
        run.scores = { overall: { score: 0 } }; return route.fulfill({ json: run });
      }
      if (url.pathname === '/runs/run-test') return route.fulfill({ json: run });
      throw new Error('Unexpected request: ' + url.pathname);
    });
    await page.goto('http://127.0.0.1:8090/chat?view=analysis');
    await page.getByLabel('Local access token', { exact: true }).fill('dummy-browser-only');
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByLabel('Public URL', { exact: true }).fill('https://example.com/farm');
    await page.locator('#analysis-consent').check();
    await page.getByRole('button', { name: 'Analyse page', exact: true }).click();
    await page.getByRole('heading', { name: 'Improvement hypotheses' }).waitFor();
    assert.equal(records.length, 1); assert.equal(proposals, 0); assert.equal(starts, 0);
    assert.equal(await page.locator('#page-report img').count(), 0);
    assert.equal(await page.locator('#execute-evaluation').isVisible(), false);
    await page.getByLabel('Target audience', { exact: true }).fill('UK visitors');
    await page.locator('#proposal-consent').check();
    await page.getByRole('button', { name: 'Propose 5 queries' }).click();
    await page.getByRole('button', { name: 'Approve these exact queries' }).waitFor();
    assert.equal(starts, 0); assert.equal(await page.locator('#execute-evaluation').isVisible(), false);
    await page.getByRole('button', { name: 'Approve these exact queries' }).click();
    await page.getByRole('button', { name: 'Run 5 searches + 5 answer calls' }).click();
    await page.getByRole('heading', { name: 'Exact-page citation score: 0 / 100' }).waitFor();
    assert.equal(proposals, 1); assert.equal(starts, 1);
    await page.screenshot({ path: path.resolve(__dirname, '../.data/page-analysis-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.resolve(__dirname, '../.data/page-analysis-mobile.png'), fullPage: true });
    await page.getByLabel('Public URL', { exact: true }).fill('https://example.com/unindexed');
    await page.getByRole('button', { name: 'Analyse page', exact: true }).click();
    await page.getByText('Retrieval: failed / Analysis: not-started', { exact: true }).waitFor();
    assert.equal(await page.locator('#evaluation-section').isVisible(), false);
    loseNextResponse = true;
    await page.getByLabel('Public URL', { exact: true }).fill('https://example.com/recovery');
    await page.getByRole('button', { name: 'Analyse page', exact: true }).click();
    await page.locator('#status.error').waitFor();
    assert.equal(await page.locator('#analyse').isDisabled(), true);
    await page.getByRole('button', { name: 'Check saved request' }).click();
    await page.getByRole('heading', { name: 'Improvement hypotheses' }).waitFor();
    assert.equal(records.length, 3); assert.equal(submittedKeys.size, 3);
    assert.deepEqual(errors, []);
    console.log('PASS: analysis, exact approval, safe rendering, failed retrieval, idempotent recovery, desktop/mobile; no provider calls.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });