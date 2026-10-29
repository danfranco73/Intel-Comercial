// Offline behavior tests of production dispatcher, series, fallback and legacy functions.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const analysis = source.slice(source.indexOf('let analysisInFlight = false;'),
  source.indexOf('function buildSalesCoachHomeFromReport'));
const series = source.slice(source.indexOf('async function analyzeSeries()'), source.indexOf('function renderBiResults()'));

function setup(api) {
  const button = { disabled: false };
  const status = { textContent: '' };
  const rendered = [];
  const biRendered = [];
  const results = { classList: { add: () => {} } };
  const context = vm.createContext({
    state: { datasets: { sales: { sourceMode: 'mongo', erp: { fechaDesde: '2026-10-01', fechaHasta: '2026-10-29' } } },
      coachComparison: {}, planning: {}, planningDirty: true, series: {}, reportView: {} },
    workspace: 'coach', BI_FULL_REPORT_MAX_DAYS: 93,
    document: { getElementById: id => id === 'analyzeBtn' ? button : id === 'results' ? results : status },
    datasetOrder: ['sales'], isBiSurface: true,
    serializeFilters: () => ({}), normalizeSupplierFocusSelection: () => '',
    normalizePlanningConfig: x => x, setStatus: s => { status.textContent = s; },
    withProgress: async (_, run) => run(), api, rangeDays: () => 29,
    buildSalesCoachHomeFromReport: () => ({}), renderResults: r => rendered.push(r),
    saveCachedWorkspace: async () => {}, openRequestedSalesCoachDetail: async () => {},
    seriesRequestPayload: () => ({ fechaDesde: context.state.datasets.sales.erp.fechaDesde, fechaHasta: context.state.datasets.sales.erp.fechaHasta }),
    renderBiReportData: () => biRendered.push('detail'), renderBiResults: () => biRendered.push('results'),
    showError: () => {},
  });
  vm.runInContext(series + '\n' + analysis, context);
  return { context, button, status, rendered, biRendered };
}
function report() {
  return { meta: { rowsAnalyzed: 3, periodStart: '2026-10-01', periodEnd: '2026-10-29' } };
}

test('repeated clicks issue one sequential report/tactical pair and permit later refresh', async () => {
  let resume;
  const gate = new Promise(resolve => { resume = resolve; });
  const calls = [];
  const env = setup(async url => {
    calls.push(url);
    if (url === '/api/analyze') { await gate; return report(); }
    return { tacticalMonth: { ok: true } };
  });
  const first = env.context.analyze();
  assert.equal(env.button.disabled, true);
  await env.context.analyze();
  await env.context.analyze();
  assert.deepEqual(calls, ['/api/analyze']);
  resume();
  await first;
  assert.deepEqual(calls, ['/api/analyze', '/api/bi/tactical-month']);
  assert.equal(env.button.disabled, false);
  assert.equal(env.rendered[0].tacticalMonth.ok, true);
  await env.context.analyze();
  assert.equal(calls.length, 4);
});

test('tactical failure renders principal report with explicit warning', async () => {
  const env = setup(async url => {
    if (url === '/api/analyze') return report();
    throw new Error('503');
  });
  await env.context.analyze();
  assert.equal(env.rendered.length, 1);
  assert.match(env.status.textContent, /no se pudo completar el análisis táctico/);
  assert.doesNotMatch(env.status.textContent, /Análisis completo/);
  assert.equal(env.button.disabled, false);
});

test('principal failure releases guard and button for retry', async () => {
  let fail = true;
  const env = setup(async url => {
    if (fail) throw new Error('503');
    return url === '/api/analyze' ? report() : { tacticalMonth: {} };
  });
  await assert.rejects(env.context.analyze(), /503/);
  assert.equal(env.rendered.length, 0);
  assert.equal(env.button.disabled, false);
  fail = false;
  await env.context.analyze();
  assert.equal(env.rendered.length, 1);
});

test('validation return releases guard', async () => {
  const env = setup(async () => { throw new Error('must not call API'); });
  env.context.state.datasets.sales.erp.fechaDesde = '';
  await env.context.analyze();
  assert.equal(env.button.disabled, false);
  assert.match(env.status.textContent, /Elegí fecha/);
});

test('guard stays active throughout tactical request', async () => {
  let resume, entered;
  const gate = new Promise(resolve => { resume = resolve; });
  const tacticalStarted = new Promise(resolve => { entered = resolve; });
  const calls = [];
  const env = setup(async url => {
    calls.push(url);
    if (url === '/api/analyze') return report();
    entered();
    await gate;
    return { tacticalMonth: {} };
  });
  const first = env.context.analyze();
  await tacticalStarted;
  await env.context.analyze();
  assert.equal(env.button.disabled, true);
  assert.equal(calls.length, 2);
  resume();
  await first;
  assert.equal(env.button.disabled, false);
});

test('Comercial preserves aggregate return value and blocks repeated clicks', async () => {
  let resume;
  const gate = new Promise(resolve => { resume = resolve; });
  const calls = [];
  const result = { summary: { periodLabel: 'Octubre' } };
  const env = setup(async url => { calls.push(url); await gate; return result; });
  env.context.workspace = 'bi';
  const first = env.context.analyze();
  await env.context.analyze();
  assert.deepEqual(calls, ['/api/bi/series']);
  assert.equal(env.button.disabled, true);
  resume();
  assert.equal(await first, result);
  assert.equal(env.context.state.series.data, result);
  assert.equal(env.rendered.length, 0);
  assert.deepEqual(env.biRendered, ['results']);
  assert.equal(env.button.disabled, false);
});

for (const tacticalFails of [false, true]) {
  test(`Comercial fallback preserves return, key and duplicate guard; tactical failure=${tacticalFails}`, async () => {
    let resume, entered;
    const gate = new Promise(resolve => { resume = resolve; });
    const started = new Promise(resolve => { entered = resolve; });
    const calls = [];
    const result = report();
    const env = setup(async url => {
      calls.push(url);
      if (url === '/api/bi/series') throw { payload: { fallback: 'legacy' } };
      if (url === '/api/analyze') return result;
      entered(); await gate;
      if (tacticalFails) throw new Error('503');
      return { tacticalMonth: { ok: true } };
    });
    env.context.workspace = 'bi';
    const first = env.context.analyze();
    await started;
    await env.context.analyze();
    assert.equal(env.button.disabled, true);
    assert.deepEqual(calls, ['/api/bi/series', '/api/analyze', '/api/bi/tactical-month']);
    resume();
    assert.equal(await first, result);
    assert.equal(env.context.state.series.data, result);
    assert.equal(env.context.state.reportView.lastData, result);
    assert.equal(result._seriesKey, env.context.state.series.key);
    assert.deepEqual(env.biRendered, ['detail', 'results']);
    assert.equal(env.rendered.length, 0);
    assert.equal(env.button.disabled, false);
    if (tacticalFails) {
      assert.match(env.status.textContent, /no se pudo completar el análisis táctico/);
      assert.doesNotMatch(env.status.textContent, /Informe completo listo/);
    } else {
      assert.equal(result.tacticalMonth.ok, true);
      assert.match(env.status.textContent, /Informe completo listo/);
    }
  });
}

test('Comercial non-fallback failure propagates and permits retry', async () => {
  let fail = true;
  const calls = [];
  const env = setup(async url => { calls.push(url); if (fail) throw new Error('503'); return report(); });
  env.context.workspace = 'bi';
  await assert.rejects(env.context.analyze(), /503/);
  assert.equal(env.button.disabled, false);
  fail = false;
  await env.context.analyze();
  assert.deepEqual(calls, ['/api/bi/series', '/api/bi/series']);
});

test('Comercial fallback retains date limit and null return without loading detail', async () => {
  const calls = [];
  const env = setup(async url => { calls.push(url); throw { payload: { fallback: 'legacy' } }; });
  env.context.workspace = 'bi';
  env.context.rangeDays = () => 120;
  assert.equal(await env.context.analyze(), null);
  assert.deepEqual(calls, ['/api/bi/series']);
  assert.match(env.status.textContent, /admite hasta 3 meses/);
  assert.equal(env.button.disabled, false);
});

test('Comercial missing date returns null without API calls', async () => {
  const env = setup(async () => { throw new Error('must not call API'); });
  env.context.workspace = 'bi';
  env.context.state.datasets.sales.erp.fechaDesde = '';
  assert.equal(await env.context.analyze(), null);
  assert.equal(env.button.disabled, false);
});
