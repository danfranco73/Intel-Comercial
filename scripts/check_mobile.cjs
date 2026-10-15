/* Responsive UI smoke test; all HTTP requests are intercepted with anonymous fixtures.
 * Install Playwright separately, then run with NODE_PATH pointing to its node_modules:
 * NODE_PATH=/tmp/gestion-mobile-tools/node_modules node scripts/check_mobile.cjs
 * Optional: BROWSER_EXECUTABLE, TEST_PYTHON, MOBILE_SCREENSHOT_DIR.
 * Never starts app.py, loads .env, or connects to production services.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const python = process.env.TEST_PYTHON || (fs.existsSync(path.join(root, '.venv/bin/python')) ? path.join(root, '.venv/bin/python') : 'python3');
const fixtures = spawnSync(python, ['-c', `
import runpy, json
from datetime import date
c = runpy.run_path('tests/conftest.py')  # Blocks dotenv and external networking before any app import.
import analyzer
p = runpy.run_path('tests/test_pdv_360.py')
print(json.dumps({
 'schema': {'datasets': analyzer.DATASET_DEFINITIONS},
 'report': analyzer.analyze_datasets(c['datasets'].__wrapped__(c['sales_records'].__wrapped__())),
 'pdv': p['_build']([p['line'](date(2026,9,1), 'B1', 300), p['line'](date(2026,9,2), 'M1', 100)]),
 'portfolio': p['_portfolio']({'C1'}),
}, default=str))
`], { cwd: root, encoding: 'utf8' });
assert.equal(fixtures.status, 0, fixtures.stderr);
const { schema, report, pdv, portfolio } = JSON.parse(fixtures.stdout);
const screenshots = process.env.MOBILE_SCREENSHOT_DIR;
if (screenshots) fs.mkdirSync(screenshots, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  try {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, reducedMotion: 'reduce' });
    const errors = [], mutations = [], external = [];
    let role = 'admin', checked = 0;
    await context.route('**/*', async (route) => {
      const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
      if (url.hostname !== 'gestion.test') { external.push(request.url()); return route.abort(); }
      if (request.method() !== 'GET') mutations.push(`${request.method()} ${pathname}`);
      if (pathname.startsWith('/api/')) {
        const responses = {
          '/api/auth/me': { user: { name: 'Usuario de prueba con nombre largo', email: 'demo@example.test', role }, csrfToken: 'test-only' },
          '/api/datasets': schema,
          '/api/files': { files: [] },
          '/api/users': { users: [{ id: 'demo-user', name: 'Usuario de ejemplo para probar los controles de administración', email: 'demo@example.test', role: 'seller', is_active: true }] },
          '/api/access/companies': { companies: [{ company_key: 'EMPRESA_DE_PRUEBA_CON_CODIGO_LARGO', name: 'Empresa de ejemplo con nombre largo', is_active: true }] },
          '/api/access/options': Object.fromEntries(['branches', 'suppliers', 'lines', 'deposits', 'businessUnits', 'salesForces', 'sellers'].map((key) => [key, [{ key: 'CODIGO_DE_CATALOGO_LARGO', name: 'Opción de catálogo con nombre largo para probar el formulario' }]])),
          '/api/intelligence/deposits': { universe_coverage: { status: 'verified' }, deposits: [{ deposit_id: '1', deposit_name: 'Depósito de ejemplo con nombre largo para comprobar adaptación móvil', active: true, configured: true, include_in_stock_analysis: true }] },
          '/api/pdv/360': pdv,
          '/api/pdv/portfolio': portfolio,
        };
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(responses[pathname] || {}) });
      }
      const pages = { '/bi': 'index.html', '/sales-coach': 'index.html', '/sales-coach/seller': 'index.html', '/sales-coach/client': 'index.html', '/pdv': 'index.html', '/admin': 'admin.html', '/users': 'users.html', '/login': 'login.html', '/admin/intelligence': 'intelligence-admin.html' };
      const file = path.join(root, 'static', pages[pathname] || pathname.slice(1));
      if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: '' });
      return route.fulfill({ contentType: { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[path.extname(file)] || 'application/octet-stream', body: fs.readFileSync(file) });
    });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    async function fits(label) {
      // Allow ResizeObserver, ECharts and Tabulator to finish their layout work.
      await page.waitForTimeout(120);
      const size = await page.evaluate(() => ({ width: innerWidth, document: document.documentElement.scrollWidth }));
      assert.ok(size.document <= size.width + 1, `${label}: horizontal page overflow ${JSON.stringify(size)}`);
      assert.deepEqual(errors, [], `${label}: browser errors`);
      checked++;
    }
    async function shot(name, selector) {
      if (!screenshots) return;
      if (selector) await page.locator(selector).scrollIntoViewIfNeeded();
      else await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({ path: path.join(screenshots, `${name}.png`), animations: 'disabled' });
    }
    for (const route of ['/login', '/admin', '/users', '/admin/intelligence', '/bi', '/sales-coach']) {
      await page.goto(`http://gestion.test${route}`);
      await page.waitForLoadState('networkidle');
      const dashboard = ['/bi', '/sales-coach'].includes(route);
      if (dashboard) await page.evaluate((data) => renderResults(data), report);
      for (const width of [320, 390, 768, 1024, 1440]) {
        await page.setViewportSize({ width, height: 844 });
        if (dashboard) {
          for (const view of await page.evaluate(() => DASHBOARD_VIEWS)) {
            await page.locator(`[data-dashboard-view="${view}"]`).click();
            await fits(`${route}/${view}/${width}`);
          }
        } else await fits(`${route}/${width}`);
      }
      await page.setViewportSize({ width: 390, height: 844 });
      if (route === '/users') {
        await page.locator('[data-start-reset-password]').click();
        for (const width of [320, 390, 768]) {
          await page.setViewportSize({ width, height: 844 });
          await fits(`password-reset/${width}`);
          assert.ok(await page.locator('#resetPasswordInput').evaluate((el) => el.getBoundingClientRect().width >= 120), 'Password input must remain usable');
        }
        await page.setViewportSize({ width: 390, height: 844 });
        await shot('password-reset', '.reset-password-inline');
        await page.locator('[data-cancel-reset-password]').click();
      }
      await shot(route.slice(1).replaceAll('/', '-'));
      if (await page.locator('#navigationToggle').count()) {
        const toggle = page.locator('#navigationToggle');
        await toggle.click();
        assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
        assert.ok(await page.locator('#surfaceLinks').isVisible());
        await fits(`${route}/menu`);
        await shot(`${route.slice(1)}-menu`);
        await page.keyboard.press('Escape');
        assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
        assert.ok(await toggle.evaluate((el) => el === document.activeElement));
        assert.ok(!(await page.locator('#surfaceLinks').isVisible()));
        if (['/admin', '/users'].includes(route)) assert.equal(await page.locator('.surface-links a.active').getAttribute('href'), route);
      }
    }

    await page.goto('http://gestion.test/bi');
    await page.waitForLoadState('networkidle');
    await page.evaluate((data) => renderResults(data), report);
    await page.locator('#toggleParameters').click();
    assert.equal(await page.locator('#toggleParameters').getAttribute('aria-expanded'), 'false');
    await page.locator('#toggleParameters').click();
    const filter = page.locator('#resultsFilters [data-selectable-role="toggle"]').first();
    await filter.click();
    assert.ok(await page.locator('#resultsFilters .filter-box.open').isVisible());
    await fits('open-filter');
    await shot('filter', '#resultsFilters .filter-box.open');
    await page.locator('#resultsFilters .filter-box.open input[type="checkbox"]').first().check();
    await page.locator('#dashboardViewSelector').click();
    await page.locator('[data-dashboard-view="executive"]').click();
    await shot('executive-chart', '#execTrendChart');
    // Check real chart dimensions after resizing without rerendering the report.
    for (const width of [1440, 320, 844, 390]) {
      await page.setViewportSize({ width, height: width === 844 ? 390 : 844 });
      await fits(`rotation/${width}`);
      const charts = await page.evaluate(() => Object.entries(dashboardState.charts).filter(([id]) => document.getElementById(id).getBoundingClientRect().height > 0).map(([id, chart]) => ({ id, width: chart.getWidth(), actual: document.getElementById(id).clientWidth, compact: chartLayouts.get(id).compact })));
      assert.ok(charts.length > 0);
      for (const chart of charts) {
        assert.equal(chart.width, chart.actual, `${chart.id}: stale chart width`);
        assert.equal(chart.compact, chart.actual < 600, `${chart.id}: wrong chart layout`);
      }
    }
    await page.locator('[data-dashboard-view="pdv"]').click();
    await page.evaluate((data) => renderPdvPortfolio(data), portfolio);
    for (const width of [320, 390, 768, 1440]) { await page.setViewportSize({ width, height: 844 }); await fits(`pdv-portfolio/${width}`); }
    await page.evaluate((data) => { setPdvMode('single'); state.pdv.data = data; renderPdv360(data); }, pdv);
    for (const width of [320, 390, 768, 1440]) { await page.setViewportSize({ width, height: 844 }); await fits(`pdv-single/${width}`); }
    await page.setViewportSize({ width: 390, height: 844 });
    await shot('pdv', '#pdvCompanies');
    const table = page.locator('#pdvUnits');
    assert.ok(await table.evaluate((el) => { el.scrollLeft = 100; return el.scrollWidth > el.clientWidth && el.scrollLeft > 0; }), 'PDV table should scroll independently');
    await page.locator('#navigationToggle').click();
    await page.locator('#themeToggle').click();
    await page.keyboard.press('Escape');
    await fits('dark-mode');
    await shot('pdv-dark', '#pdvCompanies');
    await page.evaluate(() => document.getElementById('progressModal').classList.remove('hidden'));
    await fits('progress-modal');
    await shot('progress');
    await page.evaluate(() => document.getElementById('progressModal').classList.add('hidden'));
    await page.goto('http://gestion.test/sales-coach/seller');
    await page.waitForLoadState('networkidle');
    await page.evaluate((data) => { renderResults(data); renderSellerCoachDetail({ identification: { name: 'Vendedor de ejemplo', salesForce: 'Fuerza de ejemplo' }, kpis: { sales: 123456789.12 } }); }, report);
    await fits('seller-detail');
    await shot('seller-detail', '#salesCoachDetailPanel');
    // Role-based links remain unavailable in the mobile menu.
    role = 'seller';
    await page.goto('http://gestion.test/bi');
    await page.waitForLoadState('networkidle');
    await page.locator('#navigationToggle').click();
    assert.equal(await page.locator('.surface-links a[href="/admin"], .surface-links a[href="/users"]').count(), 0);
    await fits('seller-menu');
    assert.deepEqual(errors, []);
    // Opening Alerts automatically requests generation in the existing app.
    // That POST is fulfilled above with a fixture, just like every GET.
    assert.deepEqual(mutations.filter((request) => request !== 'POST /api/alerts/generate'), [], 'Unexpected form submission');
    assert.deepEqual(external, [], 'Smoke test must never access external services');
    console.log(`PASS: ${checked} responsive states, mobile navigation, filters, table scrolling, chart rotation, dark mode and role visibility. Every API request mocked; no external requests.`);
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
