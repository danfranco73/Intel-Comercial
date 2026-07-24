# Informe de implementación — FASE 5

Fecha: 23/07/2026  
Rama: `feature/codenoa-sales-coach`

## Resumen

Se evolucionaron los dashboards existentes a Codenoa Sales Coach sin reemplazar
ECharts, Tabulator ni el motor analítico. Se agregaron home gobernado, rankings
multidimensionales, ficha de vendedor, ficha de cliente, evidencia numérica,
comparaciones, objetivos y planes de acción.

No se implementaron IA, motor configurable de comentarios de FASE 6, alertas
persistentes, PDF ni PowerPoint.

## Archivos

| Archivo | Acción | Motivo |
|---|---|---|
| `bi/focus_dashboards.py` | modificado | métricas, rankings y comentarios verificables |
| `analyzer.py` | modificado | venta neta y mix de producto para fichas |
| `sales_coach/services/sales_coach_service.py` | nuevo | contratos de home y fichas |
| `sales_coach/routes/sales_coach_routes.py` | nuevo | APIs autorizadas |
| `sales_coach/routes/registry.py` | modificado | registro central de rutas |
| `sales_coach/server.py` | modificado | páginas navegables protegidas |
| `static/index.html` | modificado | panel dedicado de ficha |
| `static/app.js` | modificado | navegación, tablas y render de evidencia |
| `static/app.css` | modificado | presentación incremental |
| `tests/test_phase5_sales_coach.py` | nuevo | rankings, fichas y aislamiento |

## APIs

| Método | Ruta | Resultado |
|---|---|---|
| GET | `/api/sales-coach/home` | KPIs ejecutivos y focos |
| GET | `/api/sales-coach/sellers` | ranking multidimensional y fórmulas |
| GET | `/api/sales-coach/seller` | ficha individual por `sellerKey` |
| GET | `/api/sales-coach/client` | ficha individual por `clientKey` |

Todas reciben `fechaDesde` y `fechaHasta` y aplican el alcance antes de devolver
resultados.

Páginas protegidas:

- `/sales-coach`;
- `/sales-coach/seller?sellerKey=...`;
- `/sales-coach/client?clientKey=...`.

## Seguridad

- Seller: sólo su ficha y clientes de su cartera.
- Supervisor: sólo vendedores/clientes de su estructura resuelta.
- Director/admin: universo comercial autorizado.
- Las claves de URL nunca amplían el alcance backend.
- Se agregaron pruebas explícitas contra acceso horizontal.

## Base de datos

No se crearon colecciones ni migraciones en esta fase. Se reutilizan:

- ventas normalizadas;
- maestros;
- objetivos de FASE 4;
- frescura de FASE 3.

## Pruebas

Resultado integral: 37 tests aprobados. También pasaron compilación Python,
validación JavaScript y `git diff --check`.

## Riesgos pendientes

- La ficha dedicada carga el rango comparativo y año anterior; conviene crear
  agregados ClickHouse después de validar las fórmulas con negocio.
- Potencial es una heurística explicable, no una predicción.
- Categorías ausentes dependen de cobertura del maestro de artículos.
- Las reglas de comentarios siguen embebidas y deterministas; FASE 6 debe
  versionarlas/configurarlas y resolver contradicciones formalmente.

## Próxima fase

La FASE 6 corresponde al motor configurable, versionado y auditable de
comentarios automáticos sin IA. No fue implementada.
