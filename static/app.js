const state = {
  files: [],
  schema: {},
  datasets: {},
  preview: { datasetType: "sales", sourceIndex: 0 },
  scope: "uploads",
  erpStatus: { configured: false, reachable: false, message: "" },
  erpStorage: { connected: false, available: false, message: "" },
  clickhouseStorage: { connected: false, available: false, configured: false, message: "" },
  prefilters: { available: {}, selected: {} },
  filterSearch: {},
  erpSyncPending: false,
  filters: { available: {}, selected: {} },
  supplierFocus: "",
  dynamic: { tasks: [], selectedTaskId: null },
  adminErrors: [],
  adminAuth: { token: readStoredAdminToken() },
  reportView: { metricMode: readStoredMetricMode(), lastData: null },
};

const datasetOrder = ["sales"];
const filterOrder = ["year", "month", "family", "line", "brand", "business_unit", "supplier", "product_name", "sales_force", "route_description", "seller_name", "channel"];
const ERP_KEEPALIVE_MS = 4 * 60 * 1000;
const appSurface = document.body.dataset.surface || "admin";
const isAdminSurface = appSurface === "admin";
const isBiSurface = appSurface === "bi";
const REPORT_METRIC_MODES = ["mixed", "units", "sales"];
const dashboardState = { charts: {}, table: null };

const filterGroups = [
  { id: "tiempo",     label: "Período",       fields: ["year", "month"] },
  { id: "producto",   label: "Producto",      fields: ["family", "line", "brand", "business_unit", "supplier", "product_name"] },
  { id: "comercial",  label: "Comercial",     fields: ["sales_force", "route_description", "seller_name"] },
  { id: "canal",      label: "Canal",         fields: ["channel"] },
];
const prefilterGroups = [
  { id: "producto", label: "Producto", fields: ["family", "line", "brand", "business_unit"] },
  { id: "comercial", label: "Comercial", fields: ["sales_force", "route_description", "seller_name"] },
];

const ANALYSIS_PLAYBOOK = {
  temporal_trend: {
    summary: "Muestra cómo evoluciona la venta en el tiempo y dónde se acelera o desacelera.",
    when: "Usalo cuando quieras ver tendencia, estacionalidad o caída reciente.",
    questions: [
      "¿Cómo evolucionaron las ventas mes a mes?",
      "¿Qué vendedor o canal cayó más en el último período?",
      "¿La tendencia reciente mejora o empeora?",
    ],
  },
  dimension_ranking: {
    summary: "Ordena la venta por una dimensión y muestra concentración y participación.",
    when: "Sirve para ver top clientes, top vendedores, top marcas o mix de negocio.",
    questions: [
      "¿Quiénes son mis clientes más importantes?",
      "¿Qué vendedores explican la mayor parte de la venta?",
      "¿Qué marcas o líneas pesan más en el resultado?",
    ],
  },
  recurrence_churn: {
    summary: "Detecta recurrencia, pérdida de frecuencia y clientes que dejaron de comprar.",
    when: "Usalo para revisar cartera dormida o riesgo comercial.",
    questions: [
      "¿Qué clientes dejaron de comprar?",
      "¿En qué ruta o fuerza de ventas hay más pérdida de recurrencia?",
      "¿Dónde conviene activar recuperación de cartera?",
    ],
  },
  cross_sell_mix: {
    summary: "Mide profundidad de surtido y oportunidades de cross-sell por cartera.",
    when: "Sirve para ver cuántas familias compra cada cliente y dónde ampliar mix.",
    questions: [
      "¿Qué clientes compran poco mix?",
      "¿Qué vendedor tiene mejor profundidad de surtido?",
      "¿Dónde hay oportunidad de vender más familias?",
    ],
  },
  seller_performance: {
    summary: "Compara desempeño comercial por vendedor con foco en productividad.",
    when: "Usalo para revisar ticket, cartera atendida y peso relativo por vendedor.",
    questions: [
      "¿Qué vendedor produce más venta por cliente?",
      "¿Quién tiene mejor ticket promedio?",
      "¿Dónde hay desvíos entre vendedores?",
    ],
  },
  geographic_coverage: {
    summary: "Compara cobertura territorial y peso comercial por ruta.",
    when: "Sirve para detectar rutas débiles o con potencial desaprovechado.",
    questions: [
      "¿Qué rutas concentran más venta?",
      "¿Dónde hay brechas territoriales?",
      "¿Qué rutas necesitan revisión comercial?",
    ],
  },
  sales_force_breakdown: {
    summary: "Abre la venta por fuerza comercial para ver concentración y diferencias internas.",
    when: "Usalo si querés comparar equipos o estructuras comerciales.",
    questions: [
      "¿Qué fuerza de ventas lidera?",
      "¿Qué fuerza tiene menor productividad?",
      "¿Cómo se reparte la venta dentro de cada fuerza?",
    ],
  },
  product_analysis: {
    summary: "Analiza participación y desempeño del portafolio por producto, línea o familia.",
    when: "Sirve para revisar mix, retracción y foco de portafolio.",
    questions: [
      "¿Qué familias o líneas están cayendo?",
      "¿Qué producto empuja el crecimiento?",
      "¿Dónde se concentra el negocio por marca?",
    ],
  },
  channel_analysis: {
    summary: "Compara la venta por canal y su peso relativo dentro del negocio.",
    when: "Usalo para revisar concentración y oportunidades por canal.",
    questions: [
      "¿Qué canal aporta más venta?",
      "¿En qué canal conviene crecer?",
      "¿Qué canal perdió participación?",
    ],
  },
  margin_analysis: {
    summary: "Cruza ingreso y costo para revisar rentabilidad relativa.",
    when: "Solo aplica si los datos traen costo o margen.",
    questions: [
      "¿Qué clientes o productos venden mucho pero dejan poco margen?",
      "¿Dónde hay venta rentable para profundizar?",
      "¿Qué cartera combina volumen y rentabilidad?",
    ],
  },
};

const COMMERCIAL_REQUEST_RECIPES = [
  {
    title: "Resumen general",
    action: "Usá Actualizar informe",
    detail: "Te devuelve KPIs, insights, rankings, semáforos y gráficos del período elegido.",
    examples: [
      "Cómo vendimos este mes",
      "Quiénes fueron los mejores vendedores",
      "Qué marcas, canales y rutas explican la venta",
    ],
  },
  {
    title: "Diagnóstico puntual",
    action: "Usá el Motor dinámico",
    detail: "Elegí un análisis específico cuando ya tengas una pregunta concreta.",
    examples: [
      "Qué clientes están en riesgo",
      "Qué ruta cayó más",
      "Qué canal perdió participación",
    ],
  },
  {
    title: "Recorte comercial",
    action: "Definí Enfoque del informe",
    detail: "Primero recortá por vendedor, ruta, fuerza, marca o proveedor; después actualizá.",
    examples: [
      "Solo vendedor GOLDEN",
      "Solo fuerza de ventas AMBA",
      "Solo línea o marca específica",
    ],
  },
];

let activeFilterTab = "tiempo";
let activePrefilterTab = "producto";
let erpKeepaliveTimer = null;
let progressDepth = 0;
let progressTicker = null;
let progressContext = null;

function formatDateInput(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function addDays(date, days) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function createErpConfig() {
  const today = new Date();
  return {
    fechaDesde: formatDateInput(addDays(today, -89)),
    fechaHasta: formatDateInput(today),
  };
}

function preferredStoredSalesSource() {
  if (state.clickhouseStorage?.available) {
    return "clickhouse";
  }
  if (state.erpStorage?.available) {
    return "mongo";
  }
  if (state.clickhouseStorage?.configured || state.clickhouseStorage?.connected) {
    return "clickhouse";
  }
  return "mongo";
}

function getStoredSalesSourceLabel(sourceMode) {
  if (sourceMode === "clickhouse") {
    return "ClickHouse";
  }
  if (sourceMode === "mongo") {
    return "MongoDB";
  }
  return "persistencia ERP";
}

function getStoredSalesStorage(sourceMode) {
  return sourceMode === "clickhouse" ? (state.clickhouseStorage || {}) : (state.erpStorage || {});
}

function getCommercialStorageWindow() {
  const windows = [state.erpStorage, state.clickhouseStorage]
    .filter((item) => item?.available && item?.periodStart && item?.periodEnd)
    .map((item) => ({ start: item.periodStart, end: item.periodEnd }));
  if (!windows.length) {
    return { available: false, periodStart: "", periodEnd: "" };
  }
  return {
    available: true,
    periodStart: windows.map((item) => item.start).sort()[0],
    periodEnd: windows.map((item) => item.end).sort().slice(-1)[0],
  };
}

function resolveStoredSalesRange(sourceMode, from, to) {
  const storage = getStoredSalesStorage(sourceMode);
  const periodStart = storage?.periodStart || "";
  const periodEnd = storage?.periodEnd || "";
  let nextFrom = from || periodStart;
  let nextTo = to || periodEnd;

  if (periodStart && (!nextFrom || nextFrom < periodStart || nextFrom > periodEnd)) {
    nextFrom = periodStart;
  }
  if (periodEnd && (!nextTo || nextTo > periodEnd || nextTo < periodStart)) {
    nextTo = periodEnd;
  }
  if (nextFrom && nextTo && nextFrom > nextTo) {
    nextFrom = periodStart || nextFrom;
    nextTo = periodEnd || nextTo;
  }
  return { from: nextFrom, to: nextTo };
}

function resolveCommercialRange(from, to) {
  const storage = getCommercialStorageWindow();
  if (!storage.available) {
    return { from: from || "", to: to || "" };
  }
  let nextFrom = from || storage.periodStart;
  let nextTo = to || storage.periodEnd;
  if (storage.periodStart && (!nextFrom || nextFrom < storage.periodStart || nextFrom > storage.periodEnd)) {
    nextFrom = storage.periodStart;
  }
  if (storage.periodEnd && (!nextTo || nextTo > storage.periodEnd || nextTo < storage.periodStart)) {
    nextTo = storage.periodEnd;
  }
  if (nextFrom && nextTo && nextFrom > nextTo) {
    nextFrom = storage.periodStart || nextFrom;
    nextTo = storage.periodEnd || nextTo;
  }
  return { from: nextFrom, to: nextTo };
}

function getPersistedTargetsLabel() {
  return state.clickhouseStorage?.configured || state.clickhouseStorage?.connected
    ? "MongoDB + ClickHouse"
    : "MongoDB";
}

function readStoredAdminToken() {
  try {
    return sessionStorage.getItem("appAdminToken") || "";
  } catch (_) {
    return "";
  }
}

function storeAdminToken(token) {
  state.adminAuth.token = token || "";
  try {
    if (state.adminAuth.token) {
      sessionStorage.setItem("appAdminToken", state.adminAuth.token);
    } else {
      sessionStorage.removeItem("appAdminToken");
    }
  } catch (_) {
    // Ignorar almacenamiento no disponible.
  }
}

function readStoredMetricMode() {
  try {
    const stored = localStorage.getItem("reportMetricMode") || "mixed";
    return REPORT_METRIC_MODES.includes(stored) ? stored : "mixed";
  } catch (_) {
    return "mixed";
  }
}

function storeMetricMode(mode) {
  const nextMode = REPORT_METRIC_MODES.includes(mode) ? mode : "mixed";
  state.reportView.metricMode = nextMode;
  try {
    localStorage.setItem("reportMetricMode", nextMode);
  } catch (_) {
    // Ignorar almacenamiento no disponible.
  }
}

function requestAdminToken() {
  const token = window.prompt("Ingresá el token de admin para continuar:");
  if (!token) {
    throw new Error("La operación admin requiere un token válido.");
  }
  storeAdminToken(token.trim());
}

async function api(url, options, retryOnAuth = true) {
  const headers = new Headers(options?.headers || {});
  if (isAdminSurface && state.adminAuth.token) {
    headers.set("X-Admin-Token", state.adminAuth.token);
  }
  const response = await fetch(url, { ...(options || {}), headers });
  const data = await response.json();
  if (response.status === 401 && isAdminSurface && retryOnAuth) {
    requestAdminToken();
    return api(url, options, false);
  }
  if (!response.ok) {
    throw new Error(data.error || "Error inesperado");
  }
  return data;
}

async function boot() {
  setStatus("Preparando estructura de análisis...");
  const [
    filesResponse,
    schemaResponse,
    sessionResponse,
    erpStatus,
    erpStorage,
    clickhouseStorage,
    erpPrefilters,
    adminErrorsResponse,
  ] = await Promise.all([
    api(`/api/files?scope=${encodeURIComponent(state.scope)}`),
    api("/api/datasets"),
    api("/api/session").catch(() => ({ datasets: null })),
    api("/api/erp/status").catch(() => ({ configured: false, reachable: false, message: "ChessERP no disponible." })),
    api("/api/erp/storage-status").catch(() => ({ connected: false, available: false, message: "MongoDB ERP no disponible." })),
    api("/api/clickhouse/storage-status").catch(() => ({ connected: false, available: false, configured: false, message: "ClickHouse no disponible." })),
    api("/api/erp/prefilter-options").catch(() => ({ filters: {} })),
    isAdminSurface ? api("/api/admin/errors?limit=20").catch(() => ({ errors: [] })) : Promise.resolve({ errors: [] }),
  ]);
  state.files = filesResponse.files || [];
  state.schema = schemaResponse.datasets || {};
  state.erpStatus = erpStatus || { configured: false, reachable: false, message: "" };
  state.erpStorage = erpStorage || { connected: false, available: false, message: "" };
  state.clickhouseStorage = clickhouseStorage || { connected: false, available: false, configured: false, message: "" };
  state.prefilters.available = erpPrefilters?.filters || {};
  state.prefilters.selected = normalizeSelectedFilters({}, state.prefilters.available);
  normalizeSupplierFocusSelection();
  state.adminErrors = adminErrorsResponse?.errors || [];
  initializeDatasets();

  if (isBiSurface) {
    restoreBiDefaults(sessionResponse.datasets);
    const commercialStorage = getCommercialStorageWindow();
    setStatus(commercialStorage.available
      ? "Base comercial lista para analizar."
      : "Todavía no hay histórico comercial persistido disponible. Coordiná una sync desde Admin.");
  } else if (sessionResponse.datasets) {
    await restoreSession(sessionResponse.datasets);
    setStatus("Sesión anterior restaurada. Podés analizar directamente o cambiar los archivos.");
  } else {
    setStatus(state.erpStatus.reachable
      ? "Podés usar ChessERP para ventas o cargar archivos Excel."
      : "Cargá uno o más archivos de venta por cliente y luego los maestros.");
  }

  renderFileLibrary();
  renderDatasetConfigs();
  renderCommercialGuide();
  renderPreview();
  renderAdminDiagnostics();
  startErpKeepalive();
}

function startErpKeepalive() {
  if (erpKeepaliveTimer) {
    clearInterval(erpKeepaliveTimer);
  }
  if (!state.erpStatus.configured) {
    return;
  }
  erpKeepaliveTimer = setInterval(async () => {
    if (progressDepth > 0 || state.erpSyncPending) {
      return;
    }
    try {
      const status = await api("/api/erp/status");
      state.erpStatus = status || state.erpStatus;
    } catch (_) {
      // Mantener silencioso el keepalive para no interrumpir la UI.
    }
  }, ERP_KEEPALIVE_MS);
}

async function restoreSession(saved) {
  // Ventas (múltiples fuentes)
  const savedSales = saved.sales;
  const savedErp = savedSales?.erp || {};
  const useAuto = savedSales?.source === "auto";
  const useErp = savedSales?.source === "erp" || savedErp.enabled;
  const useMongo = savedSales?.source === "mongo";
  const useClickHouse = savedSales?.source === "clickhouse" && (
    state.clickhouseStorage?.available || state.clickhouseStorage?.configured || state.clickhouseStorage?.connected
  );
  if (useClickHouse || useAuto) {
    state.datasets.sales.sourceMode = useAuto ? preferredStoredSalesSource() : "clickhouse";
    state.datasets.sales.erp.fechaDesde = savedSales?.fechaDesde || savedErp.fechaDesde || state.datasets.sales.erp.fechaDesde;
    state.datasets.sales.erp.fechaHasta = savedSales?.fechaHasta || savedErp.fechaHasta || state.datasets.sales.erp.fechaHasta;
  } else if (useMongo) {
    state.datasets.sales.sourceMode = "mongo";
    state.datasets.sales.erp.fechaDesde = savedSales?.fechaDesde || savedErp.fechaDesde || state.datasets.sales.erp.fechaDesde;
    state.datasets.sales.erp.fechaHasta = savedSales?.fechaHasta || savedErp.fechaHasta || state.datasets.sales.erp.fechaHasta;
  } else if (useErp) {
    state.datasets.sales.sourceMode = "erp";
    state.datasets.sales.erp.fechaDesde = savedSales?.fechaDesde || savedErp.fechaDesde || state.datasets.sales.erp.fechaDesde;
    state.datasets.sales.erp.fechaHasta = savedSales?.fechaHasta || savedErp.fechaHasta || state.datasets.sales.erp.fechaHasta;
  } else if (savedSales?.sources?.length) {
    state.datasets.sales.mapping = savedSales.mapping || {};
    const restoredSources = [];
    for (const src of savedSales.sources) {
      if (!src.file || !src.sheet) continue;
      const fileExists = state.files.some((f) => f.path === src.file);
      if (!fileExists) continue;   // el archivo ya no está en disco, omitir
      const source = createSource();
      source.file      = src.file;
      source.sheet     = src.sheet;
      source.headerRow = src.headerRow ?? 0;
      try {
        const wb = await api(`/api/workbook?file=${encodeURIComponent(src.file)}`);
        source.sheets = wb.sheets || [];
        const previewData = await api(`/api/preview?file=${encodeURIComponent(src.file)}&sheet=${encodeURIComponent(src.sheet)}&datasetType=sales`);
        source.preview = previewData.preview;
        source.mappingSuggestions = previewData.mappingSuggestions || {};
      } catch (_) { /* archivo inaccesible, dejar sin preview */ }
      restoredSources.push(source);
    }
    if (restoredSources.length) {
      state.datasets.sales.sources = restoredSources;
    }
  }

  // Datasets singulares (articles, routes, sellers)
  for (const dt of datasetOrder.filter((d) => d !== "sales")) {
    const savedDs = saved[dt];
    if (!savedDs?.file || !savedDs?.sheet) continue;
    const fileExists = state.files.some((f) => f.path === savedDs.file);
    if (!fileExists) continue;
    state.datasets[dt].file      = savedDs.file;
    state.datasets[dt].sheet     = savedDs.sheet;
    state.datasets[dt].headerRow = savedDs.headerRow ?? 0;
    state.datasets[dt].mapping   = savedDs.mapping || {};
    try {
      const wb = await api(`/api/workbook?file=${encodeURIComponent(savedDs.file)}`);
      state.datasets[dt].sheets = wb.sheets || [];
      const previewData = await api(`/api/preview?file=${encodeURIComponent(savedDs.file)}&sheet=${encodeURIComponent(savedDs.sheet)}&datasetType=${dt}`);
      state.datasets[dt].preview = previewData.preview;
      state.datasets[dt].mappingSuggestions = previewData.mappingSuggestions || {};
    } catch (_) { /* inaccessible */ }
  }
}

function restoreBiDefaults(saved) {
  state.datasets.sales.sourceMode = "auto";
  const savedSales = saved?.sales || {};
  const savedErp = savedSales.erp || {};
  const requestedFrom = savedSales.fechaDesde || savedErp.fechaDesde || "";
  const requestedTo = savedSales.fechaHasta || savedErp.fechaHasta || "";
  const { from, to } = resolveCommercialRange(requestedFrom, requestedTo);
  if (from) {
    state.datasets.sales.erp.fechaDesde = from;
  }
  if (to) {
    state.datasets.sales.erp.fechaHasta = to;
  }
}

function initializeDatasets() {
  state.datasets = {
    sales: {
      sourceMode: isBiSurface ? "auto" : "files",
      sources: [createSource()],
      mapping: {},
      mappingSuggestions: {},
      erp: createErpConfig(),
    },
    articles: createSingleDataset(),
    routes: createSingleDataset(),
    sellers: createSingleDataset(),
  };
  state.filters = { available: {}, selected: {} };
}

function createSource() {
  return {
    file: "",
    sheets: [],
    sheet: "",
    headerRow: 0,
    preview: null,
    mappingSuggestions: {},
  };
}

function createSingleDataset() {
  return {
    file: "",
    sheets: [],
    sheet: "",
    headerRow: 0,
    preview: null,
    mapping: {},
    mappingSuggestions: {},
  };
}

function renderFileLibrary() {
  const container = document.getElementById("fileLibrary");
  if (!container) {
    return;
  }
  const sidePanel = document.querySelector(".controls-side");
  if (sidePanel) {
    sidePanel.classList.toggle("hidden", !isAdminSurface || !usesFileMode());
  }
  if (!state.files.length) {
    container.innerHTML = "<div class='file-item muted'>No hay archivos .xlsx o .xlsm disponibles.</div>";
    return;
  }
  container.innerHTML = state.files.map((file) => `
    <div class="file-item">
      <strong>${escapeHtml(file.name)}</strong>
      <div class="muted">${escapeHtml(file.location)}</div>
      <div class="muted">${escapeHtml(file.path)}</div>
    </div>
  `).join("");
}

function usesFileMode() {
  return isAdminSurface && state.datasets?.sales?.sourceMode === "files";
}

function renderDatasetConfigs() {
  const container = document.getElementById("datasetConfigs");
  if (!container) {
    return;
  }
  container.innerHTML = renderSalesDatasetCard();

  bindDatasetEvents(container);
  renderCommercialGuide();
}

function renderCommercialGuide() {
  const container = document.getElementById("commercialGuide");
  if (!container || !isBiSurface) {
    return;
  }
  const storage = getCommercialStorageWindow();
  const period = storage.periodStart && storage.periodEnd
    ? `${storage.periodStart} a ${storage.periodEnd}`
    : "sin histórico disponible todavía";
  container.innerHTML = `
    <div class="guide-callout">
      <strong>Cómo pedir información en esta vista</strong>
      <div class="muted">Acá no escribís una consulta libre. El flujo es: elegís período, definís el enfoque comercial y después ejecutás el informe general o un análisis puntual.</div>
      <div class="muted">La app resuelve automáticamente la base correcta según el rango que pedís. Cobertura disponible: ${escapeHtml(period)}.</div>
    </div>
    <div class="guide-grid">
      ${COMMERCIAL_REQUEST_RECIPES.map((recipe) => `
        <article class="guide-card">
          <div class="guide-card-title">${escapeHtml(recipe.title)}</div>
          <div class="guide-card-action">${escapeHtml(recipe.action)}</div>
          <div class="muted">${escapeHtml(recipe.detail)}</div>
          <div class="guide-examples">
            ${recipe.examples.map((item) => `<span class="guide-chip">${escapeHtml(item)}</span>`).join("")}
          </div>
        </article>
      `).join("")}
    </div>
  `;
}

function renderSalesDatasetCard() {
  if (isBiSurface) {
    return renderBiSalesCard();
  }
  const schema = state.schema.sales;
  const dataset = state.datasets.sales;
  const usingErp = dataset.sourceMode === "erp";
  const usingMongo = dataset.sourceMode === "mongo";
  const usingClickHouse = dataset.sourceMode === "clickhouse";
  const clickhouseReady = state.clickhouseStorage?.available || state.clickhouseStorage?.configured || state.clickhouseStorage?.connected;
  return `
    <div class="source-card ${state.preview.datasetType === "sales" ? "active" : ""}">
      <div class="subpanel-header">
        <div>
          <h3>${escapeHtml(schema.label)}</h3>
          <div class="muted">${usingErp ? "Ventas en vivo desde ChessERP con rango de fechas." : usingMongo ? "Ventas ERP persistidas en MongoDB para trabajar sin depender del Excel." : usingClickHouse ? "Ventas históricas servidas desde ClickHouse para BI y consultas más pesadas." : "Podés cargar varios archivos, por ejemplo uno por año."}</div>
        </div>
        ${usingErp || usingMongo || usingClickHouse ? `<button data-sales-preview-erp="1">${state.preview.datasetType === "sales" ? "Viendo fuente" : "Ver fuente"}</button>` : `<button data-sales-add="1">Agregar archivo</button>`}
      </div>

      <div class="segmented">
        <button class="${!usingErp && !usingMongo && !usingClickHouse ? "active" : ""}" data-sales-mode="files">Archivos</button>
        <button class="${usingErp ? "active" : ""}" data-sales-mode="erp">ChessERP</button>
        <button class="${usingMongo ? "active" : ""}" data-sales-mode="mongo">MongoDB</button>
        <button class="${usingClickHouse ? "active" : ""}" data-sales-mode="clickhouse" ${clickhouseReady ? "" : "disabled"}>ClickHouse</button>
      </div>

      ${usingErp ? renderErpSalesPanel(dataset.erp) : usingMongo ? renderMongoSalesPanel(dataset.erp) : usingClickHouse ? renderClickHouseSalesPanel(dataset.erp) : `
        <div class="sales-source-list">
          ${dataset.sources.map((source, index) => renderSalesSource(source, index)).join("")}
        </div>

        <div class="mapping-grid compact">
          ${renderSalesMappingFields()}
        </div>
      `}
    </div>
  `;
}

function renderBiSalesCard() {
  const schema = state.schema.sales;
  return `
    <div class="source-card surface-bi-card">
      <div class="subpanel-header">
        <div>
          <h3>${escapeHtml(schema.label)}</h3>
          <div class="muted">Vista de solo lectura sobre la base comercial. El usuario comercial no ejecuta syncs ni cambia fuentes.</div>
        </div>
      </div>
      ${renderBiSalesPanel(state.datasets.sales.erp)}
    </div>
  `;
}

function renderBiSalesPanel(erp) {
  const storage = getCommercialStorageWindow();
  const period = storage.periodStart && storage.periodEnd
    ? `${storage.periodStart} a ${storage.periodEnd}`
    : "todavía sin histórico disponible";
  return `
    <div class="erp-panel bi-panel">
      <div class="bi-surface-note">
        <strong>Base comercial:</strong> consulta automática según el período seleccionado
      </div>
      <div class="row">
        <label>
          Fecha desde
          <input data-erp-from="1" type="date" value="${escapeHtml(erp.fechaDesde || "")}">
        </label>
        <label>
          Fecha hasta
          <input data-erp-to="1" type="date" value="${escapeHtml(erp.fechaHasta || "")}">
        </label>
      </div>
      ${renderSupplierFocusControl()}
      ${renderPrefilterPanel("Enfoque del informe")}
      <div class="request-flow">
        <div class="request-step">
          <strong>1. Actualizar informe</strong>
          <div class="muted">Usalo para obtener el tablero completo: KPIs, insights, rankings, gráficos y plan de acción.</div>
        </div>
        <div class="request-step">
          <strong>2. Motor dinámico</strong>
          <div class="muted">Usalo cuando ya tenés una pregunta puntual, por ejemplo tendencia, ranking, churn, mix, rutas o canales.</div>
        </div>
      </div>
      <div class="muted">Cobertura disponible actualmente: ${escapeHtml(period)}.</div>
    </div>
  `;
}

function renderErpSalesPanel(erp) {
  const statusClass = state.erpStatus.reachable ? "ok" : state.erpStatus.configured ? "warn" : "danger";
  const storage = `
    ${renderMongoStorageSummary()}
    ${state.clickhouseStorage?.configured || state.clickhouseStorage?.connected ? renderClickHouseStorageSummary() : ""}
  `;
  const persistLabel = getPersistedTargetsLabel();
  return `
    <div class="erp-panel">
      <div class="erp-status ${statusClass}">${escapeHtml(state.erpStatus.message || "Estado de ChessERP desconocido.")}</div>
      <div class="row">
        <label>
          Fecha desde
          <input data-erp-from="1" type="date" value="${escapeHtml(erp.fechaDesde || "")}">
        </label>
        <label>
          Fecha hasta
          <input data-erp-to="1" type="date" value="${escapeHtml(erp.fechaHasta || "")}">
        </label>
      </div>
      <div class="button-row">
        <button class="primary" data-erp-sync="1" ${state.erpSyncPending ? "disabled" : ""}>${state.erpSyncPending ? "Sincronizando..." : `Sincronizar en ${persistLabel}`}</button>
      </div>
      ${renderSupplierFocusControl()}
      ${renderPrefilterPanel("Enfoque del informe")}
      ${storage}
      <div class="muted">Podés definir el enfoque comercial antes de analizar. Por limitación actual de ChessERP, la extracción de ventas sigue yendo por fecha; estas pestañas segmentan el informe dentro de la app usando los maestros persistidos.</div>
    </div>
  `;
}

function renderMongoSalesPanel(erp) {
  const statusClass = state.erpStorage.available ? "ok" : state.erpStorage.connected ? "warn" : "danger";
  return `
    <div class="erp-panel">
      <div class="erp-status ${statusClass}">${escapeHtml(state.erpStorage.message || "Estado de MongoDB desconocido.")}</div>
      <div class="row">
        <label>
          Fecha desde
          <input data-erp-from="1" type="date" value="${escapeHtml(erp.fechaDesde || "")}">
        </label>
        <label>
          Fecha hasta
          <input data-erp-to="1" type="date" value="${escapeHtml(erp.fechaHasta || "")}">
        </label>
      </div>
      ${renderSupplierFocusControl()}
      ${renderPrefilterPanel("Enfoque del informe")}
      ${renderMongoStorageSummary()}
      <div class="muted">El análisis usará únicamente ventas ERP persistidas en MongoDB para el rango seleccionado.</div>
    </div>
  `;
}

function renderClickHouseSalesPanel(erp) {
  const statusClass = state.clickhouseStorage.available ? "ok" : state.clickhouseStorage.connected ? "warn" : "danger";
  return `
    <div class="erp-panel">
      <div class="erp-status ${statusClass}">${escapeHtml(state.clickhouseStorage.message || "Estado de ClickHouse desconocido.")}</div>
      <div class="row">
        <label>
          Fecha desde
          <input data-erp-from="1" type="date" value="${escapeHtml(erp.fechaDesde || "")}">
        </label>
        <label>
          Fecha hasta
          <input data-erp-to="1" type="date" value="${escapeHtml(erp.fechaHasta || "")}">
        </label>
      </div>
      ${renderSupplierFocusControl()}
      ${renderPrefilterPanel("Enfoque del informe")}
      ${renderClickHouseStorageSummary()}
      <div class="muted">El análisis usará ventas ERP persistidas en ClickHouse para el rango seleccionado.</div>
    </div>
  `;
}

function normalizeSupplierFocusSelection(rawValue = state.supplierFocus) {
  const options = state.prefilters.available?.supplier?.options || [];
  const match = options.find((option) => normalizeText(option.value) === normalizeText(rawValue));
  state.supplierFocus = match ? String(match.value) : "";
  return state.supplierFocus;
}

function renderSupplierFocusControl() {
  const supplierConfig = state.prefilters.available?.supplier;
  if (!supplierConfig?.options?.length) {
    return "";
  }
  const selected = normalizeSupplierFocusSelection();
  return `
    <div class="filter-grid">
      <div class="subpanel-header">
        <div>
          <strong>Proveedor foco</strong>
          <div class="muted">Restringe el informe completo al proveedor elegido y recalcula gráficos, rankings e insights con ese recorte.</div>
        </div>
      </div>
      <div class="row">
        <label class="wide">
          Proveedor
          <select data-supplier-focus="1">
            <option value="">Todos los proveedores</option>
            ${supplierConfig.options.map((option) => `<option value="${escapeHtml(option.value)}" ${option.value === selected ? "selected" : ""}>${escapeHtml(option.label)} (${option.count})</option>`).join("")}
          </select>
        </label>
      </div>
    </div>
  `;
}

function renderPrefilterPanel(title) {
  const availableGroups = prefilterGroups.filter((group) =>
    group.fields.some((field) => state.prefilters.available[field]?.options?.length)
  );
  if (!availableGroups.length) {
    return "<div class='muted'>Todavía no hay agrupaciones comerciales disponibles. Pedí una actualización de maestros para habilitar el enfoque por producto y comercial.</div>";
  }
  if (!availableGroups.find((group) => group.id === activePrefilterTab)) {
    activePrefilterTab = availableGroups[0].id;
  }
  const tabsHtml = availableGroups.map((group) => {
    const activeInGroup = group.fields.filter((field) => isFieldConstrained(field, state.prefilters.selected, state.prefilters.available)).length;
    const badge = activeInGroup ? `<span class="filter-tab-badge">${activeInGroup}</span>` : "";
    return `<button class="filter-tab${group.id === activePrefilterTab ? " active" : ""}" data-prefilter-tab="${group.id}">${escapeHtml(group.label)}${badge}</button>`;
  }).join("");
  const activeGroup = availableGroups.find((group) => group.id === activePrefilterTab);
  const fieldsHtml = activeGroup
    ? activeGroup.fields
        .filter((field) => state.prefilters.available[field]?.options?.length)
        .map((field) => renderSelectableField("prefilter", field, state.prefilters.available[field], state.prefilters.selected[field] || []))
        .join("")
    : "<div class='muted'>No hay agrupaciones disponibles.</div>";
  return `
    <div class="filter-grid">
      <div class="subpanel-header">
        <div>
          <strong>${escapeHtml(title)}</strong>
          <div class="muted">Elegí segmentos comerciales antes de ejecutar el análisis.</div>
        </div>
        <div class="button-row">
          <button data-prefilter-clear="1">Limpiar enfoque</button>
        </div>
      </div>
      <div class="filter-tabs">${tabsHtml}</div>
      <div class="filter-tab-body">
        <div class="filter-tab-hint muted">Marcá las opciones que quieras incluir o usá Todos / Ninguno.</div>
        <div class="filter-fields-row">${fieldsHtml}</div>
      </div>
    </div>
  `;
}

function renderMongoStorageSummary() {
  const storage = state.erpStorage || {};
  const range = storage.lastRange ? `${storage.lastRange.fechaDesde} a ${storage.lastRange.fechaHasta}` : "sin sincronizaciones";
  const period = storage.periodStart && storage.periodEnd ? `${storage.periodStart} a ${storage.periodEnd}` : "sin registros";
  const chunkInfo = Number(storage.lastChunkCount || 0) > 1
    ? `<div><strong>Última sync resuelta en:</strong> ${Number(storage.lastChunkCount).toLocaleString("es-AR")} tramos internos</div>`
    : "";
  const lastSyncRows = Number.isFinite(Number(storage.lastSyncRowsValid))
    ? `<div><strong>Ventas incorporadas en la última sync:</strong> ${Number(storage.lastSyncRowsValid || 0).toLocaleString("es-AR")}</div>`
    : "";
  const warningInfo = storage.lastSyncWarning
    ? `<div><strong>Advertencia última sync:</strong> ${escapeHtml(storage.lastSyncWarning)}</div>`
    : "";
  return `
    <div class="mongo-summary">
      <div><strong>Ventas persistidas:</strong> ${Number(storage.records || 0).toLocaleString("es-AR")}</div>
      <div><strong>Artículos persistidos:</strong> ${Number(storage.articleRecords || 0).toLocaleString("es-AR")}</div>
      <div><strong>Vendedores persistidos:</strong> ${Number(storage.sellerRecords || 0).toLocaleString("es-AR")}</div>
      <div><strong>Rutas persistidas:</strong> ${Number(storage.routeRecords || 0).toLocaleString("es-AR")}</div>
      <div><strong>Jerarquía MKT persistida:</strong> ${Number(storage.marketingRecords || 0).toLocaleString("es-AR")}</div>
      <div><strong>Período disponible:</strong> ${escapeHtml(period)}</div>
      <div><strong>Última sincronización:</strong> ${escapeHtml(storage.lastSyncAt || "nunca")}</div>
      <div><strong>Última sync de artículos:</strong> ${escapeHtml(storage.articlesLastSyncAt || "nunca")}</div>
      <div><strong>Última sync de vendedores:</strong> ${escapeHtml(storage.sellersLastSyncAt || "nunca")}</div>
      <div><strong>Última sync de rutas:</strong> ${escapeHtml(storage.routesLastSyncAt || "nunca")}</div>
      <div><strong>Última sync de jerarquía MKT:</strong> ${escapeHtml(storage.marketingLastSyncAt || "nunca")}</div>
      <div><strong>Último rango sincronizado:</strong> ${escapeHtml(range)}</div>
      ${chunkInfo}
      ${lastSyncRows}
      ${warningInfo}
    </div>
  `;
}

function renderClickHouseStorageSummary() {
  const storage = state.clickhouseStorage || {};
  const period = storage.periodStart && storage.periodEnd ? `${storage.periodStart} a ${storage.periodEnd}` : "sin registros";
  return `
    <div class="mongo-summary">
      <div><strong>Ventas persistidas:</strong> ${Number(storage.records || 0).toLocaleString("es-AR")}</div>
      <div><strong>Período disponible:</strong> ${escapeHtml(period)}</div>
      <div><strong>Base:</strong> ${escapeHtml(storage.database || "-")}</div>
      <div><strong>Tabla:</strong> ${escapeHtml(storage.table || "-")}</div>
      <div><strong>Rows activas:</strong> ${Number(storage.rows || 0).toLocaleString("es-AR")}</div>
      <div><strong>Storage estimado:</strong> ${Number(storage.storageMB || 0).toLocaleString("es-AR")} MB</div>
    </div>
  `;
}

function renderAdminDiagnostics() {
  const summary = document.getElementById("adminOpsSummary");
  const errors = document.getElementById("adminErrors");
  if (!summary || !errors) {
    return;
  }
  const storage = state.erpStorage || {};
  const clickhouse = state.clickhouseStorage || {};
  const period = storage.periodStart && storage.periodEnd
    ? `${storage.periodStart} a ${storage.periodEnd}`
    : "sin período cargado";
  const clickhousePeriod = clickhouse.periodStart && clickhouse.periodEnd
    ? `${clickhouse.periodStart} a ${clickhouse.periodEnd}`
    : "sin período cargado";
  summary.innerHTML = [
    `ChessERP: ${state.erpStatus.message || "Sin estado"}`,
    `MongoDB: ${storage.message || "Sin estado"}`,
    `ClickHouse: ${clickhouse.message || "Sin estado"}`,
    `Cobertura disponible en Mongo: ${period}`,
    `Cobertura disponible en ClickHouse: ${clickhousePeriod}`,
    `Último rango sincronizado: ${storage.lastRange ? `${storage.lastRange.fechaDesde} a ${storage.lastRange.fechaHasta}` : "sin sincronizaciones"}`,
    `Última sync con ventas: ${Number(storage.lastSyncRowsValid || 0).toLocaleString("es-AR")} filas`,
    `Ventas persistidas en ClickHouse: ${Number(clickhouse.records || 0).toLocaleString("es-AR")} filas`,
    "Siguiente paso recomendado: mover el backfill histórico y las syncs recurrentes a procesos backend programados.",
  ].map((item) => `<div class="insight-item">${escapeHtml(item)}</div>`).join("");

  if (!state.adminErrors.length) {
    errors.innerHTML = "<div class='muted'>Sin errores recientes registrados.</div>";
    return;
  }
  errors.innerHTML = state.adminErrors.map((item) => {
    const extra = Object.keys(item.extra || {}).length
      ? `<div class="muted">${escapeHtml(JSON.stringify(item.extra))}</div>`
      : "";
    return `
      <div class="insight-item">
        <strong>${escapeHtml(item.context || "error")}</strong> · ${escapeHtml(item.timestamp || "")}
        <div>${escapeHtml(item.error || "Sin detalle")}</div>
        ${extra}
      </div>
    `;
  }).join("");
}

function renderSalesSource(source, index) {
  const fileOptions = ['<option value="">Sin archivo</option>']
    .concat(state.files.map((file) => `<option value="${escapeHtml(file.path)}" ${file.path === source.file ? "selected" : ""}>${escapeHtml(file.name)} · ${escapeHtml(file.location)}</option>`))
    .join("");
  const sheetOptions = source.sheets.length
    ? source.sheets.map((sheet) => `<option value="${escapeHtml(sheet.name)}" ${sheet.name === source.sheet ? "selected" : ""}>${escapeHtml(sheet.name)}</option>`).join("")
    : '<option value="">Elegí un archivo</option>';
  return `
    <div class="sales-source">
      <div class="subpanel-header">
        <strong>Archivo ${index + 1}</strong>
        <div class="button-row">
          <button data-sales-preview="${index}">${state.preview.datasetType === "sales" && state.preview.sourceIndex === index ? "Viendo preview" : "Ver preview"}</button>
          ${state.datasets.sales.sources.length > 1 ? `<button data-sales-remove="${index}">Quitar</button>` : ""}
        </div>
      </div>
      <label>
        Archivo
        <select data-sales-file="${index}">
          ${fileOptions}
        </select>
      </label>
      <label>
        Hoja
        <select data-sales-sheet="${index}">
          ${sheetOptions}
        </select>
      </label>
      <label>
        Fila de encabezado
        <input data-sales-header="${index}" type="number" min="0" value="${source.headerRow}">
      </label>
      <div class="muted">${source.preview ? `${source.preview.rowCount} filas detectadas` : "Sin preview"}</div>
    </div>
  `;
}

function renderSalesMappingFields() {
  if (state.datasets.sales.sourceMode === "erp" || state.datasets.sales.sourceMode === "mongo" || state.datasets.sales.sourceMode === "clickhouse") {
    return "";
  }
  const schema = state.schema.sales;
  const previewSource = getSalesPreviewSource();
  const headers = previewSource?.preview?.headers || [];
  return schema.fields.map((field) => {
    const value = state.datasets.sales.mapping[field.id];
    return `
      <label>
        ${escapeHtml(field.label)}${field.required ? " *" : ""}
        <select data-map-dataset="sales" data-map-field="${field.id}">
          <option value="">No mapear</option>
          ${headers.map((header, index) => `<option value="${index}" ${value === index ? "selected" : ""}>${escapeHtml(header)}</option>`).join("")}
        </select>
      </label>
    `;
  }).join("");
}

function renderSingleDatasetCard(datasetType) {
  const schema = state.schema[datasetType];
  const dataset = state.datasets[datasetType];
  const fileOptions = ['<option value="">Sin archivo</option>']
    .concat(state.files.map((file) => `<option value="${escapeHtml(file.path)}" ${file.path === dataset.file ? "selected" : ""}>${escapeHtml(file.name)} · ${escapeHtml(file.location)}</option>`))
    .join("");
  const sheetOptions = dataset.sheets.length
    ? dataset.sheets.map((sheet) => `<option value="${escapeHtml(sheet.name)}" ${sheet.name === dataset.sheet ? "selected" : ""}>${escapeHtml(sheet.name)}</option>`).join("")
    : '<option value="">Elegí un archivo</option>';
  return `
    <div class="source-card ${state.preview.datasetType === datasetType ? "active" : ""}">
      <div class="subpanel-header">
        <div>
          <h3>${escapeHtml(schema.label)}</h3>
          <div class="muted">${schema.required ? "Obligatorio" : "Opcional pero recomendado"}</div>
        </div>
        <button data-preview="${datasetType}">${state.preview.datasetType === datasetType ? "Viendo preview" : "Ver preview"}</button>
      </div>

      <label>
        Archivo
        <select data-file="${datasetType}">
          ${fileOptions}
        </select>
      </label>

      <label>
        Hoja
        <select data-sheet="${datasetType}">
          ${sheetOptions}
        </select>
      </label>

      <label>
        Fila de encabezado
        <input data-header="${datasetType}" type="number" min="0" value="${dataset.headerRow}">
      </label>

      <div class="muted">${dataset.preview ? `${dataset.preview.rowCount} filas detectadas` : "Sin preview"}</div>

      <div class="mapping-grid compact">
        ${renderMappingFields(datasetType)}
      </div>
    </div>
  `;
}

function renderMappingFields(datasetType) {
  const schema = state.schema[datasetType];
  const dataset = state.datasets[datasetType];
  const headers = dataset.preview?.headers || [];
  return schema.fields.map((field) => {
    const value = dataset.mapping[field.id];
    return `
      <label>
        ${escapeHtml(field.label)}${field.required ? " *" : ""}
        <select data-map-dataset="${datasetType}" data-map-field="${field.id}">
          <option value="">No mapear</option>
          ${headers.map((header, index) => `<option value="${index}" ${value === index ? "selected" : ""}>${escapeHtml(header)}</option>`).join("")}
        </select>
      </label>
    `;
  }).join("");
}

function bindDatasetEvents(container) {
  container.querySelectorAll("[data-sales-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.datasets.sales.sourceMode = button.dataset.salesMode;
      state.preview = { datasetType: "sales", sourceIndex: 0 };
      renderDatasetConfigs();
      renderPreview();
    });
  });

  container.querySelectorAll("[data-sales-add]").forEach((button) => {
    button.addEventListener("click", () => {
      state.datasets.sales.sources.push(createSource());
      state.preview = { datasetType: "sales", sourceIndex: state.datasets.sales.sources.length - 1 };
      renderDatasetConfigs();
      renderPreview();
    });
  });

  container.querySelectorAll("[data-sales-remove]").forEach((button) => {
    button.addEventListener("click", () => {
      const index = Number(button.dataset.salesRemove);
      state.datasets.sales.sources.splice(index, 1);
      if (state.preview.datasetType === "sales") {
        state.preview.sourceIndex = Math.max(0, Math.min(state.preview.sourceIndex, state.datasets.sales.sources.length - 1));
      }
      renderDatasetConfigs();
      renderPreview();
    });
  });

  container.querySelectorAll("[data-sales-preview]").forEach((button) => {
    button.addEventListener("click", () => {
      state.preview = { datasetType: "sales", sourceIndex: Number(button.dataset.salesPreview) };
      renderDatasetConfigs();
      renderPreview();
    });
  });

  container.querySelectorAll("[data-sales-preview-erp]").forEach((button) => {
    button.addEventListener("click", () => {
      state.preview = { datasetType: "sales", sourceIndex: 0 };
      renderDatasetConfigs();
      renderPreview();
    });
  });

  container.querySelectorAll("[data-erp-sync]").forEach((button) => {
    button.addEventListener("click", () => syncErpToMongo().catch(showError));
  });

  container.querySelectorAll("[data-supplier-focus]").forEach((select) => {
    select.addEventListener("change", () => {
      state.supplierFocus = select.value || "";
      const label = state.supplierFocus ? `Proveedor foco: ${state.supplierFocus}. Actualizá el informe para recalcular gráficos, rankings e insights con ese recorte.` : "Proveedor foco limpiado. Actualizá el informe para volver a la vista general.";
      setStatus(label);
    });
  });

  container.querySelectorAll("[data-prefilter-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      activePrefilterTab = button.dataset.prefilterTab;
      renderDatasetConfigs();
    });
  });

  container.querySelectorAll("[data-selectable-kind='prefilter'][data-selectable-role='option']").forEach((input) => {
    input.addEventListener("change", () => {
      toggleSelectableValue("prefilter", input.dataset.selectableField, input.value, input.checked);
    });
  });

  container.querySelectorAll("[data-selectable-kind='prefilter'][data-selectable-role='action']").forEach((button) => {
    button.addEventListener("click", () => {
      applySelectableAction("prefilter", button.dataset.selectableField, button.dataset.selectableAction);
      renderDatasetConfigs();
    });
  });

  container.querySelectorAll("[data-prefilter-clear]").forEach((button) => {
    button.addEventListener("click", () => {
      state.prefilters.selected = normalizeSelectedFilters({}, state.prefilters.available);
      renderDatasetConfigs();
    });
  });

  container.querySelectorAll("[data-sales-file]").forEach((select) => {
    select.addEventListener("change", async () => {
      await onSalesFileChange(Number(select.dataset.salesFile), select.value);
    });
  });

  container.querySelectorAll("[data-sales-sheet]").forEach((select) => {
    select.addEventListener("change", async () => {
      const index = Number(select.dataset.salesSheet);
      state.datasets.sales.sources[index].sheet = select.value;
      await refreshSalesPreview(index);
      renderDatasetConfigs();
      if (state.preview.datasetType === "sales" && state.preview.sourceIndex === index) {
        renderPreview();
      }
    });
  });

  container.querySelectorAll("[data-sales-header]").forEach((input) => {
    input.addEventListener("change", () => {
      const index = Number(input.dataset.salesHeader);
      state.datasets.sales.sources[index].headerRow = Number(input.value || 0);
      if (state.preview.datasetType === "sales" && state.preview.sourceIndex === index) {
        renderPreview();
      }
    });
  });

  container.querySelectorAll("[data-erp-from]").forEach((input) => {
    input.addEventListener("change", () => {
      state.datasets.sales.erp.fechaDesde = input.value;
      if (state.preview.datasetType === "sales") {
        renderPreview();
      }
    });
  });

  container.querySelectorAll("[data-erp-to]").forEach((input) => {
    input.addEventListener("change", () => {
      state.datasets.sales.erp.fechaHasta = input.value;
      if (state.preview.datasetType === "sales") {
        renderPreview();
      }
    });
  });

  container.querySelectorAll("[data-preview]").forEach((button) => {
    button.addEventListener("click", () => {
      state.preview = { datasetType: button.dataset.preview, sourceIndex: 0 };
      renderDatasetConfigs();
      renderPreview();
    });
  });

  container.querySelectorAll("[data-file]").forEach((select) => {
    select.addEventListener("change", async () => {
      await onFileChange(select.dataset.file, select.value);
    });
  });

  container.querySelectorAll("[data-sheet]").forEach((select) => {
    select.addEventListener("change", async () => {
      const datasetType = select.dataset.sheet;
      state.datasets[datasetType].sheet = select.value;
      await refreshPreviewForDataset(datasetType);
      renderDatasetConfigs();
      if (state.preview.datasetType === datasetType) {
        renderPreview();
      }
    });
  });

  container.querySelectorAll("[data-header]").forEach((input) => {
    input.addEventListener("change", () => {
      const datasetType = input.dataset.header;
      state.datasets[datasetType].headerRow = Number(input.value || 0);
      if (state.preview.datasetType === datasetType) {
        renderPreview();
      }
    });
  });

  container.querySelectorAll("[data-map-dataset]").forEach((select) => {
    select.addEventListener("change", () => {
      const datasetType = select.dataset.mapDataset;
      const field = select.dataset.mapField;
      if (datasetType === "sales") {
        state.datasets.sales.mapping[field] = select.value === "" ? null : Number(select.value);
      } else {
        state.datasets[datasetType].mapping[field] = select.value === "" ? null : Number(select.value);
      }
    });
  });
}

async function onSalesFileChange(index, filePath) {
  const source = state.datasets.sales.sources[index];
  source.file = filePath;
  source.preview = null;
  source.sheets = [];
  source.sheet = "";
  source.headerRow = 0;
  source.mappingSuggestions = {};
  if (!filePath) {
    renderDatasetConfigs();
    if (state.preview.datasetType === "sales" && state.preview.sourceIndex === index) {
      renderPreview();
    }
    return;
  }
  setStatus(`Leyendo estructura de ${lookupFileName(filePath)}...`);
  const workbook = await api(`/api/workbook?file=${encodeURIComponent(filePath)}`);
  source.sheets = workbook.sheets || [];
  source.sheet = workbook.defaultSheet || source.sheets?.[0]?.name || "";
  await refreshSalesPreview(index);
  state.preview = { datasetType: "sales", sourceIndex: index };
  renderDatasetConfigs();
  renderPreview();
  setStatus(`Archivo ${lookupFileName(filePath)} agregado a Venta por cliente.`);
}

async function refreshSalesPreview(index) {
  const source = state.datasets.sales.sources[index];
  if (!source.file || !source.sheet) {
    return;
  }
  const data = await api(`/api/preview?file=${encodeURIComponent(source.file)}&sheet=${encodeURIComponent(source.sheet)}&datasetType=sales`);
  source.preview = data.preview;
  source.mappingSuggestions = data.mappingSuggestions || {};
  source.headerRow = data.preview.headerRow;
  if (isEmptyMapping(state.datasets.sales.mapping)) {
    state.datasets.sales.mapping = { ...source.mappingSuggestions };
  }
}

async function onFileChange(datasetType, filePath) {
  const dataset = state.datasets[datasetType];
  dataset.file = filePath;
  dataset.preview = null;
  dataset.mappingSuggestions = {};
  dataset.mapping = {};
  if (!filePath) {
    dataset.sheets = [];
    dataset.sheet = "";
    dataset.headerRow = 0;
    renderDatasetConfigs();
    if (state.preview.datasetType === datasetType) {
      renderPreview();
    }
    return;
  }

  setStatus(`Leyendo estructura de ${lookupFileName(filePath)}...`);
  const workbook = await api(`/api/workbook?file=${encodeURIComponent(filePath)}`);
  dataset.sheets = workbook.sheets || [];
  dataset.sheet = workbook.defaultSheet || dataset.sheets?.[0]?.name || "";
  await refreshPreviewForDataset(datasetType);
  state.preview = { datasetType, sourceIndex: 0 };
  renderDatasetConfigs();
  renderPreview();
  setStatus(`Archivo ${lookupFileName(filePath)} listo para mapear como ${state.schema[datasetType].label}.`);
}

async function refreshPreviewForDataset(datasetType) {
  const dataset = state.datasets[datasetType];
  if (!dataset.file || !dataset.sheet) {
    return;
  }
  const data = await api(`/api/preview?file=${encodeURIComponent(dataset.file)}&sheet=${encodeURIComponent(dataset.sheet)}&datasetType=${encodeURIComponent(datasetType)}`);
  dataset.preview = data.preview;
  dataset.mappingSuggestions = data.mappingSuggestions || {};
  dataset.headerRow = data.preview.headerRow;
  dataset.mapping = { ...dataset.mappingSuggestions };
}

async function refreshErpStorageStatus() {
  state.erpStorage = await api("/api/erp/storage-status").catch(() => ({ connected: false, available: false, message: "MongoDB ERP no disponible." }));
  renderAdminDiagnostics();
}

async function refreshClickHouseStorageStatus() {
  state.clickhouseStorage = await api("/api/clickhouse/storage-status").catch(() => ({ connected: false, available: false, configured: false, message: "ClickHouse no disponible." }));
  renderAdminDiagnostics();
}

async function refreshErpPrefilterOptions() {
  const response = await api("/api/erp/prefilter-options").catch(() => ({ filters: {} }));
  state.prefilters.available = response.filters || {};
  state.prefilters.selected = normalizeSelectedFilters(state.prefilters.selected, state.prefilters.available);
  normalizeSupplierFocusSelection();
}

async function refreshAdminErrors() {
  if (!isAdminSurface) {
    return;
  }
  const response = await api("/api/admin/errors?limit=20").catch(() => ({ errors: [] }));
  state.adminErrors = response.errors || [];
  renderAdminDiagnostics();
}

async function syncErpToMongo() {
  if (state.erpSyncPending) {
    return;
  }
  const { fechaDesde, fechaHasta } = state.datasets.sales.erp;
  if (!fechaDesde || !fechaHasta) {
    setStatus("Elegí fecha desde y fecha hasta para sincronizar.");
    return;
  }
  state.erpSyncPending = true;
  renderDatasetConfigs();
  const persistLabel = getPersistedTargetsLabel();
  try {
    setStatus(`Sincronizando ChessERP en ${persistLabel} para ${fechaDesde} a ${fechaHasta}...`);
    const data = await withProgress(`Sincronizando ChessERP en ${persistLabel} para ${fechaDesde} a ${fechaHasta}...`, () => api("/api/erp/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fechaDesde, fechaHasta, refreshMasters: false }),
    }), { operationType: "sync", fechaDesde, fechaHasta, sourceMode: "erp" });
    state.erpStorage = data.storage || state.erpStorage;
    state.clickhouseStorage = data.clickhouseStorage || state.clickhouseStorage;
    await refreshErpPrefilterOptions();
    if (data.warning) {
      state.preview = { datasetType: "sales", sourceIndex: 0 };
      renderDatasetConfigs();
      renderPreview();
      setStatus(`Sincronización completa sin ventas nuevas para ${fechaDesde} a ${fechaHasta}. ${data.warning}`);
      return;
    }
    state.datasets.sales.sourceMode = preferredStoredSalesSource();
    state.preview = { datasetType: "sales", sourceIndex: 0 };
    renderDatasetConfigs();
    renderPreview();
    const mastersMessage = data.mastersSynced
      ? `${data.articleRowsValid || 0} artículos, ${data.sellerRowsValid || 0} vendedores, ${data.routeRowsValid || 0} rutas y ${data.marketingRowsValid || 0} nodos MKT`
      : "maestros reutilizados desde MongoDB";
    const chunkMessage = Number(data.sync?.chunkCount || 0) > 1
      ? ` La app dividió el rango en ${data.sync.chunkCount} tramos internos.`
      : "";
    const storageBreakdown = [
      `base operativa ${Number(data.mongoStored || 0).toLocaleString("es-AR")} filas`,
      data.clickhouseStorage?.configured ? `base histórica ${Number(data.clickhouseStored || 0).toLocaleString("es-AR")} filas` : "",
    ].filter(Boolean).join(" · ");
    setStatus(`Sincronización completa: ${data.rowsValid} líneas de venta. ${storageBreakdown}. ${mastersMessage}.${chunkMessage}`);
    renderAdminDiagnostics();
  } finally {
    state.erpSyncPending = false;
    renderDatasetConfigs();
  }
}

function renderPreview() {
  const meta = document.getElementById("previewMeta");
  const container = document.getElementById("previewContainer");
  if (!meta || !container) {
    return;
  }
  const target = getPreviewTarget();

  if (!target) {
    meta.textContent = "Elegí un dataset para ver su preview.";
    container.innerHTML = "<div class='muted'>Todavía no hay vista previa disponible.</div>";
    return;
  }

  if (target.message) {
    meta.textContent = target.meta;
    container.innerHTML = `<div class='muted'>${escapeHtml(target.message)}</div>`;
    return;
  }

  if (!target.preview) {
    meta.textContent = "Elegí un dataset para ver su preview.";
    container.innerHTML = "<div class='muted'>Todavía no hay vista previa disponible.</div>";
    return;
  }

  meta.textContent = target.meta;
  const rows = target.preview.preview || [];
  if (!rows.length) {
    container.innerHTML = "<div class='muted'>No hay filas para mostrar.</div>";
    return;
  }

  const table = document.createElement("table");
  const body = document.createElement("tbody");
  rows.forEach((row, index) => {
    const tr = document.createElement("tr");
    const rowLabel = document.createElement("td");
    rowLabel.textContent = `Fila ${index}`;
    tr.appendChild(rowLabel);
    row.forEach((value) => {
      const cell = document.createElement(index === Number(target.headerRow) ? "th" : "td");
      cell.textContent = value ?? "";
      tr.appendChild(cell);
    });
    body.appendChild(tr);
  });
  table.appendChild(body);
  container.innerHTML = "";
  container.appendChild(table);
}

function getPreviewTarget() {
  if (state.preview.datasetType === "sales") {
    if (state.datasets.sales.sourceMode === "erp" || state.datasets.sales.sourceMode === "mongo" || state.datasets.sales.sourceMode === "clickhouse" || state.datasets.sales.sourceMode === "auto") {
      const erp = state.datasets.sales.erp;
      return {
        meta: `${state.schema.sales.label} · ${erp.fechaDesde || "sin fecha"} a ${erp.fechaHasta || "sin fecha"}`,
        message: isBiSurface
          ? "La base comercial se resolverá automáticamente según el período seleccionado."
          : state.datasets.sales.sourceMode === "mongo"
            ? "La fuente de ventas será MongoDB con datos sincronizados desde ChessERP."
            : state.datasets.sales.sourceMode === "clickhouse"
              ? "La fuente de ventas será ClickHouse con datos persistidos y compactados desde ChessERP."
              : state.erpStatus.reachable
                ? "ChessERP no ofrece preview tabular en esta pantalla. El análisis tomará ventas históricas del rango seleccionado."
                : "La conexión con ChessERP no está disponible. Revisá el estado antes de analizar.",
      };
    }
    const source = getSalesPreviewSource();
    if (!source) {
      return null;
    }
    return {
      preview: source.preview,
      headerRow: source.headerRow,
      meta: `${state.schema.sales.label} · archivo ${state.preview.sourceIndex + 1} · ${lookupFileName(source.file)} · hoja ${source.sheet}`,
    };
  }
  const dataset = state.datasets[state.preview.datasetType];
  if (!dataset) {
    return null;
  }
  return {
    preview: dataset.preview,
    headerRow: dataset.headerRow,
    meta: `${state.schema[state.preview.datasetType].label} · ${lookupFileName(dataset.file)} · hoja ${dataset.sheet}`,
  };
}

function getSalesPreviewSource() {
  return state.datasets.sales.sources[state.preview.sourceIndex] || state.datasets.sales.sources[0];
}

async function uploadFiles() {
  const input = document.getElementById("uploadInput");
  const files = Array.from(input.files || []);
  if (!files.length) {
    setStatus("Seleccioná al menos un archivo para subir.");
    return;
  }
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  setStatus("Subiendo archivos...");
  const response = await withProgress("Subiendo archivos a la biblioteca...", () => api("/api/upload", { method: "POST", body: form }));
  state.scope = "uploads";
  state.files = response.files || [];
  renderFileLibrary();
  renderDatasetConfigs();
  document.getElementById("libraryScope").value = state.scope;
  input.value = "";
  setStatus(`Se subieron ${response.uploaded.length} archivo(s).`);
}

async function clearUploads() {
  setStatus("Limpiando archivos subidos...");
  const response = await withProgress("Limpiando archivos subidos...", () => api("/api/clear-uploads", { method: "POST" }));
  state.scope = "uploads";
  state.files = response.files || [];
  initializeDatasets();
  renderFileLibrary();
  renderDatasetConfigs();
  renderPreview();
  document.getElementById("results").classList.add("hidden");
  document.getElementById("libraryScope").value = state.scope;
  setStatus(`Se eliminaron ${response.removed} archivo(s) subidos. Ya podés empezar de nuevo.`);
}

async function analyze() {
  const payload = {
    datasets: {},
    filters: serializeFilters(),
    supplierFocus: normalizeSupplierFocusSelection(),
  };

  if (state.datasets.sales.sourceMode === "erp" || state.datasets.sales.sourceMode === "mongo" || state.datasets.sales.sourceMode === "clickhouse" || state.datasets.sales.sourceMode === "auto") {
    const { fechaDesde, fechaHasta } = state.datasets.sales.erp;
    if (!fechaDesde || !fechaHasta) {
      setStatus(isBiSurface ? "Elegí fecha desde y fecha hasta para consultar la base comercial." : "Elegí fecha desde y fecha hasta para consultar ventas ERP.");
      return;
    }
    payload.datasets.sales = {
      source: state.datasets.sales.sourceMode,
      fechaDesde,
      fechaHasta,
      erp: { enabled: state.datasets.sales.sourceMode === "erp", fechaDesde, fechaHasta },
    };
  } else {
    const salesSources = state.datasets.sales.sources
      .filter((source) => source.file && source.sheet)
      .map((source) => ({
        file: source.file,
        sheet: source.sheet,
        headerRow: Number(source.headerRow || 0),
      }));
    if (!salesSources.length) {
      setStatus("Cargá al menos un archivo en Venta por cliente.");
      return;
    }
    payload.datasets.sales = {
      source: "files",
      sources: salesSources,
      mapping: state.datasets.sales.mapping,
    };
  }

  for (const datasetType of datasetOrder.filter((item) => item !== "sales")) {
    const dataset = state.datasets[datasetType];
    if (!dataset.file || !dataset.sheet) {
      continue;
    }
    payload.datasets[datasetType] = {
      file: dataset.file,
      sheet: dataset.sheet,
      headerRow: Number(dataset.headerRow || 0),
      mapping: dataset.mapping,
    };
  }

  setStatus(
    isBiSurface
      ? "Consultando la base comercial, relacionando ventas con maestros y recalculando el informe..."
      : state.datasets.sales.sourceMode === "erp"
        ? "Consultando ChessERP, relacionando ventas con maestros y recalculando el informe..."
        : state.datasets.sales.sourceMode === "mongo"
          ? "Leyendo ventas ERP persistidas en MongoDB, relacionando datos y recalculando el informe..."
          : state.datasets.sales.sourceMode === "clickhouse"
            ? "Leyendo ventas ERP persistidas en ClickHouse, relacionando datos y recalculando el informe..."
            : "Relacionando ventas con maestros y recalculando el informe..."
  );
  const data = await withProgress(document.getElementById("status").textContent || "Procesando análisis...", () => api("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }), {
    operationType: "analyze",
    fechaDesde: state.datasets.sales.erp?.fechaDesde,
    fechaHasta: state.datasets.sales.erp?.fechaHasta,
    sourceMode: state.datasets.sales.sourceMode,
  });
  renderResults(data);
  const comparisonLabel = data.meta?.comparison?.comparisonLabel;
  setStatus(
    comparisonLabel
      ? `Análisis completo: ${data.meta.rowsAnalyzed} registros del período ${data.meta.periodStart} a ${data.meta.periodEnd}, comparados contra ${comparisonLabel}.`
      : `Análisis completo: ${data.meta.rowsAnalyzed} registros analizados entre ${data.meta.periodStart} y ${data.meta.periodEnd}.`
  );
}

function renderResults(data) {
  document.getElementById("results").classList.remove("hidden");
  state.reportView.lastData = data;
  state.supplierFocus = data.supplierFocus?.selected ? (data.supplierFocus.supplier || "") : normalizeSupplierFocusSelection();
  state.filters.available = data.availableFilters || {};
  state.filters.selected = normalizeSelectedFilters(data.appliedFilters || {}, state.filters.available);
  renderFilterPanel(data.meta);
  renderMetricViewBar(data);
  renderReportData(data);
}

function renderReportData(data) {
  const mode = resolveMetricMode(data.summary);
  renderSummaryCards(data.summary, data.meta, mode);
  renderExecutiveDashboard(data, mode);
  renderSemaphores(data.semaphores, data.summary, data.forecast, data.meta, mode);
  renderCoverage(data.coverage, data.meta.datasets);
  renderInsights(data.insights, data.summary, data.meta, mode);
  renderActionPlan(data.actionPlan);
  renderRatios(data.ratios, data.opportunities, data.supplierFocus, mode);
  renderForecast(data.forecast, data.meta, mode);
  renderCharts(data.charts, data.supplierFocus, mode);
  renderRankings(data.rankings, mode);
  renderDynamicPanel(data);
}

function resolveMetricMode(summary = {}) {
  const preferred = state.reportView.metricMode || "mixed";
  if (preferred === "units" && !summary.volumeModeActive) {
    return "sales";
  }
  return preferred;
}

function renderMetricViewBar(data) {
  const containers = [document.getElementById("metricViewHeader")].filter(Boolean);
  if (!containers.length) {
    return;
  }
  const mode = resolveMetricMode(data.summary);
  const disabledUnits = !data.summary?.volumeModeActive;
  const helper = mode === "mixed"
    ? "Mixto muestra resumen dual y prioriza bultos en gráficos y rankings."
    : mode === "sales"
      ? "La visualización prioriza pesos en gráficos, rankings y semáforos."
      : "La visualización prioriza bultos en gráficos, rankings y semáforos.";
  const html = `
    <div class="subpanel-header">
      <div>
        <h2>Visualización del informe</h2>
        <div class="muted">${helper}</div>
      </div>
      <div class="metric-toggle" role="tablist" aria-label="Modo del informe">
        <button type="button" class="metric-chip${mode === "mixed" ? " active" : ""}" data-metric-mode="mixed">Mixto</button>
        <button type="button" class="metric-chip${mode === "units" ? " active" : ""}" data-metric-mode="units" ${disabledUnits ? "disabled" : ""}>Bultos</button>
        <button type="button" class="metric-chip${mode === "sales" ? " active" : ""}" data-metric-mode="sales">Pesos</button>
      </div>
    </div>
  `;
  containers.forEach((container) => {
    container.classList.remove("hidden");
    container.innerHTML = html;
    container.querySelectorAll("[data-metric-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        storeMetricMode(button.dataset.metricMode);
        if (state.reportView.lastData) {
          renderMetricViewBar(state.reportView.lastData);
          renderReportData(state.reportView.lastData);
        }
      });
    });
  });
}

function renderExecutiveDashboard(data, mode = "mixed") {
  renderExecutiveDashboardSummary(data, mode);
  renderExecutiveCharts(data, mode);
  renderExecutiveTable(data, mode);
}

function renderExecutiveDashboardSummary(data, mode) {
  const summary = data.summary || {};
  const comparisonLabel = data.meta?.comparison?.comparisonLabel || summary.comparisonLabel || "período anterior equivalente";
  const text = mode === "sales"
    ? `Tablero en pesos para ${summary.periodLabel || "el período"} contra ${comparisonLabel}.`
    : mode === "mixed"
      ? `Tablero mixto para ${summary.periodLabel || "el período"} con lectura simultánea de pesos y bultos.`
      : `Tablero en bultos para ${summary.periodLabel || "el período"} contra ${comparisonLabel}.`;
  document.getElementById("executiveDashboardSummary").textContent = text;
}

function renderExecutiveCharts(data, mode) {
  if (!window.echarts) {
    renderDashboardFallback();
    return;
  }
  const trendMode = mode === "sales" ? "sales" : mode === "units" ? "units" : "mixed";
  mountDashboardChart("execTrendChart", buildTrendOption(data, trendMode));
  mountDashboardChart("execSellerChart", buildSellerOption(data, mode));
  mountDashboardChart("execChannelChart", buildPieOption(data, mode, "channel"));
  mountDashboardChart("execBrandChart", buildBarOption(data, mode, "brand"));
}

function renderExecutiveTable(data, mode) {
  const host = document.getElementById("executiveTable");
  if (!host) {
    return;
  }
  const tableRows = buildExecutiveTableRows(data, mode);
  const tableModeLabel = mode === "sales" ? "pesos" : mode === "units" ? "bultos" : "visión mixta";
  document.getElementById("executiveTableTitle").textContent = "Mesa comercial priorizada";
  document.getElementById("executiveTableSummary").textContent = `Clientes y cuentas clave del período en ${tableModeLabel}.`;
  if (!window.Tabulator) {
    host.innerHTML = "<div class='muted'>Tabulator no está disponible en esta sesión.</div>";
    return;
  }
  if (!dashboardState.table) {
    dashboardState.table = new Tabulator(host, {
      data: tableRows,
      layout: "fitColumns",
      responsiveLayout: "collapse",
      height: "420px",
      pagination: true,
      paginationSize: 10,
      movableColumns: true,
      placeholder: "Sin registros para mostrar",
      columns: buildExecutiveColumns(mode),
    });
    return;
  }
  dashboardState.table.setColumns(buildExecutiveColumns(mode));
  dashboardState.table.replaceData(tableRows);
}

function renderDashboardFallback() {
  ["execTrendChart", "execSellerChart", "execChannelChart", "execBrandChart"].forEach((id) => {
    const node = document.getElementById(id);
    if (node) {
      node.innerHTML = "<div class='muted'>ECharts no está disponible en esta sesión.</div>";
    }
  });
}

function mountDashboardChart(id, option) {
  const node = document.getElementById(id);
  if (!node) {
    return;
  }
  let chart = dashboardState.charts[id];
  if (!chart) {
    chart = echarts.init(node, null, { renderer: "canvas" });
    dashboardState.charts[id] = chart;
  }
  chart.setOption(option, true);
}

function buildTrendOption(data, mode) {
  const salesSeries = data.charts?.salesByMonthMoney || [];
  const unitsSeries = data.charts?.salesByMonthUnits || [];
  const labels = dedupeLabels([...salesSeries, ...unitsSeries].map((item) => item.label));
  const salesMap = indexSeriesByLabel(salesSeries);
  const unitsMap = indexSeriesByLabel(unitsSeries);
  const summary = data.summary || {};
  document.getElementById("execTrendTitle").textContent = mode === "sales"
    ? "Evolución de ventas"
    : mode === "units"
      ? "Evolución de bultos"
      : "Evolución del período";
  const series = [];
  if (mode !== "units") {
    series.push({
      name: "Pesos",
      type: "bar",
      itemStyle: { color: "#0f766e", borderRadius: [8, 8, 0, 0] },
      data: labels.map((label) => salesMap.get(label) || 0),
      yAxisIndex: 0,
    });
  }
  if (mode !== "sales") {
    series.push({
      name: "Bultos",
      type: "line",
      smooth: true,
      symbolSize: 8,
      lineStyle: { width: 3, color: mode === "mixed" ? "#b45309" : "#1d4ed8" },
      itemStyle: { color: mode === "mixed" ? "#b45309" : "#1d4ed8" },
      yAxisIndex: mode === "mixed" ? 1 : 0,
      data: labels.map((label) => unitsMap.get(label) || 0),
    });
  }
  return {
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { color: "#6b7280" } },
    grid: { left: 56, right: mode === "mixed" ? 56 : 24, top: 42, bottom: 40 },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: {
        color: "#6b7280",
        interval: 0,
        formatter: (value) => formatMonthAxisLabel(value),
      },
    },
    yAxis: mode === "mixed"
      ? [
          { type: "value", axisLabel: { color: "#6b7280", formatter: (value) => compactMoney(value) } },
          { type: "value", axisLabel: { color: "#6b7280", formatter: (value) => compactNumber(value) } },
        ]
      : [{ type: "value", axisLabel: { color: "#6b7280", formatter: (value) => mode === "sales" ? compactMoney(value) : compactNumber(value) } }],
    series,
    graphic: buildChartKicker(mode === "sales"
      ? `Venta actual ${money(summary.salesCurrent || 0)}`
      : mode === "units"
        ? `Bultos actuales ${decimalNumber(summary.unitsCurrent || 0)}`
        : `Actual ${money(summary.salesCurrent || 0)} y ${decimalNumber(summary.unitsCurrent || 0)} bultos`),
  };
}

function buildSellerOption(data, mode) {
  const isSales = mode === "sales";
  const isMixed = mode === "mixed";
  const sellers = isSales ? (data.rankings?.topSellersBySales || []) : (data.rankings?.topSellersByUnits || []);
  document.getElementById("execSellerTitle").textContent = isSales ? "Top vendedores por pesos" : isMixed ? "Top vendedores por bultos" : "Top vendedores por bultos";
  return {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 140, right: 24, top: 18, bottom: 20 },
    xAxis: { type: "value", axisLabel: { color: "#6b7280", formatter: (value) => isSales ? compactMoney(value) : compactNumber(value) } },
    yAxis: { type: "category", data: sellers.map((item) => item.seller), axisLabel: { color: "#6b7280" } },
    series: [{
      type: "bar",
      data: sellers.map((item) => isSales ? item.sales : item.quantity),
      itemStyle: { color: isSales ? "#0f766e" : "#1d4ed8", borderRadius: [0, 8, 8, 0] },
      label: { show: true, position: "right", formatter: ({ value }) => isSales ? compactMoney(value) : compactNumber(value), color: "#6b7280" },
    }],
  };
}

function buildPieOption(data, mode, target) {
  const isSales = mode === "sales";
  const items = target === "channel"
    ? (isSales ? (data.rankings?.topChannelsBySales || []) : (data.rankings?.topChannelsByUnits || []))
    : [];
  document.getElementById("execChannelTitle").textContent = isSales ? "Canales por pesos" : mode === "mixed" ? "Canales por bultos" : "Canales por bultos";
  return {
    tooltip: { trigger: "item" },
    legend: { bottom: 0, textStyle: { color: "#6b7280" } },
    series: [{
      type: "pie",
      radius: ["44%", "72%"],
      center: ["50%", "44%"],
      itemStyle: { borderRadius: 10, borderColor: "#fffdf8", borderWidth: 3 },
      label: { formatter: "{b}\n{d}%" },
      data: items.map((item) => ({ name: item.channel, value: isSales ? item.sales : item.quantity })),
    }],
  };
}

function buildBarOption(data, mode, target) {
  const isSales = mode === "sales";
  const items = target === "brand"
    ? (isSales ? (data.rankings?.topBrandsBySales || []) : (data.rankings?.topBrandsByUnits || []))
    : [];
  document.getElementById("execBrandTitle").textContent = isSales ? "Marcas por pesos" : mode === "mixed" ? "Marcas por bultos" : "Marcas por bultos";
  return {
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 56, right: 24, top: 20, bottom: 52 },
    xAxis: {
      type: "category",
      data: items.map((item) => item.brand),
      axisLabel: { color: "#6b7280", rotate: 22 },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "#6b7280", formatter: (value) => isSales ? compactMoney(value) : compactNumber(value) },
    },
    series: [{
      type: "bar",
      data: items.map((item) => isSales ? item.sales : item.quantity),
      itemStyle: { color: isSales ? "#15803d" : "#b45309", borderRadius: [8, 8, 0, 0] },
    }],
  };
}

function buildExecutiveTableRows(data, mode) {
  const baseRows = mode === "sales"
    ? (data.rankings?.positiveClientsBySales || [])
    : mode === "units"
      ? (data.rankings?.positiveClientsByUnits || [])
      : (data.rankings?.positiveClientsBySales || []);
  return baseRows.map((item, index) => ({
    rank: index + 1,
    client: item.client,
    status: item.status || "Activo",
    sales: Number(item.sales12m || 0),
    units: Number(item.quantity12m || 0),
    avgTicket: Number(item.avgTicket || 0),
    avgUnitsPerOrder: Number(item.avgUnitsPerOrder || 0),
    families: Number(item.families || 0),
    lastDate: item.lastDate || "",
    salesForce: item.sales_force || "Sin fuerza",
    route: item.route_description || "Sin ruta",
    seller: item.seller_name || "Sin vendedor",
  }));
}

function buildExecutiveColumns(mode) {
  const columns = [
    { title: "#", field: "rank", hozAlign: "center", width: 60 },
    { title: "Cliente", field: "client", minWidth: 220, headerFilter: "input" },
    { title: "Estado", field: "status", width: 130, headerFilter: "list", headerFilterParams: { valuesLookup: true } },
    { title: "Pesos", field: "sales", hozAlign: "right", formatter: (cell) => money(cell.getValue()) },
    { title: "Bultos", field: "units", hozAlign: "right", formatter: (cell) => decimalNumber(cell.getValue()) },
    { title: "Ticket", field: "avgTicket", hozAlign: "right", formatter: (cell) => money(cell.getValue()) },
    { title: "Bultos/pedido", field: "avgUnitsPerOrder", hozAlign: "right", formatter: (cell) => decimalNumber(cell.getValue()) },
    { title: "Familias", field: "families", hozAlign: "center", width: 110 },
    { title: "Última compra", field: "lastDate", width: 130 },
    { title: "Fuerza", field: "salesForce", minWidth: 140, headerFilter: "input" },
    { title: "Ruta", field: "route", minWidth: 150, headerFilter: "input" },
    { title: "Vendedor", field: "seller", minWidth: 150, headerFilter: "input" },
  ];
  if (mode === "sales") {
    return columns.filter((column) => !["units", "avgUnitsPerOrder"].includes(column.field));
  }
  if (mode === "units") {
    return columns.filter((column) => !["avgTicket"].includes(column.field));
  }
  return columns;
}

function buildChartKicker(text) {
  return [{
    type: "text",
    right: 8,
    top: 8,
    style: {
      text,
      fill: "#6b7280",
      font: '12px "Trebuchet MS", Verdana, sans-serif',
    },
  }];
}

function dedupeLabels(labels) {
  return [...new Set(labels.filter(Boolean))];
}

function indexSeriesByLabel(items) {
  return new Map((items || []).map((item) => [item.label, Number(item.value || 0)]));
}

function compactMoney(value) {
  const amount = Number(value || 0);
  if (Math.abs(amount) >= 1_000_000) {
    return `${(amount / 1_000_000).toFixed(1)}M`;
  }
  if (Math.abs(amount) >= 1_000) {
    return `${(amount / 1_000).toFixed(0)}K`;
  }
  return intNumber(amount);
}

function compactNumber(value) {
  const amount = Number(value || 0);
  if (Math.abs(amount) >= 1_000_000) {
    return `${(amount / 1_000_000).toFixed(1)}M`;
  }
  if (Math.abs(amount) >= 1_000) {
    return `${(amount / 1_000).toFixed(0)}K`;
  }
  return decimalNumber(amount);
}

function formatMonthAxisLabel(label) {
  if (!label || !/^\d{4}-\d{2}$/.test(label)) {
    return label || "";
  }
  const [year, month] = label.split("-");
  const shortMonths = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
  return `${shortMonths[Number(month) - 1] || month} ${year.slice(2)}`;
}

function renderFilterPanel(meta) {
  const container = document.getElementById("resultsFilters");
  container._lastMeta = meta;  // guardado para re-render al cambiar tab
  const activeCount = countConstrainedFields(state.filters.selected, state.filters.available);
  const comparisonLabel = meta?.comparison?.comparisonLabel;
  const summary = activeCount
    ? `${meta.activeFilterSummary}. ${meta.rowsAnalyzed} registros del período seleccionados de ${meta.rowsUniverse} disponibles.${comparisonLabel ? ` Comparando contra ${comparisonLabel}.` : ""}`
    : `Sin filtros aplicados. ${meta.rowsAnalyzed} registros del período disponibles.${comparisonLabel ? ` Comparando contra ${comparisonLabel}.` : ""}`;

  // Calcular qué grupos tienen opciones disponibles
  const availableGroups = filterGroups.filter((g) =>
    g.fields.some((f) => state.filters.available[f]?.options?.length)
  );

  // Si la pestaña activa no tiene datos, resetear a la primera disponible
  if (!availableGroups.find((g) => g.id === activeFilterTab) && availableGroups.length) {
    activeFilterTab = availableGroups[0].id;
  }

  // Badges con conteo de filtros activos por grupo
  const tabsHtml = availableGroups.map((g) => {
    const activeInGroup = g.fields.filter((f) => isFieldConstrained(f, state.filters.selected, state.filters.available)).length;
    const badge = activeInGroup ? `<span class="filter-tab-badge">${activeInGroup}</span>` : "";
    return `<button class="filter-tab${g.id === activeFilterTab ? " active" : ""}" data-filter-tab="${g.id}">${escapeHtml(g.label)}${badge}</button>`;
  }).join("");

  // Contenido del tab activo
  const activeGroup = availableGroups.find((g) => g.id === activeFilterTab);
  const fieldsHtml = activeGroup
    ? activeGroup.fields
        .filter((f) => state.filters.available[f]?.options?.length)
        .map((f) => renderFilterField(f, state.filters.available[f], state.filters.selected[f] || []))
        .join("")
    : "<div class='muted'>No hay dimensiones disponibles.</div>";

  container.innerHTML = `
    <div class="subpanel-header">
      <div>
        <h2>Filtros del informe</h2>
        <div class="muted">${escapeHtml(summary)}</div>
      </div>
      <div class="button-row">
        <button id="applyFiltersBtn" class="primary">Aplicar</button>
        <button id="clearFiltersBtn">Limpiar filtros</button>
      </div>
    </div>
    <div class="filter-tabs">${tabsHtml}</div>
    <div class="filter-tab-body">
      <div class="filter-tab-hint muted">Marcá las opciones que quieras incluir o usá Todos / Ninguno.</div>
      <div class="filter-fields-row">${fieldsHtml}</div>
    </div>
  `;
  bindFilterEvents();
}

function renderFilterField(field, config, selectedValues) {
  return renderSelectableField("filter", field, config, selectedValues);
}

function renderSelectableField(kind, field, config, selectedValues) {
  const searchKey = buildSelectableKey(kind, field);
  const searchValue = state.filterSearch[searchKey] || "";
  const visibleOptions = filterSelectableOptions(config.options, searchValue);
  return `
    <div class="filter-box">
      <div class="filter-box-header">
        <div class="filter-box-title">${escapeHtml(config.label)}</div>
        <div class="filter-box-actions">
          <button type="button" data-selectable-kind="${kind}" data-selectable-role="action" data-selectable-action="all" data-selectable-field="${field}">Todos</button>
          <button type="button" data-selectable-kind="${kind}" data-selectable-role="action" data-selectable-action="none" data-selectable-field="${field}">Ninguno</button>
        </div>
      </div>
      <input
        class="filter-search"
        type="search"
        placeholder="Buscar..."
        value="${escapeHtml(searchValue)}"
        data-selectable-kind="${kind}"
        data-selectable-role="search"
        data-selectable-field="${field}"
      >
      <div class="filter-box-list">
        ${visibleOptions.length ? visibleOptions.map((option) => `
          <label class="check-option">
            <input
              type="checkbox"
              data-selectable-kind="${kind}"
              data-selectable-role="option"
              data-selectable-field="${field}"
              value="${escapeHtml(option.value)}"
              ${isSelectedFilterValue(field, selectedValues, option.value) ? "checked" : ""}
            >
            <span>${escapeHtml(option.label)}</span>
            <span class="check-option-count">${option.count}</span>
          </label>
        `).join("") : `<div class="muted">No hay coincidencias para "${escapeHtml(searchValue)}".</div>`}
      </div>
    </div>
  `;
}

function renderSummaryCards(summary, meta = {}, mode = "mixed") {
  const comparisonLabel = meta?.comparison?.comparisonLabel || summary.comparisonLabel || "período anterior equivalente";
  const cards = mode === "mixed" ? [
    ["Período comparado", summary.periodLabel || `${meta.periodStart} a ${meta.periodEnd}`, `vs ${comparisonLabel.toLowerCase()}`],
    ["Bultos del período", decimalNumber(summary.unitsCurrent), `base ${decimalNumber(summary.unitsPrevious || 0)} · ${summary.unitsGrowthPct}%`],
    ["Venta del período", money(summary.salesCurrent), `base ${money(summary.salesPrevious || 0)} · ${summary.salesGrowthPct}%`],
    ["Pedidos", intNumber(summary.ordersCurrent), `${decimalNumber(summary.avgUnitsPerOrder)} bultos por pedido · ${money(summary.avgOrderValue)} por pedido`],
    ["Frecuencia de compra", `${decimalNumber(summary.purchaseFrequencyMonthly)} veces por mes`, `${decimalNumber(summary.purchaseFrequencyUniverseMonthly)} sobre padrón · ${decimalNumber(summary.ordersPerMonth)} pedidos/mes`],
    ["Precio promedio / bulto", money(summary.avgUnitPrice), `${money(summary.avgTicket)} ticket cliente`],
    ["Productividad comercial", `${decimalNumber(summary.unitsPerActiveSeller)} bultos`, `${money(summary.salesPerActiveSeller)} por vendedor activo`],
    ["Clientes activos", intNumber(summary.activeClients), `${summary.activeRatioPct}% del padrón`],
  ] : mode === "units" ? [
    ["Bultos del período", decimalNumber(summary.unitsCurrent), `vs ${comparisonLabel.toLowerCase()} ${summary.unitsGrowthPct}% · base ${decimalNumber(summary.unitsPrevious || 0)}`],
    ["Pedidos", intNumber(summary.ordersCurrent), `${decimalNumber(summary.avgUnitsPerOrder)} bultos por pedido`],
    ["Frecuencia de compra", `${decimalNumber(summary.purchaseFrequencyMonthly)} veces por mes`, `${decimalNumber(summary.purchaseFrequencyUniverseMonthly)} sobre padrón · ${decimalNumber(summary.ordersPerMonth)} pedidos/mes`],
    ["Venta del período", money(summary.salesCurrent), `base ${money(summary.salesPrevious || 0)} · vs ${comparisonLabel.toLowerCase()} ${summary.salesGrowthPct}%`],
    ["Precio promedio / bulto", money(summary.avgUnitPrice), `${money(summary.avgTicket)} ticket cliente`],
    ["Clientes activos", intNumber(summary.activeClients), `${summary.activeRatioPct}% del padrón`],
    ["Bultos por vendedor", decimalNumber(summary.unitsPerActiveSeller), `${summary.sellerCount} vendedores · ${summary.salesForceCount} fuerzas`],
    ["Mix activo", `${summary.brandCount} marcas`, `${summary.businessUnitCount} unidades negocio · ${summary.channelCount} canales`],
    ["Cobertura BI", `${summary.articleCoveragePct}%`, `${summary.routeCoveragePct}% rutas · ${summary.sellerCoveragePct}% vendedores`],
  ] : [
    ["Venta del período", money(summary.salesCurrent), `vs ${comparisonLabel.toLowerCase()} ${summary.salesGrowthPct}% · base ${money(summary.salesPrevious || 0)}`],
    ["Pedidos", intNumber(summary.ordersCurrent), `${money(summary.avgOrderValue)} por pedido`],
    ["Frecuencia de compra", `${decimalNumber(summary.purchaseFrequencyMonthly)} veces por mes`, `${decimalNumber(summary.purchaseFrequencyUniverseMonthly)} sobre padrón · ${decimalNumber(summary.ordersPerMonth)} pedidos/mes`],
    ["Unidades", decimalNumber(summary.unitsCurrent), `${decimalNumber(summary.avgUnitsPerOrder)} por pedido`],
    ["Precio promedio / unidad", money(summary.avgUnitPrice), `${money(summary.avgTicket)} ticket cliente`],
    ["Clientes activos", intNumber(summary.activeClients), `${summary.activeRatioPct}% del padrón`],
    ["Productividad vendedor", money(summary.salesPerActiveSeller), `${summary.sellerCount} vendedores · ${summary.salesForceCount} fuerzas`],
    ["Mix activo", `${summary.brandCount} marcas`, `${summary.businessUnitCount} unidades negocio · ${summary.channelCount} canales`],
    ["Cobertura BI", `${summary.articleCoveragePct}%`, `${summary.routeCoveragePct}% rutas · ${summary.sellerCoveragePct}% vendedores`],
  ];
  document.getElementById("summaryCards").innerHTML = cards.map(([label, value, sub]) => `
    <article class="card summary-card">
      <div class="label">${label}</div>
      <div class="value ${summaryValueClass(value)}">${value}</div>
      <div class="sub">${sub}</div>
    </article>
  `).join("");
}

function summaryValueClass(value) {
  const text = String(value ?? "");
  if (text.length >= 24) {
    return "value-xs";
  }
  if (text.length >= 15 || text.includes("\n")) {
    return "value-sm";
  }
  return "";
}

function renderSemaphores(items, summary = {}, forecast = {}, meta = {}, mode = "mixed") {
  const normalized = [...(items || [])];
  const comparisonLabel = meta?.comparison?.comparisonLabel || summary.comparisonLabel || "período anterior equivalente";
  if (normalized[0]) {
    normalized[0] = {
      ...normalized[0],
      detail: mode === "sales"
        ? `${summary.periodLabel || "Período"}: ${summary.salesGrowthPct}% vs ${comparisonLabel.toLowerCase()}`
        : mode === "mixed"
          ? `${summary.periodLabel || "Período"}: ${summary.unitsGrowthPct}% en bultos y ${summary.salesGrowthPct}% en pesos`
          : `${summary.periodLabel || "Período"}: ${summary.unitsGrowthPct}% vs ${comparisonLabel.toLowerCase()}`,
    };
  }
  if (normalized[normalized.length - 1]) {
    normalized[normalized.length - 1] = {
      ...normalized[normalized.length - 1],
      detail: mode === "sales"
        ? `Próxima ventana: ${money(forecast.projectedQuarterSales || 0)}`
        : mode === "mixed"
          ? `Próxima ventana: ${decimalNumber(forecast.projectedQuarterUnits || 0)} bultos y ${money(forecast.projectedQuarterSales || 0)}`
          : `Próxima ventana: ${decimalNumber(forecast.projectedQuarterUnits || 0)} bultos`,
    };
  }
  document.getElementById("semaphores").innerHTML = normalized.map((item) => `
    <div class="semaphore">
      <div><span class="dot ${item.color}"></span><strong>${item.name}</strong></div>
      <div class="muted">${item.detail}</div>
    </div>
  `).join("");
}

function renderCoverage(coverage, datasets) {
  const items = [
    `Rutas: ${coverage.routeCoveragePct}% de cobertura · ${money(coverage.salesWithoutRoute)} sin ruta`,
    `Artículos: ${coverage.articleCoveragePct}% de cobertura · ${money(coverage.salesWithoutArticle)} sin enriquecer`,
    `Vendedores: ${coverage.sellerCoveragePct}% de cobertura · ${money(coverage.salesWithoutSeller)} sin vendedor consistente`,
  ];

  for (const dataset of datasets || []) {
    if (dataset.datasetType === "sales" && dataset.sources?.length) {
      items.push(`Venta por cliente: ${dataset.sourceCount} archivo(s) · ${dataset.rowsValid} filas válidas consolidadas`);
      dataset.sources.forEach((source) => {
        items.push(`Ventas: ${lookupFileName(source.file)} · hoja ${source.sheet} · ${source.rowsValid} filas válidas`);
      });
    } else {
      items.push(`${dataset.label}: ${lookupFileName(dataset.file)} · ${dataset.rowsValid} filas válidas`);
    }
  }
  document.getElementById("coverageCards").innerHTML = items.map((item) => `<div class="insight-item">${item}</div>`).join("");
}

function renderInsights(items, summary = {}, meta = {}, mode = "mixed") {
  const comparisonLabel = meta?.comparison?.comparisonLabel || summary.comparisonLabel || "período anterior equivalente";
  const intro = mode === "sales"
    ? `En ${summary.periodLabel || "el período"} la referencia principal es ${money(summary.salesCurrent || 0)}, frente a ${money(summary.salesPrevious || 0)} en ${comparisonLabel.toLowerCase()}.`
    : mode === "mixed"
      ? `En ${summary.periodLabel || "el período"} se combinaron ${decimalNumber(summary.unitsCurrent || 0)} bultos y ${money(summary.salesCurrent || 0)}, comparados contra ${comparisonLabel.toLowerCase()}.`
      : `En ${summary.periodLabel || "el período"} la referencia principal es ${decimalNumber(summary.unitsCurrent || 0)} bultos, frente a ${decimalNumber(summary.unitsPrevious || 0)} en ${comparisonLabel.toLowerCase()}.`;
  document.getElementById("insights").innerHTML = [intro, ...(items || [])]
    .map((item) => `<div class="insight-item">${item}</div>`)
    .join("");
}

function renderActionPlan(items) {
  document.getElementById("actionPlan").innerHTML = (items || []).map((item) => `
    <div class="action-item">
      <div class="subpanel-header">
        <strong>${item.title}</strong>
        <span class="pill">${item.priority}</span>
      </div>
      <div class="muted">Responsable: ${item.owner} · Horizonte: ${item.horizon}</div>
      <div>${item.detail}</div>
    </div>
  `).join("");
}

function renderRatios(ratios, opportunities, supplierFocus = {}, mode = "mixed") {
  const title = document.getElementById("ratiosSectionTitle");
  if (supplierFocus?.selected) {
    if (title) {
      title.textContent = `Ratios del proveedor · ${supplierFocus.label}`;
    }
    const focusRatios = supplierFocus.ratios || {};
    const focusTotals = supplierFocus.totals || {};
    const items = [
      `Proveedor foco: ${supplierFocus.label}`,
      `Bultos / cliente activo: ${decimalNumber(focusRatios.bultosCliente)}`,
      `Facturación / cliente activo: ${money(focusRatios.facturacionCliente)}`,
      `Penetración: ${focusRatios.penetracionPct || 0}% · ${intNumber(focusRatios.clientesCompradores || 0)} clientes compradores sobre ${intNumber(focusRatios.clientesActivosTotales || 0)} activos`,
      `Rotación: ${decimalNumber(focusRatios.rotacion)} bultos por cliente activo`,
      `Mix proveedor: ${focusRatios.mixMarcaPct || 0}% de la facturación total`,
      `Ticket: ${money(focusRatios.ticket)}`,
      `Growth mensual: ${focusRatios.growthPct || 0}% · ${focusRatios.mesActual || "-"} vs ${focusRatios.mesAnterior || "-"}`,
      `Facturación proveedor: ${money(focusTotals.sales || 0)} · Bultos proveedor: ${decimalNumber(focusTotals.units || 0)}`,
      `Cobertura clientes último mes: ${focusRatios.coberturaClientesActualPct || 0}%`,
    ];
    document.getElementById("ratiosCards").innerHTML = items.map((item) => `<div class="insight-item">${item}</div>`).join("");
    return;
  }

  if (title) {
    title.textContent = "Ratios y potencial";
  }

  const frequencyDetail = `Frecuencia de compra: ${decimalNumber(ratios.purchaseFrequencyMonthly)} pedidos por cliente comprador por mes (${intNumber(ratios.totalOrders)} pedidos / ${intNumber(ratios.periodMonths)} meses / ${intNumber(ratios.buyingClients)} clientes compradores)`;
  const universeFrequencyDetail = `Frecuencia sobre padrón: ${decimalNumber(ratios.purchaseFrequencyUniverseMonthly)} pedidos por cliente por mes (${intNumber(ratios.totalClients)} clientes del universo)`;
  const monthlyOrdersDetail = `Pedidos mensuales del período: ${decimalNumber(ratios.ordersPerMonth)} (${intNumber(ratios.totalOrders)} pedidos / ${intNumber(ratios.periodMonths)} meses)`;

  const items = mode === "mixed" ? [
    `Bultos por cliente: ${decimalNumber(ratios.unitsPerClient)}`,
    `Venta por cliente: ${money(ratios.salesPerClient)}`,
    frequencyDetail,
    universeFrequencyDetail,
    monthlyOrdersDetail,
    `Bultos por vendedor: ${decimalNumber(ratios.unitsPerSeller)}`,
    `Venta por vendedor: ${money(ratios.salesPerSeller)}`,
    `Pedidos por vendedor: ${ratios.ordersPerSeller}`,
    `Bultos por pedido: ${decimalNumber(ratios.avgUnitsPerOrder)} · Ticket promedio ${money(ratios.avgOrderValue)}`,
    `Precio promedio por bulto: ${money(ratios.avgUnitPrice)}`,
    `Top 3 fuerzas de ventas: ${ratios.top3SalesForcesSharePct}% de la venta`,
    `Top 3 vendedores: ${ratios.top3SellersSharePct}% de la venta`,
    `Profundidad media: ${ratios.familyBreadthPerClient} familias por cliente`,
    `Potencial recuperación de cartera: ${money(opportunities.recoverDormantSales)}`,
    `Potencial cross-sell: ${money(opportunities.crossSellPotential)}`,
    `Potencial optimización de rutas: ${money(opportunities.routeOptimizationPotential)}`,
    `Potencial total estimado: ${money(opportunities.totalPotential)}`,
  ] : mode === "units" ? [
    `Bultos por cliente: ${decimalNumber(ratios.unitsPerClient)}`,
    frequencyDetail,
    universeFrequencyDetail,
    monthlyOrdersDetail,
    `Bultos por vendedor: ${decimalNumber(ratios.unitsPerSeller)}`,
    `Clientes por vendedor: ${ratios.clientsPerSeller}`,
    `Pedidos por vendedor: ${ratios.ordersPerSeller}`,
    `Bultos por pedido: ${decimalNumber(ratios.avgUnitsPerOrder)}`,
    `Precio promedio por bulto: ${money(ratios.avgUnitPrice)}`,
    `Venta por cliente: ${money(ratios.salesPerClient)}`,
    `Venta por vendedor: ${money(ratios.salesPerSeller)}`,
    `Top 3 fuerzas de ventas: ${ratios.top3SalesForcesSharePct}% de la venta`,
    `Top 3 vendedores: ${ratios.top3SellersSharePct}% de la venta`,
    `Marca líder: ${ratios.topBrandSharePct}% de la venta`,
    `Unidad de negocio líder: ${ratios.topBusinessUnitSharePct}% de la venta`,
    `Canal líder: ${ratios.topChannelSharePct}% de la venta`,
    `Profundidad media: ${ratios.familyBreadthPerClient} familias por cliente`,
    `Potencial recuperación de cartera: ${money(opportunities.recoverDormantSales)}`,
    `Potencial cross-sell: ${money(opportunities.crossSellPotential)}`,
    `Potencial optimización de rutas: ${money(opportunities.routeOptimizationPotential)}`,
    `Potencial foco en mix: ${money(opportunities.familyFocusPotential)}`,
    `Potencial total estimado: ${money(opportunities.totalPotential)}`,
  ] : [
    `Venta por cliente: ${money(ratios.salesPerClient)}`,
    frequencyDetail,
    universeFrequencyDetail,
    monthlyOrdersDetail,
    `Venta por vendedor: ${money(ratios.salesPerSeller)}`,
    `Clientes por vendedor: ${ratios.clientsPerSeller}`,
    `Pedidos por vendedor: ${ratios.ordersPerSeller}`,
    `Ticket promedio: ${money(ratios.avgOrderValue)}`,
    `Unidades por pedido: ${decimalNumber(ratios.avgUnitsPerOrder)}`,
    `Precio promedio por unidad: ${money(ratios.avgUnitPrice)}`,
    `Top 3 fuerzas de ventas: ${ratios.top3SalesForcesSharePct}% de la venta`,
    `Top 3 vendedores: ${ratios.top3SellersSharePct}% de la venta`,
    `Marca líder: ${ratios.topBrandSharePct}% de la venta`,
    `Unidad de negocio líder: ${ratios.topBusinessUnitSharePct}% de la venta`,
    `Canal líder: ${ratios.topChannelSharePct}% de la venta`,
    `Profundidad media: ${ratios.familyBreadthPerClient} familias por cliente`,
    `Potencial recuperación de cartera: ${money(opportunities.recoverDormantSales)}`,
    `Potencial cross-sell: ${money(opportunities.crossSellPotential)}`,
    `Potencial optimización de rutas: ${money(opportunities.routeOptimizationPotential)}`,
    `Potencial foco en mix: ${money(opportunities.familyFocusPotential)}`,
    `Potencial total estimado: ${money(opportunities.totalPotential)}`,
  ];
  document.getElementById("ratiosCards").innerHTML = items.map((item) => `<div class="insight-item">${item}</div>`).join("");
}

function renderForecast(forecast, meta = {}, mode = "mixed") {
  const nextLabel = forecast.nextWindowLabel || meta?.comparison?.comparisonLabel || "la próxima ventana";
  const items = mode === "mixed" ? [
    `Base media del período: ${decimalNumber(forecast.baseMonthlyUnits)} bultos`,
    `Base media del período: ${money(forecast.baseMonthlySales)}`,
    `Tendencia reciente: ${forecast.unitsTrendPct}% en bultos · ${forecast.trendPct}% en pesos`,
    `Proyección ${nextLabel.toLowerCase()}: ${decimalNumber(forecast.projectedQuarterUnits)} bultos`,
    `Proyección ${nextLabel.toLowerCase()}: ${money(forecast.projectedQuarterSales)}`,
  ] : mode === "units" ? [
    `Base media del período: ${decimalNumber(forecast.baseMonthlyUnits)} bultos`,
    `Tendencia reciente: ${forecast.unitsTrendPct}%`,
    `Proyección ${nextLabel.toLowerCase()}: ${decimalNumber(forecast.projectedQuarterUnits)} bultos`,
    `Referencia monetaria: ${money(forecast.projectedQuarterSales)}`,
  ] : [
    `Base media del período: ${money(forecast.baseMonthlySales)}`,
    `Tendencia reciente: ${forecast.trendPct}%`,
    `Proyección ${nextLabel.toLowerCase()}: ${money(forecast.projectedQuarterSales)}`,
  ];
  document.getElementById("forecastCards").innerHTML = items.map((item) => `<div class="insight-item">${item}</div>`).join("");
}

function renderCharts(charts, supplierFocus = {}, mode = "mixed") {
  const unitsMode = mode !== "sales";
  const formatter = unitsMode ? decimalNumber : money;
  document.getElementById("chartSalesByMonthTitle").textContent = unitsMode ? "Bultos por mes" : "Ventas por mes";
  document.getElementById("chartForecastTitle").textContent = unitsMode ? "Proyección en bultos" : "Proyección en pesos";
  document.getElementById("chartZoneSalesTitle").textContent = unitsMode ? "Bultos por fuerza de ventas" : "Ventas por fuerza de ventas";
  document.getElementById("chartSellerSalesTitle").textContent = unitsMode ? "Bultos por vendedor" : "Ticket por vendedor";
  document.getElementById("chartFamilyMomentumTitle").textContent = unitsMode ? "Bultos por marca" : "Ventas por marca";
  document.getElementById("chartCoverageTitle").textContent = unitsMode ? "Bultos por canal" : "Ventas por canal";
  document.getElementById("chartSalesByMonth").innerHTML = renderLineChart(unitsMode ? (charts.salesByMonthUnits || []) : (charts.salesByMonthMoney || []), formatter);
  document.getElementById("chartForecast").innerHTML = renderBars(unitsMode ? (charts.salesForecastUnits || []) : (charts.salesForecastMoney || []), formatter);
  document.getElementById("chartZoneSales").innerHTML = renderBars(unitsMode ? (charts.salesForceUnits || []) : (charts.salesForceMoney || []), formatter);
  document.getElementById("chartSellerSales").innerHTML = renderBars(unitsMode ? (charts.sellerProductivityUnits || []) : (charts.sellerProductivityMoney || []), formatter);
  document.getElementById("chartFamilyMomentum").innerHTML = renderBars(unitsMode ? (charts.brandUnits || []) : (charts.brandMoney || []), formatter);
  document.getElementById("chartCoverage").innerHTML = renderBars(unitsMode ? (charts.channelUnits || []) : (charts.channelMoney || []), formatter);
  renderSupplierFocusChart(supplierFocus);
}

function renderSupplierFocusChart(supplierFocus = {}) {
  const title = document.getElementById("chartSupplierCoverageTitle");
  const host = document.getElementById("chartSupplierCoverage");
  if (!title || !host) {
    return;
  }
  if (!supplierFocus?.selected) {
    title.textContent = "Cobertura mensual de clientes";
    host.innerHTML = "<div class='muted'>Elegí un proveedor foco para comparar compradores mensuales contra el total de clientes activos.</div>";
    return;
  }
  const monthlyCoverage = supplierFocus.monthlyCoverage || [];
  title.textContent = `Cobertura mensual de clientes · ${supplierFocus.label}`;
  if (!monthlyCoverage.length) {
    host.innerHTML = "<div class='muted'>No hay datos mensuales suficientes para este proveedor en el período seleccionado.</div>";
    return;
  }
  host.innerHTML = renderMultiLineChart([
    {
      label: `${supplierFocus.label} compradores`,
      color: "#0f766e",
      data: monthlyCoverage.map((item) => ({ x: item.label, y: item.supplierClients })),
    },
    {
      label: "Clientes activos totales",
      color: "#b45309",
      dashed: true,
      data: monthlyCoverage.map((item) => ({ x: item.label, y: item.totalActiveClients })),
    },
  ], intNumber);
}

function renderRankings(rankings, mode = "mixed") {
  const unitsMode = mode !== "sales";
  const titleMode = mode === "mixed" ? "mixed" : (unitsMode ? "units" : "sales");
  renderRankingGroup("clientsRankings", [
    { title: titleMode === "mixed" ? "Clientes destacados" : unitsMode ? "Clientes con más volumen" : "Clientes más valiosos", items: unitsMode ? rankings.positiveClientsByUnits : rankings.positiveClientsBySales, formatter: (item) => clientLine(item, titleMode) },
    { title: "Clientes en riesgo", items: rankings.riskClients, formatter: clientRiskLine },
  ]);
  renderRankingGroup("commercialRankings", [
    { title: titleMode === "mixed" ? "Vendedores destacados" : unitsMode ? "Vendedores con más volumen" : "Vendedores destacados", items: unitsMode ? rankings.topSellersByUnits : rankings.topSellersBySales, formatter: (item) => sellerLine(item, titleMode) },
    { title: titleMode === "mixed" ? "Productividad comercial" : unitsMode ? "Mejor volumen por pedido" : "Vendedores más rentables", items: unitsMode ? rankings.productiveSellersByUnits : rankings.productiveSellersBySales, formatter: (item) => productiveSellerLine(item, titleMode) },
    { title: "Fuerzas de ventas principales", items: unitsMode ? rankings.topSalesForcesByUnits : rankings.topSalesForcesBySales, formatter: (item) => salesForceLine(item, titleMode) },
    { title: "Rutas principales", items: unitsMode ? rankings.topRoutesByUnits : rankings.topRoutesBySales, formatter: (item) => routeLine(item, titleMode) },
  ]);
  renderRankingGroup("mixRankings", [
    { title: "Marcas líderes", items: unitsMode ? rankings.topBrandsByUnits : rankings.topBrandsBySales, formatter: (item) => brandLine(item, titleMode) },
    { title: "Unidades de negocio", items: unitsMode ? rankings.topBusinessUnitsByUnits : rankings.topBusinessUnitsBySales, formatter: (item) => businessUnitLine(item, titleMode) },
    { title: "Canales principales", items: unitsMode ? rankings.topChannelsByUnits : rankings.topChannelsBySales, formatter: (item) => channelLine(item, titleMode) },
    { title: "Potencial", items: [{ text: rankings.opportunityHeadline }], formatter: genericLine },
  ]);
}

function bindFilterEvents() {
  // Cambio de pestaña
  document.querySelectorAll("[data-filter-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeFilterTab = btn.dataset.filterTab;
      // Re-render solo el panel sin volver a llamar analyze
      const meta = document.getElementById("resultsFilters")._lastMeta;
      if (meta) renderFilterPanel(meta);
    });
  });
  document.querySelectorAll("[data-selectable-kind='filter'][data-selectable-role='option']").forEach((input) => {
    input.addEventListener("change", () => {
      toggleSelectableValue("filter", input.dataset.selectableField, input.value, input.checked);
    });
  });

  document.querySelectorAll("[data-selectable-kind='filter'][data-selectable-role='action']").forEach((button) => {
    button.addEventListener("click", () => {
      applySelectableAction("filter", button.dataset.selectableField, button.dataset.selectableAction);
      const meta = document.getElementById("resultsFilters")._lastMeta;
      if (meta) {
        renderFilterPanel(meta);
      }
    });
  });

  document.querySelectorAll("[data-selectable-role='search']").forEach((input) => {
    input.addEventListener("input", () => {
      state.filterSearch[buildSelectableKey(input.dataset.selectableKind, input.dataset.selectableField)] = input.value || "";
      if (input.dataset.selectableKind === "prefilter") {
        renderDatasetConfigs();
        return;
      }
      const meta = document.getElementById("resultsFilters")._lastMeta;
      if (meta) {
        renderFilterPanel(meta);
      }
    });
  });

  const applyButton = document.getElementById("applyFiltersBtn");
  if (applyButton) {
    applyButton.addEventListener("click", () => analyze().catch(showError));
  }

  const clearButton = document.getElementById("clearFiltersBtn");
  if (clearButton) {
    clearButton.addEventListener("click", () => {
      state.filters.selected = normalizeSelectedFilters({}, state.filters.available);
      analyze().catch(showError);
    });
  }
}

function renderRankingGroup(targetId, groups) {
  document.getElementById(targetId).innerHTML = groups.map((group) => `
    <div>
      <h3>${group.title}</h3>
      ${(group.items || []).slice(0, 8).map((item) => `<div class="ranking-item">${group.formatter(item)}</div>`).join("")}
    </div>
  `).join("");
}

function toggleSelectableValue(kind, field, rawValue, checked) {
  const selected = kind === "prefilter" ? state.prefilters.selected : state.filters.selected;
  const available = kind === "prefilter" ? state.prefilters.available : state.filters.available;
  const parsedValue = parseFilterValue(field, rawValue);
  const current = [...getSelectedValuesForField(field, selected, available)];
  const next = checked
    ? current.some((value) => isSameFilterValue(field, value, parsedValue)) ? current : [...current, parsedValue]
    : current.filter((value) => !isSameFilterValue(field, value, parsedValue));
  selected[field] = next;
}

function applySelectableAction(kind, field, action) {
  const selected = kind === "prefilter" ? state.prefilters.selected : state.filters.selected;
  const available = kind === "prefilter" ? state.prefilters.available : state.filters.available;
  if (action === "all") {
    selected[field] = getAllFieldValues(field, available);
    return;
  }
  selected[field] = [];
}

function buildSelectableKey(kind, field) {
  return `${kind}:${field}`;
}

function filterSelectableOptions(options, searchValue) {
  const query = normalizeText(searchValue || "");
  if (!query) {
    return options || [];
  }
  return (options || []).filter((option) => normalizeText(`${option.label} ${option.value}`).includes(query));
}

function renderLineChart(items, formatter) {
  if (!items.length) {
    return "<div class='muted'>Sin datos para graficar.</div>";
  }
  const width = 640;
  const height = 200;
  const maxValue = Math.max(...items.map((item) => item.value), 1);
  const stepX = items.length === 1 ? width / 2 : width / (items.length - 1);
  const points = items.map((item, index) => {
    const x = index * stepX;
    const y = height - (item.value / maxValue) * (height - 20) - 10;
    return `${x},${y}`;
  }).join(" ");
  const last = items[items.length - 1];
  const axisLabels = buildLineAxisLabels(items);
  return `
    <div class="chart-shell">
      <div class="line-chart">
        <svg viewBox="0 0 ${width} ${height}">
          <polyline fill="none" stroke="#0f766e" stroke-width="4" points="${points}" />
          ${items.map((item, index) => {
            const x = index * stepX;
            const y = height - (item.value / maxValue) * (height - 20) - 10;
            return `<circle cx="${x}" cy="${y}" r="4" fill="#0f766e"></circle>`;
          }).join("")}
        </svg>
      </div>
      <div class="line-axis">
        ${axisLabels}
      </div>
      <div class="line-axis-summary">${last.label} · ${formatter(last.value)}</div>
    </div>
  `;
}

function renderMultiLineChart(seriesList, formatter) {
  const normalizedSeries = (seriesList || []).filter((serie) => (serie.data || []).length);
  if (!normalizedSeries.length) {
    return "<div class='muted'>Sin datos para graficar.</div>";
  }
  const labels = [];
  normalizedSeries.forEach((serie) => {
    (serie.data || []).forEach((point) => {
      if (!labels.includes(point.x)) {
        labels.push(point.x);
      }
    });
  });
  if (!labels.length) {
    return "<div class='muted'>Sin datos para graficar.</div>";
  }
  const width = 640;
  const height = 220;
  const stepX = labels.length === 1 ? width / 2 : width / (labels.length - 1);
  const seriesMaps = normalizedSeries.map((serie) => ({
    ...serie,
    map: new Map((serie.data || []).map((point) => [point.x, Number(point.y || 0)])),
  }));
  const maxValue = Math.max(
    1,
    ...seriesMaps.flatMap((serie) => labels.map((label) => Math.abs(serie.map.get(label) || 0))),
  );
  const lines = seriesMaps.map((serie) => {
    const color = serie.color || "#0f766e";
    const points = labels.map((label, index) => {
      const x = index * stepX;
      const value = serie.map.get(label) || 0;
      const y = height - (Math.abs(value) / maxValue) * (height - 28) - 14;
      return { x, y, value };
    });
    return {
      label: serie.label,
      color,
      dashed: !!serie.dashed,
      points,
      polyline: points.map((point) => `${point.x},${point.y}`).join(" "),
    };
  });
  const axisLabels = buildLineAxisLabels(labels.map((label, index) => ({ label, value: index })));
  const legend = lines.map((line) => `
    <span class="line-legend-item">
      <span class="line-legend-swatch${line.dashed ? " dashed" : ""}" style="--line-color:${line.color}"></span>
      ${escapeHtml(line.label)}
    </span>
  `).join("");
  return `
    <div class="chart-shell">
      <div class="line-legend">${legend}</div>
      <div class="line-chart">
        <svg viewBox="0 0 ${width} ${height}">
          ${lines.map((line) => `<polyline fill="none" stroke="${line.color}" stroke-width="3" ${line.dashed ? 'stroke-dasharray="8 6"' : ""} points="${line.polyline}" />`).join("")}
          ${lines.map((line) => line.points.map((point) => `<circle cx="${point.x}" cy="${point.y}" r="3.5" fill="${line.color}"></circle>`).join("")).join("")}
        </svg>
      </div>
      <div class="line-axis">${axisLabels}</div>
    </div>
  `;
}

function buildLineAxisLabels(items) {
  const maxLabels = 8;
  const labels = [];
  if (items.length <= maxLabels) {
    items.forEach((item, index) => labels.push({ item, index }));
  } else {
    const step = Math.max(1, Math.ceil((items.length - 1) / (maxLabels - 1)));
    for (let index = 0; index < items.length; index += step) {
      labels.push({ item: items[index], index });
    }
    const lastIndex = items.length - 1;
    if (labels[labels.length - 1]?.index !== lastIndex) {
      labels.push({ item: items[lastIndex], index: lastIndex });
    }
  }
  const totalSteps = Math.max(items.length - 1, 1);
  return labels.map(({ item, index }) => `
    <span class="line-axis-label" style="left:${(index / totalSteps) * 100}%">${escapeHtml(item.label)}</span>
  `).join("");
}

function renderBars(items, formatter, forceNegative = false, diverging = false) {
  if (!items.length) {
    return "<div class='muted'>Sin datos para graficar.</div>";
  }
  const maxValue = Math.max(...items.map((item) => Math.abs(item.value)), 1);
  return `
    <div class="bars">
      ${items.slice(0, 8).map((item) => {
        const width = Math.max(Math.abs(item.value) / maxValue * 100, 2);
        const negative = forceNegative ? false : diverging && item.value < 0;
        return `
          <div class="bar-row">
            <div class="bar-label">
              <span>${escapeHtml(item.label)}</span>
              <span>${formatter(item.value)}</span>
            </div>
            <div class="bar-track">
              <div class="bar-fill ${negative ? "negative" : ""}" style="width:${width}%"></div>
            </div>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function clientLine(item, metricMode = "units") {
  const detail = metricMode === "sales"
    ? `${money(item.sales12m)}`
    : metricMode === "mixed"
      ? `${decimalNumber(item.quantity12m || 0)} bultos · ${money(item.sales12m)}`
      : `${decimalNumber(item.quantity12m || 0)} bultos`;
  return `<strong>${escapeHtml(item.client)}</strong><br><span class="muted">${detail} · ${item.families} familias · ${item.sales_force}</span>`;
}

function clientRiskLine(item) {
  return `<strong>${escapeHtml(item.client)}</strong><br><span class="muted">${item.status} · ${money(item.salesHistory)} histórico · ${item.recencyDays} días sin compra</span>`;
}

function sellerLine(item, metricMode = "units") {
  const head = metricMode === "sales"
    ? money(item.sales)
    : metricMode === "mixed"
      ? `${decimalNumber(item.quantity || 0)} bultos · ${money(item.sales)}`
      : `${decimalNumber(item.quantity || 0)} bultos`;
  const tail = metricMode === "sales"
    ? `${money(item.avgOrderValue || 0)} ticket`
    : metricMode === "mixed"
      ? `${decimalNumber(item.avgUnitsPerOrder || 0)} por pedido · ${money(item.avgOrderValue || 0)} ticket`
      : `${decimalNumber(item.avgUnitsPerOrder || 0)} por pedido`;
  return `<strong>${escapeHtml(item.seller)}</strong><br><span class="muted">${head} · ${item.clients} clientes · ${tail}</span>`;
}

function productiveSellerLine(item, metricMode = "units") {
  const head = metricMode === "sales"
    ? `${money(item.avgOrderValue || 0)} por pedido`
    : metricMode === "mixed"
      ? `${decimalNumber(item.avgUnitsPerOrder || 0)} bultos por pedido · ${money(item.avgOrderValue || 0)} por pedido`
      : `${decimalNumber(item.avgUnitsPerOrder || 0)} bultos por pedido`;
  const tail = metricMode === "sales"
    ? money(item.sales)
    : metricMode === "mixed"
      ? `${decimalNumber(item.quantity || 0)} bultos · ${money(item.sales)}`
      : `${decimalNumber(item.quantity || 0)} bultos`;
  return `<strong>${escapeHtml(item.seller)}</strong><br><span class="muted">${head} · ${item.orders} pedidos · ${tail}</span>`;
}

function salesForceLine(item, metricMode = "units") {
  const detail = metricMode === "sales"
    ? money(item.sales)
    : metricMode === "mixed"
      ? `${decimalNumber(item.quantity || 0)} bultos · ${money(item.sales)}`
      : `${decimalNumber(item.quantity || 0)} bultos`;
  return `<strong>${escapeHtml(item.sales_force)}</strong><br><span class="muted">${detail} · ${item.clients} clientes</span>`;
}

function routeLine(item, metricMode = "units") {
  const head = metricMode === "sales"
    ? money(item.sales)
    : metricMode === "mixed"
      ? `${decimalNumber(item.quantity || 0)} bultos · ${money(item.sales)}`
      : `${decimalNumber(item.quantity || 0)} bultos`;
  const tail = metricMode === "sales"
    ? `${money(item.avgOrderValue || 0)} ticket`
    : metricMode === "mixed"
      ? `${decimalNumber(item.avgUnitsPerOrder || 0)} por pedido · ${money(item.avgOrderValue || 0)} ticket`
      : `${decimalNumber(item.avgUnitsPerOrder || 0)} por pedido`;
  return `<strong>${escapeHtml(item.route_description)}</strong><br><span class="muted">${head} · ${item.clients} clientes · ${tail}</span>`;
}

function brandLine(item, metricMode = "units") {
  const head = metricMode === "sales"
    ? money(item.sales)
    : metricMode === "mixed"
      ? `${decimalNumber(item.quantity || 0)} bultos · ${money(item.sales)}`
      : `${decimalNumber(item.quantity || 0)} bultos`;
  const tail = metricMode === "sales"
    ? `${money(item.avgOrderValue || 0)} ticket`
    : metricMode === "mixed"
      ? `${decimalNumber(item.avgUnitsPerOrder || 0)} por pedido · ${money(item.avgOrderValue || 0)} ticket`
      : `${decimalNumber(item.avgUnitsPerOrder || 0)} por pedido`;
  return `<strong>${escapeHtml(item.brand)}</strong><br><span class="muted">${head} · ${item.clients} clientes · ${tail}</span>`;
}

function businessUnitLine(item, metricMode = "units") {
  const detail = metricMode === "sales"
    ? money(item.sales)
    : metricMode === "mixed"
      ? `${decimalNumber(item.quantity || 0)} bultos · ${money(item.sales)}`
      : `${decimalNumber(item.quantity || 0)} bultos`;
  return `<strong>${escapeHtml(item.business_unit)}</strong><br><span class="muted">${detail} · ${item.orders} pedidos</span>`;
}

function channelLine(item, metricMode = "units") {
  const head = metricMode === "sales"
    ? money(item.sales)
    : metricMode === "mixed"
      ? `${decimalNumber(item.quantity || 0)} bultos · ${money(item.sales)}`
      : `${decimalNumber(item.quantity || 0)} bultos`;
  const tail = metricMode === "sales"
    ? `${money(item.avgOrderValue || 0)} ticket`
    : metricMode === "mixed"
      ? `${decimalNumber(item.avgUnitsPerOrder || 0)} por pedido · ${money(item.avgOrderValue || 0)} ticket`
      : `${decimalNumber(item.avgUnitsPerOrder || 0)} por pedido`;
  return `<strong>${escapeHtml(item.channel)}</strong><br><span class="muted">${head} · ${item.clients} clientes · ${tail}</span>`;
}

function genericLine(item) {
  return `<strong>${escapeHtml(item.text)}</strong>`;
}

function serializeFilters() {
  const filters = {};
  if (state.datasets.sales.sourceMode !== "files") {
    Object.entries(state.prefilters.selected || {}).forEach(([field, values]) => {
      if (!isAllSelectedForField(field, values, state.prefilters.available)) {
        filters[field] = values;
      }
    });
  }
  Object.entries(state.filters.selected || {}).forEach(([field, values]) => {
    if (!isAllSelectedForField(field, values, state.filters.available)) {
      filters[field] = values;
    }
  });
  const supplierFocus = normalizeSupplierFocusSelection();
  if (supplierFocus) {
    filters.supplier = [supplierFocus];
  }
  return filters;
}

function normalizeSelectedFilters(selected, available) {
  const normalized = {};
  Object.entries(available || {}).forEach(([field, config]) => {
    const options = config?.options || [];
    const fallback = options.map((option) => parseFilterValue(field, option.value));
    if (!Object.prototype.hasOwnProperty.call(selected || {}, field)) {
      normalized[field] = fallback;
      return;
    }
    const rawValues = Array.isArray(selected[field]) ? selected[field] : [selected[field]];
    const validValues = rawValues
      .filter((value) => options.some((option) => isSameFilterValue(field, option.value, value)))
      .map((value) => parseFilterValue(field, value));
    normalized[field] = dedupeFilterValues(field, validValues);
  });
  return normalized;
}

function getAllFieldValues(field, available) {
  return (available?.[field]?.options || []).map((option) => parseFilterValue(field, option.value));
}

function getSelectedValuesForField(field, selected, available) {
  if (Object.prototype.hasOwnProperty.call(selected || {}, field)) {
    return Array.isArray(selected[field]) ? selected[field] : [selected[field]];
  }
  return getAllFieldValues(field, available);
}

function dedupeFilterValues(field, values) {
  const deduped = [];
  (values || []).forEach((value) => {
    if (!deduped.some((current) => isSameFilterValue(field, current, value))) {
      deduped.push(parseFilterValue(field, value));
    }
  });
  return deduped;
}

function isAllSelectedForField(field, values, available) {
  const selectedValues = Array.isArray(values) ? values : [];
  const allValues = getAllFieldValues(field, available);
  if (!allValues.length) {
    return false;
  }
  if (selectedValues.length !== allValues.length) {
    return false;
  }
  return allValues.every((value) => selectedValues.some((selected) => isSameFilterValue(field, selected, value)));
}

function isFieldConstrained(field, selected, available) {
  const values = getSelectedValuesForField(field, selected, available);
  return !isAllSelectedForField(field, values, available);
}

function countConstrainedFields(selected, available) {
  return Object.keys(available || {}).filter((field) => isFieldConstrained(field, selected, available)).length;
}

function parseFilterValue(field, value) {
  if (field === "year" || field === "month") {
    return Number(value);
  }
  return String(value);
}

function isSelectedFilterValue(field, selectedValues, value) {
  return (selectedValues || []).some((selected) => isSameFilterValue(field, selected, value));
}

function isSameFilterValue(field, left, right) {
  if (field === "year" || field === "month") {
    return Number(left) === Number(right);
  }
  return String(left) === String(right);
}

function lookupFileName(path) {
  return (state.files.find((file) => file.path === path) || {}).name || String(path || "").split("/").pop();
}

function money(value) {
  return new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS", maximumFractionDigits: 0 }).format(value || 0);
}

function intNumber(value) {
  return new Intl.NumberFormat("es-AR", { maximumFractionDigits: 0 }).format(value || 0);
}

function decimalNumber(value) {
  return new Intl.NumberFormat("es-AR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value || 0);
}

function rangeDays(fechaDesde, fechaHasta) {
  if (!fechaDesde || !fechaHasta) {
    return 0;
  }
  const start = new Date(`${fechaDesde}T00:00:00`);
  const end = new Date(`${fechaHasta}T00:00:00`);
  const diff = end.getTime() - start.getTime();
  if (!Number.isFinite(diff) || diff < 0) {
    return 0;
  }
  return Math.floor(diff / 86400000) + 1;
}

function progressBucket(days) {
  if (days <= 0) return "generic";
  if (days <= 31) return "short";
  if (days <= 120) return "medium";
  if (days <= 365) return "long";
  return "xlong";
}

function buildProgressKey(options = {}) {
  const operation = options.operationType || "generic";
  const bucket = progressBucket(rangeDays(options.fechaDesde, options.fechaHasta));
  const mode = options.sourceMode || "generic";
  return `${operation}:${mode}:${bucket}`;
}

function readProgressHistory() {
  try {
    return JSON.parse(localStorage.getItem("progressHistory") || "{}");
  } catch (_) {
    return {};
  }
}

function writeProgressHistory(history) {
  try {
    localStorage.setItem("progressHistory", JSON.stringify(history));
  } catch (_) {
    // Ignorar si no hay storage disponible.
  }
}

function estimateProgressSeconds(options = {}) {
  const history = readProgressHistory();
  const key = buildProgressKey(options);
  const saved = history[key];
  if (saved?.avgSeconds) {
    return Math.max(3, Math.round(saved.avgSeconds));
  }
  const days = rangeDays(options.fechaDesde, options.fechaHasta);
  if ((options.operationType || "") === "sync") {
    if (days <= 31) return 18;
    if (days <= 90) return 45;
    if (days <= 180) return 80;
    if (days <= 365) return 150;
    return 240;
  }
  if ((options.operationType || "") === "dynamic") {
    if (days <= 31) return 8;
    if (days <= 90) return 14;
    if (days <= 180) return 22;
    if (days <= 365) return 35;
    return 55;
  }
  if ((options.operationType || "") === "analyze") {
    if (days <= 31) return 10;
    if (days <= 90) return 18;
    if (days <= 180) return 28;
    if (days <= 365) return 45;
    return 70;
  }
  return 8;
}

function recordProgressDuration(options = {}, elapsedSeconds = 0) {
  if (!elapsedSeconds || elapsedSeconds < 1) {
    return;
  }
  const history = readProgressHistory();
  const key = buildProgressKey(options);
  const current = history[key] || { avgSeconds: 0, samples: 0 };
  const samples = Math.min((current.samples || 0) + 1, 8);
  const avgSeconds = current.avgSeconds
    ? ((current.avgSeconds * Math.max((current.samples || 0), 1)) + elapsedSeconds) / Math.max(samples, 1)
    : elapsedSeconds;
  history[key] = { avgSeconds: Math.round(avgSeconds), samples };
  writeProgressHistory(history);
}

function formatElapsed(seconds) {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return mins > 0 ? `${mins}m ${String(secs).padStart(2, "0")}s` : `${secs}s`;
}

function setStatus(text) {
  document.getElementById("status").textContent = text;
}

function updateProgressMeta() {
  const meta = document.getElementById("progressMeta");
  if (!meta || !progressContext) {
    return;
  }
  const elapsedSeconds = Math.max(0, Math.round((Date.now() - progressContext.startedAt) / 1000));
  const estimateSeconds = progressContext.estimateSeconds || 0;
  if (!estimateSeconds) {
    meta.textContent = `Tiempo transcurrido: ${formatElapsed(elapsedSeconds)}.`;
    return;
  }
  const remainingSeconds = Math.max(0, estimateSeconds - elapsedSeconds);
  if (elapsedSeconds > estimateSeconds * 1.2) {
    meta.textContent = `Tiempo transcurrido: ${formatElapsed(elapsedSeconds)}. Está demorando más de lo habitual, pero puede seguir procesando en segundo plano.`;
    return;
  }
  meta.textContent = `Tiempo transcurrido: ${formatElapsed(elapsedSeconds)} · restante aprox.: ${formatElapsed(remainingSeconds)}.`;
}

function showProgressModal(text, options = {}) {
  const modal = document.getElementById("progressModal");
  const message = document.getElementById("progressMessage");
  const estimateSeconds = estimateProgressSeconds(options);
  progressContext = {
    startedAt: Date.now(),
    estimateSeconds,
    options,
  };
  message.textContent = text || "Procesando...";
  updateProgressMeta();
  if (progressTicker) {
    clearInterval(progressTicker);
  }
  progressTicker = setInterval(updateProgressMeta, 1000);
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
}

function hideProgressModal() {
  const modal = document.getElementById("progressModal");
  if (progressTicker) {
    clearInterval(progressTicker);
    progressTicker = null;
  }
  if (progressContext) {
    const elapsedSeconds = Math.max(1, Math.round((Date.now() - progressContext.startedAt) / 1000));
    recordProgressDuration(progressContext.options, elapsedSeconds);
    progressContext = null;
  }
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
}

async function withProgress(message, runner, options = {}) {
  progressDepth += 1;
  showProgressModal(message, options);
  try {
    return await runner();
  } finally {
    progressDepth = Math.max(0, progressDepth - 1);
    if (!progressDepth) {
      hideProgressModal();
    }
  }
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function normalizeText(value) {
  return String(value ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

function isEmptyMapping(mapping) {
  return !Object.values(mapping || {}).some((value) => value !== null && value !== undefined && value !== "");
}

function showError(error) {
  progressDepth = 0;
  hideProgressModal();
  setStatus(error.message || "Error inesperado");
}

function handleScrollAction(action) {
  const step = Math.max(window.innerHeight * 0.82, 320);
  if (action === "top") {
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  if (action === "bottom") {
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });
    return;
  }
  if (action === "page-up") {
    window.scrollBy({ top: -step, behavior: "smooth" });
    return;
  }
  if (action === "page-down") {
    window.scrollBy({ top: step, behavior: "smooth" });
  }
}

function bindClick(id, handler) {
  const element = document.getElementById(id);
  if (element) {
    element.addEventListener("click", handler);
  }
}

function bindChange(id, handler) {
  const element = document.getElementById(id);
  if (element) {
    element.addEventListener("change", handler);
  }
}

// ─── Fase 6: Motor dinámico de análisis ─────────────────────────────────────

function renderDynamicPanel(data) {
  state.dynamic.tasks        = data.availableAnalyses || [];
  state.dynamic.selectedTaskId = null;
  const sum = data.insightsSummary || {};
  document.getElementById("dynSummaryLine").textContent =
    `${sum.total || 0} insights detectados — ${state.dynamic.tasks.length} tipos de análisis disponibles`;
  renderDynTaskHelper();
  renderDynTaskSelector();
  renderDynKpis(data.kpiSet || {});
  renderDynInsights(data.dynamicInsights || [], sum);
  document.getElementById("dynSemaphores").innerHTML = renderDynSemaphoresHtml(data.kpiSet?.semaphores || []);
  document.getElementById("dynViz").innerHTML = "<div class='muted'>Seleccioná un tipo de análisis y ejecutá para ver la visualización.</div>";
  document.getElementById("dynResults").classList.remove("hidden");
}

function renderDynTaskSelector() {
  const container = document.getElementById("dynTaskSelector");
  const tasks = state.dynamic.tasks;
  if (!tasks.length) {
    container.innerHTML = "<div class='muted'>No hay análisis disponibles para los datos actuales.</div>";
    renderDynTaskHelper();
    return;
  }
  const domainOrder = ["tiempo","cartera","territorio","producto","canal","margen","fuerza","general"];
  const domainLabels = {
    tiempo: "Tiempo", cartera: "Cartera", territorio: "Territorio",
    producto: "Producto", canal: "Canal", margen: "Margen",
    fuerza: "Fuerza de ventas", general: "General",
  };
  const byDomain = {};
  tasks.forEach((t) => {
    const d = t.domain || "general";
    if (!byDomain[d]) byDomain[d] = [];
    byDomain[d].push(t);
  });
  container.innerHTML = domainOrder.filter((d) => byDomain[d]).map((d) => `
    <div class="dyn-domain-group">
      <span class="dyn-domain-label">${escapeHtml(domainLabels[d] || d)}</span>
      ${byDomain[d].map((t) => `
        <button class="dyn-task-pill${t.id === state.dynamic.selectedTaskId ? " active" : ""}" data-task-id="${t.id}">
          ${escapeHtml(t.label)}
        </button>
      `).join("")}
    </div>
  `).join("");
  container.querySelectorAll("[data-task-id]").forEach((btn) => {
    btn.addEventListener("click", () => onSelectDynTask(btn.dataset.taskId));
  });
}

function onSelectDynTask(taskId) {
  state.dynamic.selectedTaskId = taskId;
  renderDynTaskSelector();
  renderDynTaskHelper(taskId);
  const task = state.dynamic.tasks.find((t) => t.id === taskId);
  if (!task) return;
  const comboRow    = document.getElementById("dynComboRow");
  const comboSelect = document.getElementById("dynComboSelect");
  comboRow.classList.remove("hidden");
  if (task.combos && task.combos.length) {
    comboSelect.style.display = "";
    comboSelect.innerHTML = task.combos.map((c, i) =>
      `<option value="${i}">${escapeHtml(c.label)}</option>`
    ).join("");
  } else {
    comboSelect.style.display = "none";
    comboSelect.innerHTML = "";
  }
}

function renderDynTaskHelper(taskId = state.dynamic.selectedTaskId) {
  const container = document.getElementById("dynTaskHelp");
  if (!container) {
    return;
  }
  const task = state.dynamic.tasks.find((item) => item.id === taskId);
  if (!task) {
    container.innerHTML = `
      <div class="dyn-help-card">
        <strong>Cómo elegir un análisis puntual</strong>
        <div class="muted">Primero corré "Actualizar informe". Después elegí una pastilla según la pregunta de negocio que quieras responder. Si no sabés cuál usar, empezá por evolución temporal, ranking o recurrencia/churn.</div>
      </div>
    `;
    return;
  }
  const playbook = ANALYSIS_PLAYBOOK[task.id] || {};
  const combos = (task.combos || []).slice(0, 4).map((combo) => combo.label).filter(Boolean);
  container.innerHTML = `
    <div class="dyn-help-card">
      <div class="dyn-help-header">
        <strong>${escapeHtml(task.label)}</strong>
        <span class="pill">Prioridad ${task.priority || "-"}</span>
      </div>
      <div class="muted">${escapeHtml(playbook.summary || "Análisis puntual sobre el recorte actual del informe.")}</div>
      <div class="dyn-help-grid">
        <div>
          <div class="dyn-help-title">Cuándo usarlo</div>
          <div class="muted">${escapeHtml(playbook.when || "Cuando quieras profundizar una dimensión concreta del negocio.")}</div>
        </div>
        <div>
          <div class="dyn-help-title">Preguntas que responde</div>
          <div class="guide-examples">
            ${(playbook.questions || ["¿Qué está pasando en esta dimensión del negocio?"]).map((item) => `<span class="guide-chip">${escapeHtml(item)}</span>`).join("")}
          </div>
        </div>
        <div>
          <div class="dyn-help-title">Aperturas disponibles</div>
          <div class="guide-examples">
            ${(combos.length ? combos : ["Sin apertura adicional"]).map((item) => `<span class="guide-chip">${escapeHtml(item)}</span>`).join("")}
          </div>
        </div>
      </div>
    </div>
  `;
}

async function runDynamicTask() {
  const taskId = state.dynamic.selectedTaskId;
  if (!taskId) { setStatus("Seleccioná un tipo de análisis primero."); return; }
  const task = state.dynamic.tasks.find((t) => t.id === taskId);
  const comboSelect = document.getElementById("dynComboSelect");
  const comboIdx = Number(comboSelect.value || 0);
  const combo = (task?.combos || [])[comboIdx] || null;

  const datasetsPayload = {};
  if (state.datasets.sales.sourceMode === "erp" || state.datasets.sales.sourceMode === "mongo" || state.datasets.sales.sourceMode === "clickhouse" || state.datasets.sales.sourceMode === "auto") {
    const { fechaDesde, fechaHasta } = state.datasets.sales.erp;
    if (!fechaDesde || !fechaHasta) {
      setStatus(isBiSurface ? "Elegí fecha desde y fecha hasta para consultar la base comercial." : "Elegí fecha desde y fecha hasta para consultar ventas ERP.");
      return;
    }
    datasetsPayload.sales = {
      source: state.datasets.sales.sourceMode,
      fechaDesde,
      fechaHasta,
      erp: { enabled: state.datasets.sales.sourceMode === "erp", fechaDesde, fechaHasta },
    };
  } else {
    const salesSources = state.datasets.sales.sources
      .filter((s) => s.file && s.sheet)
      .map((s) => ({ file: s.file, sheet: s.sheet, headerRow: Number(s.headerRow || 0) }));
    if (!salesSources.length) { setStatus("No hay datos cargados para analizar."); return; }
    datasetsPayload.sales = { source: "files", sources: salesSources, mapping: state.datasets.sales.mapping };
  }

  for (const dt of datasetOrder.filter((d) => d !== "sales")) {
    const ds = state.datasets[dt];
    if (ds.file && ds.sheet) {
      datasetsPayload[dt] = { file: ds.file, sheet: ds.sheet, headerRow: Number(ds.headerRow || 0), mapping: ds.mapping };
    }
  }

  const btn = document.getElementById("dynRunBtn");
  btn.disabled = true;
  setStatus(`Ejecutando: ${task?.label || taskId}...`);
  try {
    const data = await withProgress(`Ejecutando: ${task?.label || taskId}...`, () => api("/api/analyze-dynamic", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ datasets: datasetsPayload, filters: serializeFilters(), supplierFocus: normalizeSupplierFocusSelection(), task_id: taskId, combo }),
    }), {
      operationType: "dynamic",
      fechaDesde: state.datasets.sales.erp?.fechaDesde,
      fechaHasta: state.datasets.sales.erp?.fechaHasta,
      sourceMode: state.datasets.sales.sourceMode,
    });
    renderDynKpis(data.kpiSet || {});
    renderDynInsights(data.insights || [], data.insightsSummary || {});
    document.getElementById("dynSemaphores").innerHTML = renderDynSemaphoresHtml(data.kpiSet?.semaphores || []);
    renderVizSpec(data.vizSpec, "dynViz");
    setStatus(`Análisis: ${task?.label || taskId}${combo ? " — " + combo.label : ""}`);
  } catch (err) {
    showError(err);
  } finally {
    btn.disabled = false;
  }
}

function renderDynKpis(kpiSet) {
  const kpis = kpiSet.kpis || [];
  document.getElementById("dynKpiCards").innerHTML = kpis.slice(0, 8).map((k) => {
    const fmtVal = fmtKpiValue(k.value, k.format);
    const delta  = k.delta != null
      ? `<div class="dyn-kpi-delta ${k.delta_dir || "flat"}">${k.delta >= 0 ? "▲" : "▼"} ${Math.abs(k.delta).toFixed(1)}${k.format === "pct" ? "%" : ""}</div>`
      : "";
    return `
      <div class="dyn-kpi-card">
        <div class="dyn-kpi-label">${escapeHtml(k.label)}</div>
        <div class="dyn-kpi-value">${escapeHtml(fmtVal)}</div>
        ${delta}
        <div class="dyn-kpi-context muted">${escapeHtml(k.context || "")}</div>
      </div>`;
  }).join("");
}

function renderDynInsights(insights, summary) {
  const typeLabels = { alert: "Alerta", opportunity: "Oportunidad", positive: "Positivo", trend: "Tendencia", context: "Contexto" };
  document.getElementById("dynInsightsList").innerHTML = (insights || []).map((ins) => `
    <div class="insight-item">
      <span class="ins-badge ins-${ins.type}">${escapeHtml(typeLabels[ins.type] || ins.type)}</span>
      ${escapeHtml(ins.text)}
    </div>
  `).join("") || "<div class='muted'>Sin insights para los datos actuales.</div>";
}

function renderDynSemaphoresHtml(semaphores) {
  return (semaphores || []).map((s) => `
    <div class="semaphore">
      <div><span class="dot ${s.color}"></span><strong>${escapeHtml(s.label)}</strong></div>
      <div class="muted">${escapeHtml(s.detail)}</div>
    </div>
  `).join("") || "<div class='muted'>Sin semáforos.</div>";
}

function fmtKpiValue(value, format) {
  if (format === "money") return money(value);
  if (format === "pct")   return `${Number(value).toFixed(1)}%`;
  if (format === "int")   return Number(value).toLocaleString("es-AR");
  return escapeHtml(String(value ?? ""));
}

function renderVizSpec(spec, containerId) {
  const container = document.getElementById(containerId);
  if (!spec || spec.empty || !spec.series?.length) {
    container.innerHTML = "<div class='muted'>Sin datos suficientes para visualizar.</div>";
    return;
  }
  const fmt = spec.format === "money" ? money
    : spec.format === "pct" ? (v) => `${Number(v).toFixed(1)}%`
    : (v) => Number(v).toLocaleString("es-AR");
  const main = spec.series[0];
  let vizHtml = "";
  if (spec.type === "line" || spec.type === "area") {
    vizHtml = spec.series.length > 1
      ? renderMultiLineChart(spec.series, fmt)
      : renderLineChart((main?.data || []).map((p) => ({ label: p.x, value: p.y })), fmt);
  } else if (spec.type === "bar" || spec.type === "stacked_bar") {
    const items = (main?.data || []).map((p) => ({ label: p.x, value: p.y }));
    vizHtml = renderBars(items, fmt);
  } else if (spec.type === "bar_horizontal") {
    vizHtml = renderBarHorizontal(main?.data || [], fmt);
  } else if (spec.type === "donut") {
    vizHtml = renderDonutSVG(main?.data || [], fmt);
  } else if (spec.type === "scatter") {
    vizHtml = renderScatterSVG(main?.data || [], spec.axes || {});
  } else {
    const items = (main?.data || []).map((p) => ({ label: p.x, value: p.y }));
    vizHtml = renderBars(items, fmt);
  }
  container.innerHTML = `<div class="dyn-viz-title">${escapeHtml(spec.title)}</div>` + vizHtml;
}

function renderBarHorizontal(data, formatter) {
  if (!data.length) return "<div class='muted'>Sin datos.</div>";
  const maxVal = Math.max(...data.map((p) => Math.abs(p.y)), 1);
  return `<div class="bars">${data.slice(0, 10).map((p) => {
    const w = Math.max(Math.abs(p.y) / maxVal * 100, 2);
    const extra = p.z != null ? ` <span class="muted">(${typeof p.z === "number" ? p.z.toFixed(1) + "%" : p.z})</span>` : "";
    return `
      <div class="bar-row">
        <div class="bar-label">
          <span>${escapeHtml(String(p.x))}${extra}</span>
          <span>${formatter(p.y)}</span>
        </div>
        <div class="bar-track"><div class="bar-fill" style="width:${w}%"></div></div>
      </div>`;
  }).join("")}</div>`;
}

function renderDonutSVG(data, formatter) {
  if (!data.length) return "<div class='muted'>Sin datos.</div>";
  const total = data.reduce((s, p) => s + Math.abs(p.y), 0) || 1;
  const cx = 100, cy = 100, r = 70, sw = 36;
  const circ = 2 * Math.PI * r;
  const colors = ["#0f766e","#1d4ed8","#b45309","#7c3aed","#be185d","#15803d","#b91c1c","#0369a1"];
  let offset = 0;
  const slices = data.map((p, i) => {
    const pct  = Math.abs(p.y) / total;
    const dash = pct * circ;
    const gap  = circ - dash;
    const slice = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${colors[i % colors.length]}" stroke-width="${sw}" stroke-dasharray="${dash.toFixed(2)} ${gap.toFixed(2)}" stroke-dashoffset="${(-offset * circ).toFixed(2)}" transform="rotate(-90 ${cx} ${cy})" />`;
    offset += pct;
    return slice;
  });
  const legend = data.slice(0, 8).map((p, i) => `
    <div class="donut-legend-item">
      <span class="donut-dot" style="background:${colors[i % colors.length]}"></span>
      <span>${escapeHtml(String(p.x))}</span>
      <span class="muted">${formatter(p.y)} · ${((Math.abs(p.y)/total)*100).toFixed(1)}%</span>
    </div>`).join("");
  return `<div class="donut-wrap"><svg viewBox="0 0 200 200" class="donut-svg">${slices.join("")}</svg><div class="donut-legend">${legend}</div></div>`;
}

function renderScatterSVG(data, axes) {
  if (!data.length) return "<div class='muted'>Sin datos.</div>";
  const W = 500, H = 300, pad = 45;
  const xs = data.map((p) => p.x), ys = data.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs) || 1;
  const minY = Math.min(0, Math.min(...ys)), maxY = Math.max(...ys) || 1;
  const sx = (v) => pad + (v - minX) / (maxX - minX || 1) * (W - pad * 2);
  const sy = (v) => H - pad - (v - minY) / (maxY - minY || 1) * (H - pad * 2);
  const dots = data.slice(0, 20).map((p) => {
    const lbl = p.z ? escapeHtml(String(p.z)) : "";
    return `<g><circle cx="${sx(p.x).toFixed(1)}" cy="${sy(p.y).toFixed(1)}" r="5" fill="#0f766e" fill-opacity="0.75" />${lbl ? `<title>${lbl}</title>` : ""}</g>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" class="scatter-svg">
    <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${H-pad}" stroke="#ccc" />
    <line x1="${pad}" y1="${H-pad}" x2="${W-pad}" y2="${H-pad}" stroke="#ccc" />
    <text x="${W/2}" y="${H-8}" text-anchor="middle" font-size="11" fill="#999">${escapeHtml(axes.x || "X")}</text>
    <text x="12" y="${H/2}" text-anchor="middle" font-size="11" fill="#999" transform="rotate(-90 12 ${H/2})">${escapeHtml(axes.y || "Y")}</text>
    ${dots.join("")}
  </svg>`;
}

// ─────────────────────────────────────────────────────────────────────────────

bindClick("uploadBtn", () => uploadFiles().catch(showError));
bindClick("clearUploadsBtn", () => clearUploads().catch(showError));
bindClick("refreshFiles", () => boot().catch(showError));
bindClick("analyzeBtn", () => analyze().catch(showError));
bindClick("dynRunBtn", () => runDynamicTask().catch(showError));
bindClick("refreshAdminErrors", () => refreshAdminErrors().catch(showError));
bindChange("libraryScope", async (event) => {
  state.scope = event.target.value;
  await boot().catch(showError);
});
document.querySelectorAll("[data-scroll-action]").forEach((button) => {
  button.addEventListener("click", () => handleScrollAction(button.dataset.scrollAction));
});
window.addEventListener("resize", () => {
  Object.values(dashboardState.charts).forEach((chart) => {
    try {
      chart.resize();
    } catch (_) {
      // Ignorar instancias ya descartadas.
    }
  });
});

boot().catch(showError);
