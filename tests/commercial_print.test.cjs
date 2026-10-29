// Offline tests of the printable Comercial report helpers (filters header and series table).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const helpers = source.slice(source.indexOf('function appliedFiltersText(data)'), source.indexOf('function chartsToImages(panel)'));

function setup(printable = {}) {
  const context = vm.createContext({
    state: {
      filters: {
        available: {
          family: { label: 'Familia', options: [{ value: 'Aguas', label: 'Aguas' }, { value: 'Gaseosas', label: 'Gaseosas' }] },
          month: { label: 'Mes', options: [{ value: 1, label: 'Enero' }, { value: 2, label: 'Febrero' }] },
          brand: { label: 'Marca', options: Array.from({ length: 12 }, (_, i) => ({ value: `M${i}`, label: `M${i}` })) },
        },
      },
      interactiveReport: { printable },
    },
    isSameFilterValue: (_, left, right) => String(left) === String(right),
    escapeHtml: text => String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;'),
    money: v => `$${v}`, intNumber: v => `${Math.round(v)}`, decimalNumber: v => `${v}`,
    formatMonthAxisLabel: label => `m:${label}`,
  });
  vm.runInContext(helpers, context);
  return context;
}

test('filters header lists only restricted fields with option labels', () => {
  const context = setup();
  const text = context.appliedFiltersText({ appliedFilters: { family: ['Aguas', 'Gaseosas'], month: [2], brand: ['M1'] } });
  assert.equal(text, 'Mes: Febrero · Marca: M1');
});

test('filters header shortens long selections and reports no filters', () => {
  const context = setup();
  const brands = Array.from({ length: 10 }, (_, i) => `M${i}`);
  assert.match(context.appliedFiltersText({ appliedFilters: { brand: brands } }), /M7 y 2 más$/);
  assert.match(context.appliedFiltersText({ appliedFilters: {} }), /^Sin filtros/);
  assert.match(context.appliedFiltersText(null), /^Sin filtros/);
});

test('series table has one column per month plus total', () => {
  const context = setup({
    labels: ['2026-09', '2026-10'],
    series: [{ name: 'Aguas', data: [10, 5] }, { name: '<Gas>', data: [1, null] }],
    metric: { key: 'quantity', label: 'Bultos', format: 'decimal' },
    dimension: { key: 'family', label: 'Familia' },
  });
  const html = context.printableSeriesTable();
  assert.match(html, /<h3>Bultos por Familia<\/h3>/);
  assert.match(html, /<th>m:2026-09<\/th><th>m:2026-10<\/th><th>Total<\/th>/);
  assert.match(html, /<td>Aguas<\/td><td>10<\/td><td>5<\/td><td><strong>15<\/strong><\/td>/);
  assert.match(html, /<td>&lt;Gas><\/td><td>1<\/td><td>0<\/td><td><strong>1<\/strong><\/td>/);
});

test('buyers table uses the period total from the backend and explains it', () => {
  const context = setup({
    labels: ['2026-09', '2026-10'],
    series: [{ name: 'Total', total: 7, data: [5, 4] }],
    metric: { key: 'buyers', label: 'Clientes compradores', format: 'count' },
    dimension: { key: 'total', label: 'Total' },
  });
  const html = context.printableSeriesTable();
  assert.match(html, /<strong>7<\/strong>/);
  assert.match(html, /no es la suma de los meses/);
});

test('no table without rendered series', () => {
  assert.equal(setup().printableSeriesTable(), '');
});
