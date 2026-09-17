const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const { default: AxeBuilder } = require('../.data/browser-check/node_modules/@axe-core/playwright');
const { chromium } = require('../.data/browser-check/node_modules/playwright');

const baseUrl = process.env.GEO_BROWSER_BASE_URL || 'http://127.0.0.1:8091';

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
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
    let progressReads = 0;
    let fullReads = 0;
    let progressError = 0;
    let progressOperations = [];
    let cancellations = 0;
    let assessmentReads = 0;
    let strategyReads = 0;
    let strategyError = false;
    let strategyPartial = false;
    const inferredBrand = {run_id: 'run-ui', definition_version: 1, definition_hash: 'e'.repeat(64), source: 'page-analysis', definition: {name: 'Microsoft Clarity', aliases: [{text: 'Clarity', ambiguous: true}], domains: ['clarity.microsoft.com']}};
    let brandRecord = null;
    const ratio = (numerator, denominator) => ({numerator, denominator, rate: denominator ? numerator / denominator : null});
    const assessment = () => {
      if (!run.measurement) return null;
      const sources = Array.from({length: 5}, (_, index) => ({...source, evidence_id: `source-${index + 1}`, returned_position: index + 1,
        title: index < 2 ? `Microsoft Clarity source ${index + 1}` : source.title,
        brand: {status: index < 2 ? 'matched' : index === 2 ? 'ambiguous' : 'absent', matches: []}, brand_owned_host: false, target_relation: 'other-page'}));
      return {run_id: run.run_id, run_revision: run.revision, definition_version: brandRecord.definition_version, measurement_hash: 'a'.repeat(64),
        grounding: {brand_presence: ratio(5, 5), coverage: ratio(5, 5), ambiguous_packets: 0},
        answer: {brand_presence: ratio(15, 15), coverage: ratio(15, 15), source_citation_rate: ratio(30, 75), brand_sources_cited: ratio(15, 30), ambiguous_answers: 0},
        queries: queries.map(query => ({...query, sources, status: 'completed', brand_status: 'matched'})),
        answers: queries.flatMap(query => profiles.map(profile => ({query_id: query.query_id, profile_id: profile.profile_id, status: 'completed',
          answer: 'Microsoft Clarity appears in this saved answer.', brand: {status: 'matched', matches: []},
          source_citation_rate: ratio(2, 5), exact_page_cited: false, valid_citation_ids: ['source-1', 'source-3'], unsupported_citation_ids: ['unsupported-source'],
          brand_source_count: 2, brand_cited_count: 1, sources: sources.map((source, index) => ({evidence_id: source.evidence_id, cited: index === 0 || index === 2, shared_wording: []}))}))),
        limitations: ['Model-reported citations do not prove claim support. Uncited evidence may still influence an answer.']};
    };
    const html = await fs.readFile(path.resolve(__dirname, '../src/geo_agent/measurement.html'), 'utf8');

    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === '/measurements') return route.fulfill({ contentType: 'text/html', body: html });
      if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
      if (!url.pathname.startsWith('/api/v2/')) { externalCalls++; throw new Error(`Unexpected external request: ${url.href}`); }
      if (request.headers().authorization === 'Bearer wrong-server-token') return route.fulfill({ status: 401, json: { detail: 'Invalid bearer token' } });
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
        brandRecord = structuredClone(inferredBrand);
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
        const body = request.postDataJSON(); assert.equal(body.confirm_evaluation_calls, true); assert.equal(body.include_recommendations, false);
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
        assert.deepEqual(request.postDataJSON(), { expected_revision: run.revision, definition_version: brandRecord.definition_version, definition_hash: brandRecord.definition_hash });
        run = { ...run, revision: run.revision + 1, state: 'exported', events: events('exported') };
        artifact = { artifact_id: 'artifact-ui', run_id: 'run-ui', content_hash: 'b'.repeat(64), media_type: 'application/zip', size: 2048, created_at: '2026-09-15T10:05:00Z' };
        return route.fulfill({ status: 201, json: { run, artifact, download_url: '/api/v2/runs/run-ui/artifacts/artifact-ui' } });
      }
      if (url.pathname === '/api/v2/runs/run-ui/evidence-assessment') {
        assessmentReads++;
        return route.fulfill({json: {run_id: run.run_id, run_revision: run.revision, status: run.measurement ? 'ready' : 'pending-saved-measurement', brand_definition: brandRecord, assessment: assessment()}});
      }
      if (url.pathname === '/api/v2/runs/run-ui/content-strategy') {
        strategyReads++;
        if (strategyError) return route.fulfill({status: 503, json: {detail: 'Saved recommendations temporarily unavailable.'}});
        return route.fulfill({json: {run_id: run.run_id, run_revision: run.revision, status: 'ready', report: {
          schema_version: 'geo-content-strategy/v1', measurement_hash: 'a'.repeat(64), target_url: brief.url,
          target_title: 'Original page', target_excerpt: 'How to configure <script>untrusted</script>.', provenance: 'synthetic',
          retrieved_queries: strategyPartial ? 2 : 5, query_count: 5, retrieved_sources: 25, completed_answers: strategyPartial ? 0 : 15, expected_answers: 15, unsupported_citations: 1,
          patterns: strategyPartial ? [] : [{pattern_id: 'instructions', label: 'How-to and implementation', query_count: 1,
            citation_opportunities: 3, cited_appearances: 1, target_evidence: {evidence_id: 'page-1', quote: 'How to configure.'},
            sources: [{query_id: 'q-1', evidence_id: 'shared-source', url: 'https://example.com/guide', quote: 'How to configure <script>untrusted</script>.', returned_position: 1, target_relation: 'same-domain-other-page', cited_by: ['chatgpt-style']}],
            answers: [{query_id: 'q-1', profile_id: 'chatgpt-style', quote: 'How to configure the product.'}],
            grounding_change: 'Refine the existing material into a task-focused how-to.', answer_change: 'Make the how-to self-contained with verifiable steps.'}],
          limitations: ['Citations do not prove claim support.'], verification: 'Verify facts and test one original change using the same approved queries.',
        }}});
      }
      if (url.pathname === '/api/v2/runs/run-ui/content-strategy/download') return route.fulfill({contentType: 'application/zip', body: Buffer.from('strategy zip')});
      if (url.pathname === '/api/v2/runs/run-ui/brand-definition') {
        assert.equal(request.postDataJSON().expected_definition_version, brandRecord.definition_version);
        brandRecord = {...brandRecord, source: 'manual', definition_version: brandRecord.definition_version + 1, definition: request.postDataJSON().definition};
        return route.fulfill({json: brandRecord});
      }
      if (url.pathname === '/api/v2/runs/run-ui/evidence-assessment/download') return route.fulfill({contentType: 'application/zip', body: Buffer.from('assessment zip')});
      if (url.pathname === '/api/v2/runs/run-ui/jobs') return route.fulfill({ json: job ? [job] : [] });
      if (url.pathname === '/api/v2/jobs/slow-prepare/cancel') {
        cancellations++;
        run = { ...run, revision: run.revision + 1, state: 'cancelled' };
        job = { ...job, state: 'cancelled' };
        return route.abort('failed');
      }
      if (url.pathname === '/api/v2/runs/run-ui/progress') {
        progressReads++;
        if (progressError) return route.fulfill({ status: progressError, json: { detail: progressError === 404 ? 'Not Found' : 'Status temporarily unavailable' } });
        return route.fulfill({ json: {
        run_id: run.run_id, run_revision: run.revision, run_state: run.state,
        job_id: job?.job_id, job_type: job?.job_type, job_state: job?.state,
        last_activity_at: '2026-09-16T10:00:00Z', operations: progressOperations,
      } }); }
      if (url.pathname === '/api/v2/runs/run-ui/artifacts' && request.method() === 'GET') return route.fulfill({ json: artifact ? [artifact] : [] });
      if (url.pathname === '/api/v2/runs/run-ui/artifacts/artifact-ui') return route.fulfill({ status: 200, contentType: 'application/zip', body: Buffer.from('synthetic zip') });
      if (url.pathname === '/api/v2/runs/run-ui/evidence/q-1-target') return route.fulfill({ json: source });
      if (url.pathname === '/api/v2/runs/run-ui' && request.method() === 'GET') { fullReads++; return route.fulfill({ json: run }); }
      throw new Error(`Unexpected request: ${request.method()} ${url.pathname}`);
    });

    await page.goto(`${baseUrl}/measurements`);
    await page.getByLabel('Local access token', { exact: true }).fill('wrong-server-token');
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByText('Access token not accepted by this server. Select its local API token file to reconnect.', { exact: true }).waitFor();
    assert.equal(await page.locator('#connection').isVisible(), true);
    assert.equal(briefSubmissions, 0);
    await page.getByLabel('Local API token file', { exact: true }).setInputFiles({ name: 'local-api-token', mimeType: 'text/plain', buffer: Buffer.from('dummy-browser-only\n') });
    await page.getByRole('heading', { name: 'New measurement' }).waitFor();
    await page.getByText('Allowed host: example.com', { exact: true }).waitFor();
    assert.equal(await page.locator('#brief-form input').count(), 3);
    assert.equal(await page.locator('#brief-form input[type=checkbox]').count(), 0);
    await page.screenshot({path: path.resolve(__dirname, '../.data/measurement-new-brief.png'), fullPage: true});
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
    await page.locator('.saved-brand-context:visible').getByText(/Inferred from page \+ model knowledge/).waitFor();
    await page.screenshot({path: path.resolve(__dirname, '../.data/measurement-inferred-brand.png'), fullPage: true});
    assert.equal(await page.locator('.query').count(), 5);
    await page.getByRole('button', { name: 'Edit query packet' }).click();
    await page.locator('[data-query-id="q-1"] [data-field="chat_query"]').fill('Edited buyer question 1?');
    await page.getByRole('button', { name: 'Save query packet' }).click();
    await page.getByText('Edited buyer question 1?', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Approve exact queries' }).click();
    await page.locator('#evaluate-confirm').check();
    assert.equal(await page.locator('#include-recommendations').count(), 0);
    await page.getByRole('button', { name: 'Start measurement' }).click();
    await page.getByRole('tab', {name: 'Grounding recommendations', exact: true}).waitFor();
    assert.equal(await page.locator('.progress li').count(), 6);
    assert.equal(await page.locator('#stage-4').getAttribute('aria-pressed'), 'true');
    await page.getByText('Refine the existing material into a task-focused how-to.', {exact: true}).waitFor();
    await page.getByText('Compare page, sources and answer evidence', {exact: true}).click();
    assert.equal(await page.locator('#strategy-view script').count(), 0);
    assert.ok((await page.locator('#strategy-view').innerText()).includes('same-domain-other-page'));
    const strategyReadsBeforeTabs = strategyReads;
    await page.getByRole('tab', {name: 'Grounding recommendations', exact: true}).press('ArrowRight');
    assert.equal(await page.getByRole('tab', {name: 'LLM recommendations', exact: true}).getAttribute('aria-selected'), 'true');
    await page.getByText('Make the how-to self-contained with verifiable steps.', {exact: true}).waitFor();
    await page.getByText('Compare page, sources and answer evidence', {exact: true}).click();
    await page.getByText('Final answer / q-1 / chatgpt-style', {exact: true}).waitFor();
    assert.equal(strategyReads, strategyReadsBeforeTabs);
    const strategyDownloadEvent = page.waitForEvent('download');
    await page.locator('#download-strategy-detail').click();
    assert.equal((await strategyDownloadEvent).suggestedFilename(), 'geo-content-strategy-run-ui.zip');
    await page.locator('#assessment-tab-grounding').waitFor({state: 'attached'});
    assert.equal(await page.getByText(/synthetic evidence$/).count(), 1);
    assert.equal(await page.locator('.result').count(), 0);
    await page.getByRole('heading', { name: 'Draft recommendation hypotheses' }).waitFor();
    await page.getByRole('heading', { name: 'Clarify the evidence summary' }).waitFor();
    await page.getByRole('button', { name: 'Save recommendation review' }).click();
    await page.getByText('Choose a decision for rec-1. No automatic retry.', { exact: true }).waitFor();
    await page.getByLabel('Accept for planning').check();
    await page.getByRole('button', { name: 'Save recommendation review' }).click();
    await page.getByText('Review saved. Accepted tasks are planning inputs only; no editing or publishing permission was granted.', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('Accept for planning').isDisabled(), true);
    assert.equal(await page.locator('#run-detail img').count(), 0);
    await page.getByRole('button', {name: 'Continue to export', exact: true}).click();
    await page.getByText('Exact-page score').waitFor();
    await page.getByRole('heading', { name: 'Provenance and limits' }).waitFor();
    await page.getByText('Controlled simulation. The page, retrieval packets, answers, and score are test data; they do not measure public product performance.', { exact: true }).waitFor();
    await page.getByRole('button', {name: 'Review measurement', exact: true}).click();
    await page.getByRole('tab', {name: 'Grounding evidence', exact: true}).waitFor();
    assert.equal(await page.locator('#assessment-view .assessment-source').count(), 5);
    await page.getByRole('tab', {name: 'Source trace', exact: true}).click();
    await page.getByText('1/2 brand-bearing sources cited. Uncited does not mean unused; citation is not proof of claim support.', {exact: true}).waitFor();
    assert.equal(await page.locator('#assessment-view').getByText('Model-reported citation', {exact: true}).count(), 2);
    assert.equal(await page.locator('#assessment-view').getByText('Not cited', {exact: true}).count(), 3);
    await page.getByRole('tab', {name: 'Answer and citations', exact: true}).click();
    await page.getByText('Microsoft Clarity appears in this saved answer.', {exact: true}).waitFor();
    await page.getByRole('tab', {name: 'Source trace', exact: true}).click();
    await page.getByRole('button', { name: 'Inspect saved evidence' }).first().click();
    await page.getByRole('dialog').waitFor();
    assert.equal(await page.locator('#evidence-dialog img').count(), 0);
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: 'Inspect saved evidence' }).first().evaluate(node => node === document.activeElement), true);
    await page.getByRole('button', {name: 'Edit brand definition', exact: true}).click();
    await page.getByRole('button', {name: 'Save brand definition', exact: true}).click();
    await page.getByText('Brand definition saved. No measurement calls were made.', {exact: true}).waitFor();
    await page.getByRole('button', {name: 'Export', exact: true}).click();
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
    assert.equal(await page.locator('#download-artifact svg[aria-hidden="true"]').count(), 1);
    assert.equal(await page.locator('#refresh-runs').getAttribute('title'), 'Refresh');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({ path: path.resolve(__dirname, '../.data/measurement-mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    assert.equal(externalCalls, 0);
    for (const theme of ['light', 'dark']) {
      await page.getByLabel('Color theme').selectOption(theme);
      for (const width of [1440, 768, 390, 320]) {
        await page.setViewportSize({ width, height: 900 });
        await page.getByRole('button', {name: 'Measure', exact: true}).click();
        await page.getByRole('tab', {name: 'Source trace', exact: true}).click();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `Overflow at ${width}/${theme}`);
        const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
        assert.deepEqual(audit.violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => node.target) })), [], `${width}/${theme} accessibility`);
        await page.screenshot({ path: path.resolve(__dirname, `../.data/measurement-${theme}-${width}.png`), fullPage: true });
        await page.getByRole('button', {name: 'Recommendations', exact: true}).click();
        await page.getByRole('tab', {name: 'LLM recommendations', exact: true}).click();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `Recommendations overflow at ${width}/${theme}`);
        assert.equal(await page.locator('#stage-4').evaluate(button => button.scrollWidth > button.clientWidth), false, `Recommendation label overflow at ${width}/${theme}`);
        const strategyAudit = await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
        assert.deepEqual(strategyAudit.violations.map(item => item.id), [], `Recommendations accessibility ${width}/${theme}`);
        await page.screenshot({path: path.resolve(__dirname, `../.data/recommendations-${theme}-${width}.png`), fullPage: true});
      }
    }
    console.log('PASS: workflow, light/dark accessibility and responsive layouts.');
    const exportedRun = run;
    strategyError = true;
    run = {...run, revision: run.revision + 1, state: 'partial', recommendations: null, recommendation_review: null};
    await page.setViewportSize({width: 1280, height: 900});
    await page.getByRole('button', {name: 'Refresh', exact: true}).click();
    await page.locator('#strategy-detail').getByText('Saved recommendations temporarily unavailable.', {exact: true}).waitFor();
    assert.equal(await page.locator('#strategy-detail .recommendation').count(), 0);
    const readsBeforeReload = strategyReads;
    strategyError = false; strategyPartial = true;
    await page.getByRole('button', {name: 'Reload recommendations', exact: true}).click();
    await page.getByText('No completed LLM answers. Answer-based recommendations are unavailable.', {exact: true}).waitFor();
    assert.equal(strategyReads, readsBeforeReload + 1);
    await page.getByRole('tab', {name: 'Grounding recommendations', exact: true}).click();
    await page.getByText('No matching content cues in the available evidence for this layer. This does not establish that the content is absent.', {exact: true}).waitFor();
    await page.getByRole('button', {name: 'Continue to export', exact: true}).click();
    assert.equal(await page.getByRole('button', {name: 'Create ZIP export', exact: true}).isEnabled(), true);
    assert.equal(briefSubmissions, 1);
    strategyPartial = false;
    const savedBrand = brandRecord;
    brandRecord = null;
    run = {...run, revision: run.revision + 1, state: 'awaiting-query-approval', measurement: null, approval: null, scores: null, recommendations: null, recommendation_review: null};
    await page.reload();
    await page.getByLabel('Local access token', {exact: true}).fill('dummy-browser-only');
    await page.getByRole('button', {name: 'Connect', exact: true}).click();
    await page.locator('.saved-brand-context:visible').getByText('Brand not identified. Brand metrics remain N/A.', {exact: true}).waitFor();
    assert.equal(await page.getByRole('button', {name: 'Approve exact queries', exact: true}).isEnabled(), true);
    assert.equal(await page.getByRole('button', {name: 'Set brand', exact: true}).isVisible(), true);
    assert.deepEqual(errors, []);
    brandRecord = savedBrand;
    run = {...run, revision: run.revision + 1, state: 'needs-review', inputs: null};
    job = {job_id: 'failed-prepare', run_id: run.run_id, job_type: 'prepare', state: 'failed', error_code: 'ProviderError'};
    progressOperations = [
      {operation_type: 'webiq-browse', planned: 1, completed: 1, failed: 0},
      {operation_type: 'page-analysis-model', planned: 1, completed: 1, failed: 0},
      {operation_type: 'paired-query-plan', planned: 1, completed: 0, failed: 1},
    ];
    await page.reload();
    await page.getByLabel('Local access token', {exact: true}).fill('dummy-browser-only');
    await page.getByRole('button', {name: 'Connect', exact: true}).click();
    for (const [code, reason] of [
      ['ProviderError', 'The detailed reason was not retained for this run.'],
      ['model-output-limit', 'The model reached the 2,000-token output limit'],
      ['query-plan-evidence-invalid', 'evidence that could not be verified'],
      ['provider-rate-limited', 'HTTP 429 (rate limit or quota)'],
      ['private-provider-response', 'The detailed failure reason is unavailable.'],
    ]) {
      job = {...job, error_code: code}; run = {...run, revision: run.revision + 1};
      await page.getByRole('button', {name: 'Refresh status', exact: true}).click();
      await page.locator('#panel-1 .stage-notice').filter({hasText: reason}).waitFor();
      const notice = await page.locator('#panel-1 .stage-notice').innerText();
      assert.ok(notice.startsWith('Query packet failed.'), notice);
      assert.ok(notice.includes(reason), notice);
      assert.ok(!notice.includes('interrupted'), notice);
      await page.getByText('Recorded operations', {exact: true}).click();
      assert.ok((await page.locator('#operation-progress').innerText()).includes(reason));
      assert.equal(await page.locator('#prepare-run').count(), 0);
      assert.ok(!(await page.locator('body').innerText()).includes('private-provider-response'));
    }
    for (const theme of ['light', 'dark']) {
      await page.getByLabel('Color theme').selectOption(theme);
      for (const width of [1280, 320]) {
        await page.setViewportSize({width, height: 900});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await page.screenshot({path: path.resolve(__dirname, `../.data/query-packet-failure-${theme}-${width}.png`), fullPage: true});
      }
    }
    assert.deepEqual(errors, []);
    assert.equal(externalCalls, 0);
    console.log('PASS: safe query-packet failure reasons, legacy fallback and no retry controls.');
    progressOperations = [];
    run = exportedRun;
    if (process.env.GEO_BROWSER_FOCUSED === '1') return;
    run = { ...run, revision: 20, state: 'preparing', measurement: null, scores: null, recommendations: null, recommendation_review: null, approval: null, inputs: null };
    job = { job_id: 'slow-prepare', run_id: run.run_id, job_type: 'prepare', state: 'queued' };
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.reload();
    await page.getByLabel('Local access token', { exact: true }).fill('dummy-browser-only');
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByText('Queued for durable worker', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Cancel job' }).isEnabled(), true);
    const stablePanel = await page.locator('#run-detail').evaluate(node => { window.savedPanelChild = node.firstChild; return true; });
    assert.equal(stablePanel, true);
    job = { ...job, state: 'leased' };
    await page.getByText('Durable worker job in progress', { exact: true }).waitFor({ timeout: 5000 });
    assert.equal(await page.locator('#run-detail').evaluate(node => node.firstChild === window.savedPanelChild), true);
    const readsBefore = fullReads;
    const assessmentsBefore = assessmentReads;
    const strategiesBefore = strategyReads;
    const pollsBefore = progressReads;
    const monitoringStarted = Date.now();
    for (let tick = 0; tick < 65; tick++) {
      job = { ...job, state: tick % 2 ? 'leased' : 'queued' };
      await page.getByText(tick % 2 ? 'Durable worker job in progress' : 'Queued for durable worker', { exact: true }).waitFor();
      if (tick % 10 === 0) console.log(`Monitoring tick ${tick + 1}/65`);
    }
    assert.ok(progressReads - pollsBefore >= 65, 'Monitoring must continue beyond forty ticks.');
    assert.ok(Date.now() - monitoringStarted >= 60000, 'Monitoring must continue beyond sixty seconds.');
    assert.equal(fullReads, readsBefore, 'Unchanged run revision must not refetch the full run.');
    assert.equal(assessmentReads, assessmentsBefore, 'Unchanged progress must not fetch assessments.');
    assert.equal(strategyReads, strategiesBefore, 'Unchanged progress must not fetch recommendations.');
    assert.equal(await page.locator('#run-detail').evaluate(node => node.firstChild === window.savedPanelChild), true);
    progressError = 503;
    await page.getByText(/Status unavailable\. Your job may still be running/).waitFor();
    progressError = 0;
    await page.getByText('Connection restored. Following your saved run.', { exact: true }).waitFor();
    run = { ...run, revision: 21, state: 'awaiting-query-approval', inputs, approval_hash: 'd'.repeat(64) };
    job = { ...job, state: 'completed' };
    await page.getByRole('button', { name: 'Approve exact queries' }).waitFor({ timeout: 5000 });
    run = { ...run, revision: 22, state: 'preparing', inputs: null };
    job = { ...job, state: 'queued' };
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await page.getByRole('button', { name: 'Cancel job' }).click();
    await page.locator('#run-detail .state').getByText('cancelled', { exact: true }).waitFor();
    await page.getByText(/Saved state refreshed\. Review it before submitting again/).waitFor();
    assert.equal(cancellations, 1);
    assert.equal(await page.locator('#export-run').count(), 0);
    assert.equal(await page.locator('#stage-1').getAttribute('aria-pressed'), 'true');
    for (const state of ['needs-review', 'failed']) {
      run = { ...run, revision: run.revision + 1, state };
      await page.getByRole('button', { name: 'Refresh status', exact: true }).click();
      assert.equal(await page.locator('#export-run').count(), 0);
      assert.equal(await page.locator('#stage-1').getAttribute('aria-pressed'), 'true');
    }
    progressError = 404;
    run = { ...run, revision: 25, state: 'preparing', inputs: null };
    job = { ...job, state: 'queued' };
    await page.getByRole('button', { name: 'Refresh status', exact: true }).click();
    await page.getByText('Queued for durable worker', { exact: true }).waitFor();
    await page.getByText(/Detailed operation progress becomes available after the API is restarted/).waitFor();
    job = { ...job, state: 'leased' };
    await page.getByText('Durable worker job in progress', { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    assert.equal(externalCalls, 0);
    console.log('PASS: v2 create, prepare, approve, measure, review, evidence, export, recovery, dark theme, desktop/mobile; no external calls.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });