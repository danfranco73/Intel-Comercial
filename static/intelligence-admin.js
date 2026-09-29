"use strict";
(() => {
  let csrf = "", config = null, editing = null;
  const byId = id => document.getElementById(id);
  const form = byId("depositForm");
  async function api(path, body) {
    const response = await fetch(path, {credentials: "same-origin", method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : {"Content-Type": "application/json", "X-CSRF-Token": csrf},
      body: body === undefined ? undefined : JSON.stringify(body)});
    if (response.status === 401) { window.location.href = "/login"; throw new Error("Sesión vencida"); }
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "No se pudo completar la operación");
    return result;
  }
  function edit(row) {
    editing = row || null; form.reset();
    for (const field of ["deposit_id", "deposit_name", "company", "branch", "location", "deposit_type", "notes"])
      form.elements[field].value = row?.[field] || "";
    for (const field of ["active", "include_in_stock_analysis", "relationships_verified"])
      form.elements[field].checked = row?.[field] === true;
    form.elements.deposit_id.readOnly = Boolean(row);
  }
  async function reload() {
    config = await api("/api/intelligence/deposits");
    byId("catalogStatus").textContent = `Universo: ${config.universe_coverage.status === "verified" ? "validado" : "sin validar"}. Depósitos: ${config.deposits.length}.`;
    const container = byId("catalog"); container.replaceChildren();
    for (const row of config.deposits) {
      const line = document.createElement("p"), label = document.createElement("span"), button = document.createElement("button");
      label.textContent = `${row.deposit_id} · ${row.deposit_name} | Descubierto: ${row.discovered ? "sí" : "no"} | Configurado: ${row.configured ? "sí" : "no"} | Activo: ${row.active === null ? "sin validar" : row.active ? "sí" : "no"} | En análisis: ${row.include_in_stock_analysis ? "sí" : "no"} `;
      button.textContent = "Editar"; button.type = "button"; button.onclick = () => edit(row);
      line.append(label, button); container.append(line);
    }
  }
  async function task(fn, button) {
    byId("message").textContent = "";
    if (button) button.disabled = true;
    try { await fn(); } catch (error) { byId("message").textContent = error.message; }
    finally { if (button) button.disabled = false; }
  }
  form.addEventListener("submit", event => {
    event.preventDefault();
    task(async () => {
      const data = Object.fromEntries(new FormData(form)); data.revision = editing?.revision || null;
      for (const field of ["active", "include_in_stock_analysis", "relationships_verified"]) data[field] = form.elements[field].checked;
      await api("/api/intelligence/deposits", data); edit(null); await reload();
      byId("message").textContent = "Configuración guardada.";
    }, form.querySelector("button[type=submit]"));
  });
  byId("reload").onclick = () => task(reload);
  byId("newDeposit").onclick = () => edit(null);
  for (const [id, verified] of [["certify", true], ["revoke", false]]) byId(id).onclick = () => task(async () => {
    if (verified && !byId("universeConfirmed").checked) throw new Error("Marcá la verificación del universo antes de certificar.");
    await api("/api/intelligence/deposits/universe", {verified, catalog_fingerprint: config.catalog_fingerprint});
    byId("universeConfirmed").checked = false; await reload();
  }, byId(id));
  byId("capture").onclick = () => task(async () => {
    if (!byId("stockDate").value) throw new Error("Seleccioná la fecha de stock.");
    byId("output").textContent = "Capturando depósitos configurados…";
    byId("output").textContent = JSON.stringify(await api("/api/intelligence/stock/sync", {stock_date: byId("stockDate").value}), null, 2);
  }, byId("capture"));
  byId("brief").onclick = () => task(async () => {
    byId("output").textContent = JSON.stringify(await api("/api/intelligence/stock/daily-brief"), null, 2);
  }, byId("brief"));
  task(async () => {
    const me = await api("/api/auth/me"); csrf = me.csrfToken;
    if (me.user.role !== "admin") throw new Error("Esta página requiere administración.");
    await reload();
  });
})();
