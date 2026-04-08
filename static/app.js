const state = {
  files: [],
  schema: {},
  datasets: {},
  preview: { datasetType: "sales", sourceIndex: 0 },
  scope: "uploads",
  filters: { available: {}, selected: {} },
};

const datasetOrder = ["sales", "articles", "routes", "sellers"];
const filterOrder = ["year", "month", "family", "line", "supplier", "sales_force", "route_description", "seller_name", "channel"];

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Error inesperado");
  }
  return data;
}

async function boot() {
  setStatus("Preparando estructura de análisis...");
  const [filesResponse, schemaResponse] = await Promise.all([
    api(`/api/files?scope=${encodeURIComponent(state.scope)}`),
    api("/api/datasets"),
  ]);
  state.files = filesResponse.files || [];
  state.schema = schemaResponse.datasets || {};
  initializeDatasets();
  renderFileLibrary();
  renderDatasetConfigs();
  renderPreview();
  setStatus("Cargá uno o más archivos de venta por cliente y luego los maestros.");
}

function initializeDatasets() {
  state.datasets = {
    sales: {
      sources: [createSource()],
      mapping: {},
      mappingSuggestions: {},
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

function renderDatasetConfigs() {
  const container = document.getElementById("datasetConfigs");
  container.innerHTML = datasetOrder.map((datasetType) => {
    return datasetType === "sales"
      ? renderSalesDatasetCard()
      : renderSingleDatasetCard(datasetType);
  }).join("");

  bindDatasetEvents(container);
}

function renderSalesDatasetCard() {
  const schema = state.schema.sales;
  const dataset = state.datasets.sales;
  return `
    <div class="source-card ${state.preview.datasetType === "sales" ? "active" : ""}">
      <div class="subpanel-header">
        <div>
          <h3>${escapeHtml(schema.label)}</h3>
          <div class="muted">Podés cargar varios archivos, por ejemplo uno por año.</div>
        </div>
        <button data-sales-add="1">Agregar archivo</button>
      </div>

      <div class="sales-source-list">
        ${dataset.sources.map((source, index) => renderSalesSource(source, index)).join("")}
      </div>

      <div class="mapping-grid compact">
        ${renderSalesMappingFields()}
      </div>
    </div>
  `;
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

function renderPreview() {
  const meta = document.getElementById("previewMeta");
  const container = document.getElementById("previewContainer");
  const target = getPreviewTarget();

  if (!target || !target.preview) {
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
  const response = await api("/api/upload", { method: "POST", body: form });
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
  const response = await api("/api/clear-uploads", { method: "POST" });
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

  const payload = {
    datasets: {
      sales: {
        sources: salesSources,
        mapping: state.datasets.sales.mapping,
      },
    },
    filters: serializeFilters(),
  };

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

  setStatus("Relacionando ventas con maestros y recalculando el informe...");
  const data = await api("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  renderResults(data);
  setStatus(`Análisis completo: ${data.meta.rowsAnalyzed} registros analizados entre ${data.meta.periodStart} y ${data.meta.periodEnd}.`);
}

function renderResults(data) {
  document.getElementById("results").classList.remove("hidden");
  state.filters.available = data.availableFilters || {};
  state.filters.selected = normalizeSelectedFilters(data.appliedFilters || {}, state.filters.available);
  renderFilterPanel(data.meta);
  renderSummaryCards(data.summary);
  renderSemaphores(data.semaphores);
  renderCoverage(data.coverage, data.meta.datasets);
  renderInsights(data.insights);
  renderActionPlan(data.actionPlan);
  renderRatios(data.ratios, data.opportunities);
  renderForecast(data.forecast);
  renderCharts(data.charts);
  renderRankings(data.rankings);
}

function renderFilterPanel(meta) {
  const container = document.getElementById("resultsFilters");
  const activeCount = Object.keys(state.filters.selected || {}).length;
  const summary = activeCount
    ? `${meta.activeFilterSummary}. ${meta.rowsAnalyzed} registros analizados de ${meta.rowsUniverse} disponibles.`
    : `Sin filtros aplicados. ${meta.rowsAnalyzed} registros disponibles para el informe.`;
  const hint = "Podés combinar varios valores por filtro con Ctrl/Cmd + clic.";
  const filtersMarkup = filterOrder
    .filter((field) => state.filters.available[field]?.options?.length)
    .map((field) => renderFilterField(field, state.filters.available[field], state.filters.selected[field] || []))
    .join("");

  container.innerHTML = `
    <div class="subpanel-header">
      <div>
        <h2>Filtros del informe</h2>
        <div class="muted">${escapeHtml(summary)}</div>
        <div class="muted">${hint}</div>
      </div>
      <div class="button-row">
        <button id="applyFiltersBtn">Aplicar filtros</button>
        <button id="clearFiltersBtn">Limpiar filtros</button>
      </div>
    </div>
    <div class="mapping-grid compact filter-grid">
      ${filtersMarkup || "<div class='muted'>Todavía no hay dimensiones filtrables para este análisis.</div>"}
    </div>
  `;
  bindFilterEvents();
}

function renderFilterField(field, config, selectedValues) {
  const size = Math.min(Math.max(config.options.length, 4), 8);
  return `
    <label>
      ${escapeHtml(config.label)}
      <select class="filter-select" data-filter-field="${field}" multiple size="${size}">
        ${config.options.map((option) => `
          <option value="${escapeHtml(option.value)}" ${isSelectedFilterValue(field, selectedValues, option.value) ? "selected" : ""}>
            ${escapeHtml(option.label)} (${option.count})
          </option>
        `).join("")}
      </select>
    </label>
  `;
}

function renderSummaryCards(summary) {
  const cards = [
    ["Venta 90 días", money(summary.salesCurrent), `vs período previo ${summary.salesGrowthPct}%`],
    ["Clientes activos", `${summary.activeClients}`, `${summary.activeRatioPct}% del padrón`],
    ["Ticket promedio", money(summary.avgTicket), `${summary.recurringRatioPct}% recurrencia`],
    ["Cobertura rutas", `${summary.routeCoveragePct}%`, `${summary.salesForceCount} fuerzas de ventas · ${summary.routeCount} rutas`],
    ["Cobertura artículos", `${summary.articleCoveragePct}%`, `${summary.familyCount} familias activas`],
    ["Cobertura vendedores", `${summary.sellerCoveragePct}%`, `${summary.sellerCount} vendedores`],
    ["Dormidos", `${summary.dormantClients}`, `${summary.reactivableClients} reactivables · ${summary.lostClients} perdidos`],
    ["Concentración top 10", `${summary.top10SharePct}%`, "sobre venta 12 meses"],
  ];
  document.getElementById("summaryCards").innerHTML = cards.map(([label, value, sub]) => `
    <article class="card">
      <div class="label">${label}</div>
      <div class="value">${value}</div>
      <div class="sub">${sub}</div>
    </article>
  `).join("");
}

function renderSemaphores(items) {
  document.getElementById("semaphores").innerHTML = (items || []).map((item) => `
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

function renderInsights(items) {
  document.getElementById("insights").innerHTML = (items || []).map((item) => `<div class="insight-item">${item}</div>`).join("");
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

function renderRatios(ratios, opportunities) {
  const items = [
    `Venta por cliente: ${money(ratios.salesPerClient)}`,
    `Venta por vendedor: ${money(ratios.salesPerSeller)}`,
    `Clientes por vendedor: ${ratios.clientsPerSeller}`,
    `Top 3 fuerzas de ventas: ${ratios.top3SalesForcesSharePct}% de la venta`,
    `Top 3 vendedores: ${ratios.top3SellersSharePct}% de la venta`,
    `Profundidad media: ${ratios.familyBreadthPerClient} familias por cliente`,
    `Potencial recuperación de cartera: ${money(opportunities.recoverDormantSales)}`,
    `Potencial cross-sell: ${money(opportunities.crossSellPotential)}`,
    `Potencial optimización de rutas: ${money(opportunities.routeOptimizationPotential)}`,
    `Potencial foco en mix: ${money(opportunities.familyFocusPotential)}`,
    `Potencial total estimado: ${money(opportunities.totalPotential)}`,
  ];
  document.getElementById("ratiosCards").innerHTML = items.map((item) => `<div class="insight-item">${item}</div>`).join("");
}

function renderForecast(forecast) {
  const items = [
    `Base mensual reciente: ${money(forecast.baseMonthlySales)}`,
    `Tendencia reciente: ${forecast.trendPct}%`,
    `Proyección próximo trimestre: ${money(forecast.projectedQuarterSales)}`,
  ];
  document.getElementById("forecastCards").innerHTML = items.map((item) => `<div class="insight-item">${item}</div>`).join("");
}

function renderCharts(charts) {
  document.getElementById("chartSalesByMonth").innerHTML = renderLineChart(charts.salesByMonth || [], money);
  document.getElementById("chartForecast").innerHTML = renderBars(charts.salesForecast || [], money);
  document.getElementById("chartZoneSales").innerHTML = renderBars(charts.salesForceSales || [], money);
  document.getElementById("chartSellerSales").innerHTML = renderBars(charts.sellerSales || [], money);
  document.getElementById("chartFamilyMomentum").innerHTML = renderBars(charts.familyMomentum || [], (value) => `${value}%`, false, true);
  document.getElementById("chartCoverage").innerHTML = renderBars(charts.coverage || [], (value) => `${value}%`);
}

function renderRankings(rankings) {
  renderRankingGroup("clientsRankings", [
    { title: "Clientes más valiosos", items: rankings.positiveClients, formatter: clientLine },
    { title: "Clientes en riesgo", items: rankings.riskClients, formatter: clientRiskLine },
  ]);
  renderRankingGroup("commercialRankings", [
    { title: "Vendedores destacados", items: rankings.topSellers, formatter: sellerLine },
    { title: "Fuerzas de ventas principales", items: rankings.topSalesForces, formatter: salesForceLine },
    { title: "Rutas débiles", items: rankings.weakRoutes, formatter: routeLine },
  ]);
  renderRankingGroup("mixRankings", [
    { title: "Familias en expansión", items: rankings.expandingFamilies, formatter: familyLine },
    { title: "Familias en retracción", items: rankings.contractingFamilies, formatter: familyLine },
    { title: "Potencial", items: [{ text: rankings.opportunityHeadline }], formatter: genericLine },
  ]);
}

function bindFilterEvents() {
  document.querySelectorAll("[data-filter-field]").forEach((select) => {
    select.addEventListener("change", () => {
      const field = select.dataset.filterField;
      const values = Array.from(select.selectedOptions).map((option) => parseFilterValue(field, option.value));
      if (values.length) {
        state.filters.selected[field] = values;
      } else {
        delete state.filters.selected[field];
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
      state.filters.selected = {};
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
        <span>${items[0].label}</span>
        <span>${last.label} · ${formatter(last.value)}</span>
      </div>
    </div>
  `;
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

function clientLine(item) {
  return `<strong>${escapeHtml(item.client)}</strong><br><span class="muted">${money(item.sales12m)} · ${item.families} familias · ${item.sales_force}</span>`;
}

function clientRiskLine(item) {
  return `<strong>${escapeHtml(item.client)}</strong><br><span class="muted">${item.status} · ${money(item.salesHistory)} histórico · ${item.recencyDays} días sin compra</span>`;
}

function sellerLine(item) {
  return `<strong>${escapeHtml(item.seller)}</strong><br><span class="muted">${money(item.sales)} · ${item.clients} clientes</span>`;
}

function salesForceLine(item) {
  return `<strong>${escapeHtml(item.sales_force)}</strong><br><span class="muted">${money(item.sales)} · ${item.clients} clientes</span>`;
}

function routeLine(item) {
  return `<strong>${escapeHtml(item.route_description)}</strong><br><span class="muted">${money(item.sales)} · ${item.clients} clientes</span>`;
}

function familyLine(item) {
  return `<strong>${escapeHtml(item.family)}</strong><br><span class="muted">${item.growthPct}% · ${money(item.sales)}</span>`;
}

function genericLine(item) {
  return `<strong>${escapeHtml(item.text)}</strong>`;
}

function serializeFilters() {
  const filters = {};
  Object.entries(state.filters.selected || {}).forEach(([field, values]) => {
    if (values?.length) {
      filters[field] = values;
    }
  });
  return filters;
}

function normalizeSelectedFilters(selected, available) {
  const normalized = {};
  Object.entries(selected || {}).forEach(([field, values]) => {
    const options = available[field]?.options || [];
    const validValues = values.filter((value) => options.some((option) => isSameFilterValue(field, option.value, value)));
    if (validValues.length) {
      normalized[field] = validValues.map((value) => parseFilterValue(field, value));
    }
  });
  return normalized;
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

function setStatus(text) {
  document.getElementById("status").textContent = text;
}

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function isEmptyMapping(mapping) {
  return !Object.values(mapping || {}).some((value) => value !== null && value !== undefined && value !== "");
}

function showError(error) {
  setStatus(error.message || "Error inesperado");
}

document.getElementById("uploadBtn").addEventListener("click", () => uploadFiles().catch(showError));
document.getElementById("clearUploadsBtn").addEventListener("click", () => clearUploads().catch(showError));
document.getElementById("refreshFiles").addEventListener("click", () => boot().catch(showError));
document.getElementById("analyzeBtn").addEventListener("click", () => analyze().catch(showError));
document.getElementById("libraryScope").addEventListener("change", async (event) => {
  state.scope = event.target.value;
  await boot().catch(showError);
});

boot().catch(showError);
