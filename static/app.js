const state = {
  files: [],
  schema: {},
  datasets: {},
  preview: { datasetType: "sales", sourceIndex: 0 },
  scope: "uploads",
  filters: { available: {}, selected: {} },
  dynamic: { tasks: [], selectedTaskId: null },
};

const datasetOrder = ["sales", "articles", "routes", "sellers"];
const filterOrder = ["year", "month", "family", "line", "supplier", "sales_force", "route_description", "seller_name", "channel"];

const filterGroups = [
  { id: "tiempo",     label: "Período",       fields: ["year", "month"] },
  { id: "producto",   label: "Producto",      fields: ["family", "line", "supplier"] },
  { id: "comercial",  label: "Comercial",     fields: ["sales_force", "route_description", "seller_name"] },
  { id: "canal",      label: "Canal",         fields: ["channel"] },
];

let activeFilterTab = "tiempo";

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
  const [filesResponse, schemaResponse, sessionResponse] = await Promise.all([
    api(`/api/files?scope=${encodeURIComponent(state.scope)}`),
    api("/api/datasets"),
    api("/api/session").catch(() => ({ datasets: null })),
  ]);
  state.files = filesResponse.files || [];
  state.schema = schemaResponse.datasets || {};
  initializeDatasets();

  if (sessionResponse.datasets) {
    await restoreSession(sessionResponse.datasets);
    setStatus("Sesión anterior restaurada. Podés analizar directamente o cambiar los archivos.");
  } else {
    setStatus("Cargá uno o más archivos de venta por cliente y luego los maestros.");
  }

  renderFileLibrary();
  renderDatasetConfigs();
  renderPreview();
}

async function restoreSession(saved) {
  // Ventas (múltiples fuentes)
  const savedSales = saved.sales;
  if (savedSales?.sources?.length) {
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
  renderDynamicPanel(data);
}

function renderFilterPanel(meta) {
  const container = document.getElementById("resultsFilters");
  container._lastMeta = meta;  // guardado para re-render al cambiar tab
  const activeCount = Object.keys(state.filters.selected || {}).length;
  const summary = activeCount
    ? `${meta.activeFilterSummary}. ${meta.rowsAnalyzed} registros analizados de ${meta.rowsUniverse} disponibles.`
    : `Sin filtros aplicados. ${meta.rowsAnalyzed} registros disponibles.`;

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
    const activeInGroup = g.fields.filter((f) => state.filters.selected[f]?.length).length;
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
      <div class="filter-tab-hint muted">Ctrl/Cmd + clic para selección múltiple</div>
      <div class="filter-fields-row">${fieldsHtml}</div>
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
  // Cambio de pestaña
  document.querySelectorAll("[data-filter-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeFilterTab = btn.dataset.filterTab;
      // Re-render solo el panel sin volver a llamar analyze
      const meta = document.getElementById("resultsFilters")._lastMeta;
      if (meta) renderFilterPanel(meta);
    });
  });
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

// ─── Fase 6: Motor dinámico de análisis ─────────────────────────────────────

function renderDynamicPanel(data) {
  state.dynamic.tasks        = data.availableAnalyses || [];
  state.dynamic.selectedTaskId = null;
  const sum = data.insightsSummary || {};
  document.getElementById("dynSummaryLine").textContent =
    `${sum.total || 0} insights detectados — ${state.dynamic.tasks.length} tipos de análisis disponibles`;
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

async function runDynamicTask() {
  const taskId = state.dynamic.selectedTaskId;
  if (!taskId) { setStatus("Seleccioná un tipo de análisis primero."); return; }
  const task = state.dynamic.tasks.find((t) => t.id === taskId);
  const comboSelect = document.getElementById("dynComboSelect");
  const comboIdx = Number(comboSelect.value || 0);
  const combo = (task?.combos || [])[comboIdx] || null;

  const salesSources = state.datasets.sales.sources
    .filter((s) => s.file && s.sheet)
    .map((s) => ({ file: s.file, sheet: s.sheet, headerRow: Number(s.headerRow || 0) }));
  if (!salesSources.length) { setStatus("No hay datos cargados para analizar."); return; }

  const datasetsPayload = { sales: { sources: salesSources, mapping: state.datasets.sales.mapping } };
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
    const data = await api("/api/analyze-dynamic", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ datasets: datasetsPayload, filters: serializeFilters(), task_id: taskId, combo }),
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
        <div class="dyn-kpi-value">${fmtVal}</div>
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
    const items = (main?.data || []).map((p) => ({ label: p.x, value: p.y }));
    vizHtml = renderLineChart(items, fmt);
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

document.getElementById("uploadBtn").addEventListener("click", () => uploadFiles().catch(showError));
document.getElementById("clearUploadsBtn").addEventListener("click", () => clearUploads().catch(showError));
document.getElementById("refreshFiles").addEventListener("click", () => boot().catch(showError));
document.getElementById("analyzeBtn").addEventListener("click", () => analyze().catch(showError));
document.getElementById("dynRunBtn").addEventListener("click", () => runDynamicTask().catch(showError));
document.getElementById("libraryScope").addEventListener("change", async (event) => {
  state.scope = event.target.value;
  await boot().catch(showError);
});

boot().catch(showError);
