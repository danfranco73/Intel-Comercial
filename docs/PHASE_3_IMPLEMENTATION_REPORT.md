# Informe de implementación — FASE 3

Fecha: 23/07/2026  
Rama: `feature/codenoa-sales-coach`

## Resumen

Se agregó trazabilidad durable, checkpoints, lease de scheduler, reintentos,
conciliación MongoDB/ClickHouse, healthchecks y frescura visible. Se preservaron
Chess, la normalización, las colecciones actuales, la tabla ClickHouse, los
dashboards y el frontend.

No se implementaron objetivos, alertas comerciales, Sales Coach, IA, PDF,
PowerPoint ni multiempresa.

## Archivos

| Archivo/área | Acción | Motivo |
|---|---|---|
| `sales_coach/repositories/sync_repository.py` | nuevo | corridas, checkpoints y leases |
| `sales_coach/services/reconciliation_service.py` | nuevo | conciliación gobernada |
| `sales_coach/services/sync_service.py` | modificado | ciclo durable y resultado auditable |
| `sales_coach/routes/operations_routes.py` | nuevo | estado, health y conciliación |
| `sales_coach/routes/registry.py` | modificado | permisos de nuevas APIs |
| `clickhouse_client.py` | modificado | agregado server-side, frescura y mutations |
| `scripts/sync_scheduler.py` | nuevo | job desacoplado con reintentos |
| `scripts/erp_sync_range.py` | modificado | CLI dentro del mismo ciclo auditable |
| `scripts/migrate_phase3_sync.py` | nuevo | índices aditivos |
| `static/app.js` | modificado | fecha de actualización y diagnóstico |
| `tests/test_phase3_sync.py` | nuevo | trazabilidad, lease, fallas y conciliación |

## Base de datos

- Nuevas colecciones: `sync_runs`, `sync_checkpoints`, `scheduler_leases`.
- Índices únicos: `sync_runs.run_id`, `sync_checkpoints.job_key`.
- Índices operativos por entidad/estado/fecha y expiración de lease.
- Sin cambios destructivos ni migración de ventas.
- `fact_sales_compact` se conserva.
- La migración fue ejecutada y se verificaron los índices:
  `sync_run_id_unique`, `sync_entity_started`, `sync_status_started`,
  `sync_checkpoint_job_unique` y `scheduler_lease_expiry`.

## Pruebas

```bash
.venv/bin/python -m py_compile app.py *.py bi/*.py scripts/*.py security/*.py \
  sales_coach/*.py sales_coach/routes/*.py sales_coach/services/*.py \
  sales_coach/repositories/*.py sales_coach/schemas/*.py tests/*.py
.venv/bin/python -m pytest -q
node --check static/app.js
node --check static/login.js
git diff --check
```

Resultado: 25 tests aprobados, compilación Python correcta, JavaScript válido y
diff sin errores de whitespace. Persisten únicamente warnings de deprecación de
`mongomock` en Python 3.14 y avisos LF/CRLF del repositorio.

## Seguridad y observabilidad

- APIs de corrida, conciliación y health: sólo `admin`.
- Frescura comercial: autenticada y sin detalle sensible.
- `requested_by`, origen, rango, estado, duración, filas y error por corrida.
- `X-Request-ID` en respuestas y errores operativos.
- No se registran cookies, contraseñas ni credenciales Chess.

## Riesgos pendientes

- Los dashboards de detalle todavía materializan el rango solicitado en Python;
  la conciliación ya agrega dentro de las bases.
- ClickHouse mantiene importes `Float64`; migrar a `Decimal` requiere tabla nueva,
  backfill y comparación dual.
- Las vistas materializadas deben diseñarse con el catálogo formal de métricas,
  no se agregaron prematuramente.
- El scheduler debe ser supervisado por cron/systemd/contenedor en producción.
- Marketing comparte por ahora la frecuencia diaria de maestros.

## Próxima fase

La FASE 4 corresponde al modelo persistente y versionado de objetivos
comerciales. No fue implementada.
