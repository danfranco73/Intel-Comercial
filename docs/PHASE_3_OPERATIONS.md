# Operación de datos — FASE 3

## Flujo preservado

```text
ChessERP
  -> descarga por rangos y chunks
  -> normalización existente
  -> MongoDB (ventana operativa)
  -> ClickHouse (histórico compacto)
  -> conciliación de filas, venta neta y cantidad
```

No se reemplazaron `erp_sales`, `erp_sync_runs` ni
`fact_sales_compact`. La trazabilidad nueva envuelve al pipeline existente.

## Colecciones nuevas

### `sync_runs`

Una fila lógica por ejecución iniciada desde API, CLI o scheduler:

- `run_id`, entidad, origen, usuario y destinos;
- rango solicitado;
- estado `running`, `success`, `warning` o `failed`;
- inicio, fin y duración;
- filas leídas y almacenadas;
- error sin credenciales;
- resultado de conciliación.

### `sync_checkpoints`

Conserva el último estado y rango por `job_key`. Una falla no se interpreta como
cobertura válida.

### `scheduler_leases`

Lease distribuido para impedir dos ejecuciones simultáneas del mismo job. No
contiene datos comerciales.

Aplicar índices:

```bash
.venv/bin/python scripts/migrate_phase3_sync.py
```

La migración sólo crea colecciones/índices al primer uso; no modifica ventas.

## Scheduler desacoplado

Ejecución única, apropiada para cron o un job runner:

```bash
.venv/bin/python scripts/sync_scheduler.py
```

Modo proceso continuo:

```bash
.venv/bin/python scripts/sync_scheduler.py --loop
```

Ejemplo cron cada seis horas:

```cron
0 */6 * * * cd /ruta/GESTION && .venv/bin/python scripts/sync_scheduler.py >> logs/scheduler.log 2>&1
```

Se recomienda cron/systemd en producción: el scheduler no vive dentro del
`ThreadingHTTPServer` y un reinicio web no interrumpe la planificación.

Variables:

| Variable | Propósito | Ejemplo |
|---|---|---|
| `SYNC_SALES_LOOKBACK_DAYS` | ventana móvil de toda corrida (frescura operativa) | `15` |
| `SYNC_SALES_DEEP_LOOKBACK_DAYS` | ventana de la corrida nocturna: recupera NC/devoluciones cargadas tarde contra meses ya sincronizados | `100` |
| `SYNC_SALES_WEEKLY_LOOKBACK_DAYS` | ventana de la corrida nocturna del domingo (barrido amplio, acotado) | `400` |
| `SYNC_SALES_DEEP_HOUR` | hora (UTC) por debajo de la cual una corrida usa ventana profunda | `6` |
| `SYNC_MAX_RETRIES` | intentos por ejecución | `3` |
| `SYNC_RETRY_DELAY_SECONDS` | espera base incremental | `30` |
| `SYNC_SCHEDULER_LEASE_SECONDS` | exclusión entre instancias | `3600` |
| `SYNC_INTERVAL_SECONDS` | intervalo de `--loop` | `21600` |
| `CHESS_ERP_TIMEOUT` | timeout de lectura HTTP contra Chess (subir si aparece `TimeoutError` en el sync) | `300` |
| `CHESS_ERP_SALES_CHUNK_DAYS` | días por página al traer ventas (bajar si hay `TimeoutError`) | `31` |
| `CLICKHOUSE_TIMEOUT` | timeout de conexión/consulta | `15` |
| `CLICKHOUSE_MUTATION_TIMEOUT` | espera máxima de reemplazos por rango | `180` |

La ventana de re-lectura es escalonada (ver `_lookback_days` en
`scripts/sync_scheduler.py`): corta en cada corrida, profunda de noche y muy
amplia la noche del domingo. Con el intervalo por defecto de 6 h siempre cae
exactamente una corrida en la franja nocturna. Motivo: las devoluciones y notas
de crédito de Chess se emiten días o semanas después de la factura; una ventana
fija de pocos días deja los meses viejos sin esas NC y la plataforma queda
inflada frente al ERP.

Ventas, artículos, vendedores y rutas se refrescan en cada corrida programada.
Marketing usa actualmente la misma frecuencia; puede separarse cuando se mida su
costo real contra Chess.

## APIs

Todas requieren autenticación. Las operativas requieren permiso `admin`.

| Método | Ruta | Propósito |
|---|---|---|
| GET | `/api/data/freshness` | fecha y cobertura comercial, sin datos sensibles |
| GET | `/api/sync/runs?limit=50` | historial de corridas |
| GET | `/api/sync/reconciliation?fechaDesde=...&fechaHasta=...` | comparación Mongo/ClickHouse |
| GET | `/api/health` | aplicación, MongoDB, ClickHouse, Chess y scheduler |

## Idempotencia y fallas parciales

- El rango se compacta con claves deterministas.
- MongoDB reemplaza sólo el rango confirmado.
- ClickHouse inserta con `sync_run_id`, elimina versiones anteriores del rango y
  espera las mutations.
- Una respuesta vacía no confirmada aborta sin borrar el rango existente.
- La ventana programada se fuerza para capturar correcciones retroactivas.
- Si una corrida falla, queda `failed`, conserva error/checkpoint y se reintenta.
- Si MongoDB y ClickHouse difieren, la corrida termina `warning`; la divergencia
  queda visible y no se presenta como éxito silencioso.

## Consultas

La conciliación ejecuta agregados en las bases (`count`, venta neta y cantidad);
no carga el histórico completo en memoria. Los dashboards continúan usando la
carga compatible actual. El siguiente paso de optimización será mover cada
dashboard a agregados gobernados y/o vistas materializadas, con migración
versionada, sin retirar la tabla actual.

## Rollback

1. Detener cron, job runner o `sync_scheduler.py --loop`.
2. Volver al código anterior.
3. Conservar `sync_runs`, `sync_checkpoints` y `scheduler_leases`: son aditivas y
   el código anterior las ignora.
4. Si se desea limpieza posterior, exportarlas y eliminarlas manualmente. No es
   necesario tocar `erp_sales`, `erp_sync_runs` ni ClickHouse.
