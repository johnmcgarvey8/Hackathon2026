const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('../.data/browser-check/node_modules/playwright');

const baseUrl = process.env.GEO_BROWSER_BASE_URL || 'http://127.0.0.1:8091';

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const brief = { url: 'https://example.com/mock-page', audience: 'Research buyers', goal: 'Compare trusted options', locale: 'en-GB' };
    const profiles = ['chatgpt-style', 'claude-backed', 'copilot-style'].map(profile_id => ({ profile_id }));
    const queries = Array.from({ length: 5 }, (_, offset) => {
      const index = offset + 1;
      return { query_id: `q-${index}`, priority: index, rationale: `Rationale ${index}`, intent: `Intent ${index}`, branded: false, chat_query: `Buyer question ${index}?`, grounding_query: `buyer search ${index}`, evidence: [{ evidence_id: 'page-1', quote: 'Synthetic page evidence' }] };
    });
    const source = { evidence_id: 'q-1-target', url: brief.url, title: 'Saved <img src=x onerror=alert(1)> evidence', excerpt: 'Synthetic saved evidence.', provenance: 'synthetic', returned_position: 2 };
    const recommendations = { status: 'completed', reason: '', limitations: 'Synthetic draft only. Human verification is required and no publishing is authorised.', tasks: [{ task_id: 'rec-1', priority: 1, query_id: 'q-1', confidence: 'low', title: 'Clarify the evidence summary', target_section: 'Overview', proposed_change: 'Add a concise, verifiable summary for the approved audience.', rationale: 'The saved comparison excerpt is more direct for the approved query.', verification: 'Re-run the approved measurement after a separately approved edit.', page_evidence: [{ evidence_id: 'page-1', quote: 'Synthetic page evidence' }], comparison_evidence: [{ evidence_id: 'q-1-target', quote: 'Synthetic saved evidence.' }] }] };
    const inputs = { brief, snapshot: { url: brief.url, title: 'Synthetic page', content: 'Synthetic page evidence', provenance: 'synthetic' }, query_plan: { queries }, profiles };
    const events = type => [{ sequence: 1, event_type: 'draft', occurred_at: '2026-09-15T10:00:00Z' }, ...(type === 'draft' ? [] : [{ sequence: 2, event_type: type, occurred_at: '2026-09-15T10:01:00Z' }])];
    let run = null;
    let job = null;
    let artifact = null;
    let externalCalls = 0;
    let briefSubmissions = 0;

    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === '/measurements') return route.continue();
      if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
      if (!url.pathname.startsWith('/api/v2/')) { externalCalls++; throw new Error(`Unexpected external request: ${url.href}`); }
      assert.equal(request.headers().authorization, 'Bearer dummy-browser-only');
      if (url.pathname === '/api/v2/policy') return route.fulfill({ json: { policy_id: 'mock-ui', execution_mode: 'mock', allowed_domains: ['example.com'], locale: 'en-GB', profiles, limits: { preparation_calls: 3, search_calls: 5, evaluator_calls: 15, recommendation_calls: 1, automatic_retries: false } } });
      if (url.pathname === '/api/v2/runs' && request.method() === 'GET') return route.fulfill({ json: run ? [run] : [] });
      if (url.pathname === '/api/v2/briefs') {
        briefSubmissions++;
        assert.deepEqual(request.postDataJSON(), brief);
        run = { run_id: 'run-ui', revision: 1, state: 'draft', brief, inputs: null, approval: null, approval_hash: null, measurement: null, recommendations: null, scores: null, events: events('draft') };
        return route.fulfill({ status: 201, json: run });
      }
      if (url.pathname === '/api/v2/runs/run-ui/prepare') {
        assert.equal(request.postDataJSON().confirm_preparation_calls, true);
        const queued = { ...run, revision: 2, state: 'preparing', events: events('preparation-queued') };
        job = { job_id: 'prepare-job', run_id: 'run-ui', job_type: 'prepare', state: 'queued' };
        run = { ...queued, revision: 3, state: 'awaiting-query-approval', inputs, approval_hash: 'a'.repeat(64), events: events('awaiting-query-approval') };
        return route.fulfill({ status: 202, json: { job, run: queued } });
      }
      if (url.pathname === '/api/v2/runs/run-ui/queries' && request.method() === 'PUT') {
        const body = request.postDataJSON(); assert.equal(body.expected_revision, run.revision);
        assert.equal(body.queries[0].chat_query, 'Edited buyer question 1?');
        inputs.query_plan.queries = body.queries;
        run = { ...run, revision: run.revision + 1, inputs, approval: null, approval_hash: 'c'.repeat(64), events: events('inputs-revised') };
        return route.fulfill({ json: run });
      }
      if (url.pathname === '/api/v2/runs/run-ui/query-approval') {
        assert.deepEqual(request.postDataJSON(), { expected_revision: run.revision, input_hash: run.approval_hash });
        run = { ...run, revision: run.revision + 1, approval: { revision: run.revision, input_hash: run.approval_hash }, events: events('queries-approved') };
        return route.fulfill({ json: run });
      }
      if (url.pathname === '/api/v2/runs/run-ui/start') {
        const body = request.postDataJSON(); assert.equal(body.confirm_evaluation_calls, true); assert.equal(body.include_recommendations, true);
        const queued = { ...run, revision: run.revision + 1, state: 'queued', events: events('evaluation-queued') };
        job = { job_id: 'evaluate-job', run_id: 'run-ui', job_type: 'evaluate', state: 'queued' };
        const results = queries.flatMap(query => profiles.map(profile => ({ query_id: query.query_id, profile_id: profile.profile_id, provenance: 'synthetic', status: 'completed', answer: `Synthetic answer for ${query.query_id}`, citation_ids: [source.evidence_id], sources: [source] })));
        run = { ...queued, revision: queued.revision + 2, state: 'ready', measurement: { inputs, retrievals: queries.map(query => ({ query_id: query.query_id, grounding_query: query.grounding_query, provenance: 'synthetic', status: 'completed', sources: [source] })), results }, recommendations, recommendation_review: null, scores: { overall: { score: 40, numerator: 6, denominator: 15 }, retrieval: { coverage: 1 } }, events: events('ready') };
        return route.fulfill({ status: 202, json: { job, run: queued } });
      }
      if (url.pathname === '/api/v2/runs/run-ui/recommendation-review') {
        assert.deepEqual(request.postDataJSON(), { expected_revision: run.revision, decisions: [{ task_id: 'rec-1', decision: 'accepted' }] });
        run = { ...run, revision: run.revision + 1, recommendation_review: { decisions: [{ task_id: 'rec-1', decision: 'accepted' }], publish_permission: false }, events: events('recommendations-reviewed') };
        return route.fulfill({ json: run });
      }
      if (url.pathname === '/api/v2/runs/run-ui/exports') {
        assert.deepEqual(request.postDataJSON(), { expected_revision: run.revision });
        run = { ...run, revision: run.revision + 1, state: 'exported', events: events('exported') };
        artifact = { artifact_id: 'artifact-ui', run_id: 'run-ui', content_hash: 'b'.repeat(64), media_type: 'application/zip', size: 2048, created_at: '2026-09-15T10:05:00Z' };
        return route.fulfill({ status: 201, json: { run, artifact, download_url: '/api/v2/runs/run-ui/artifacts/artifact-ui' } });
      }
      if (url.pathname === '/api/v2/runs/run-ui/jobs') return route.fulfill({ json: job ? [job] : [] });
      if (url.pathname === '/api/v2/runs/run-ui/artifacts' && request.method() === 'GET') return route.fulfill({ json: artifact ? [artifact] : [] });
      if (url.pathname === '/api/v2/runs/run-ui/artifacts/artifact-ui') return route.fulfill({ status: 200, contentType: 'application/zip', body: Buffer.from('synthetic zip') });
      if (url.pathname === '/api/v2/runs/run-ui/evidence/q-1-target') return route.fulfill({ json: source });
      if (url.pathname === '/api/v2/runs/run-ui' && request.method() === 'GET') return route.fulfill({ json: run });
      throw new Error(`Unexpected request: ${request.method()} ${url.pathname}`);
    });

    await page.goto(`${baseUrl}/measurements`);
    await page.getByLabel('Local access token', { exact: true }).fill('dummy-browser-only');
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByRole('heading', { name: 'New measurement' }).waitFor();
    await page.getByText('Allowed host: example.com', { exact: true }).waitFor();
    await page.getByLabel('Public URL').fill('https://contoso.com/not-allowed');
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await page.getByText('This policy allows: example.com. Use the default URL for the mock demo. No automatic retry.', { exact: true }).waitFor();
    assert.equal(briefSubmissions, 0);
    await page.getByLabel('Public URL').fill(brief.url);
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await page.locator('#run-detail .state').getByText('draft', { exact: true }).waitFor();
    await page.locator('#prepare-confirm').check();
    await page.getByRole('button', { name: 'Prepare query packet' }).click();
    await page.getByRole('button', { name: 'Approve exact queries' }).waitFor();
    assert.equal(await page.locator('.query').count(), 5);
    await page.getByRole('button', { name: 'Edit query packet' }).click();
    await page.locator('[data-query-id="q-1"] [data-field="chat_query"]').fill('Edited buyer question 1?');
    await page.getByRole('button', { name: 'Save query packet' }).click();
    await page.getByText('Edited buyer question 1?', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Approve exact queries' }).click();
    await page.locator('#evaluate-confirm').check();
    await page.locator('#include-recommendations').check();
    await page.getByRole('button', { name: 'Start measurement' }).click();
    await page.getByText('Exact-page score').waitFor();
    await page.getByRole('heading', { name: 'Provenance and limits' }).waitFor();
    await page.getByText('Controlled simulation. The page, retrieval packets, answers, and score are test data; they do not measure public product performance.', { exact: true }).waitFor();
    assert.equal(await page.getByText(/synthetic evidence$/).count(), 16);
    assert.equal(await page.locator('.result').count(), 15);
    await page.getByRole('heading', { name: 'Draft recommendation hypotheses' }).waitFor();
    await page.getByRole('heading', { name: 'Clarify the evidence summary' }).waitFor();
    await page.getByRole('button', { name: 'Save recommendation review' }).click();
    await page.getByText('Choose a decision for rec-1. No automatic retry.', { exact: true }).waitFor();
    await page.getByLabel('Accept for planning').check();
    await page.getByRole('button', { name: 'Save recommendation review' }).click();
    await page.getByText('Review saved. Accepted tasks are planning inputs only; no editing or publishing permission was granted.', { exact: true }).waitFor();
    assert.equal(await page.locator('#run-detail img').count(), 0);
    await page.locator('.result details summary').first().click();
    await page.getByRole('button', { name: 'Inspect saved evidence' }).first().click();
    await page.getByRole('dialog').waitFor();
    assert.equal(await page.locator('#evidence-dialog img').count(), 0);
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await page.getByRole('button', { name: 'Create ZIP export' }).click();
    await page.getByRole('button', { name: 'Download ZIP' }).waitFor();
    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download ZIP' }).click();
    const download = await downloadEvent;
    assert.match(download.suggestedFilename(), /^geo-run-ui\.zip$/);
    await page.screenshot({ path: path.resolve(__dirname, '../.data/measurement-desktop.png'), fullPage: true });

    await page.reload();
    await page.getByLabel('Local access token', { exact: true }).fill('dummy-browser-only');
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByRole('button', { name: 'Download ZIP' }).waitFor();
    await page.getByLabel('Color theme').selectOption('dark');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.resolve(__dirname, '../.data/measurement-mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    assert.equal(externalCalls, 0);
    console.log('PASS: v2 create, prepare, approve, measure, review, evidence, export, recovery, dark theme, desktop/mobile; no external calls.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });