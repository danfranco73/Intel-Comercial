# Entrega de Fase 1 — Intelligence Core

> Informe histórico de la entrega inicial. Para el estado congelado, las restricciones
> operativas y la distinción entre implementación y experimentos, consultar
> [CURRENT_STATE.md](CURRENT_STATE.md). Los estados de disponibilidad y tests de este
> documento corresponden a aquella entrega, no a una verificación actual de producción.

Fecha: 29 de septiembre de 2026. Alcance: Intel-Comercial.

## A. Resumen ejecutivo

Se implementó una API independiente de la UI que reutiliza el enriquecimiento,
los cálculos tácticos, el motor determinístico y el servicio de objetivos existentes.
Incluye Sales Daily Brief, drill-down, ingestión propia de stock Chess, snapshots
auditables y administración de depósitos en `/admin/intelligence`.

Chess se utiliza en modo lectura: autenticación y consultas GET. No se implementaron
movimientos, escrituras en Chess, transferencias, agentes LLM, WhatsApp, n8n,
logística ni finanzas. No se modificó MIPedido/TeMando.

La implementación está en el workspace. El proceso web debe cargar esta versión
para servir los endpoints nuevos; no se reinició ni desplegó el servidor productivo.

La validación real devolvió ventas `unavailable`: ClickHouse respondió, pero las
trazas de sincronización no permiten certificar los períodos solicitados. Stock
devuelve `unavailable` porque todavía no hay depósitos habilitados ni snapshots.
Estos estados representan ausencia de cobertura comprobada, no ventas o stock cero.

## B. Archivos creados

- `sales_coach/domain/intelligence.py`: contrato, señales e interfaces de expectativas/cruce.
- `sales_coach/domain/stock.py`: normalización conservadora y preservación de datos fuente.
- `sales_coach/schemas/intelligence.py`: filtros, dimensiones, corte y paginación.
- `sales_coach/repositories/deposit_repository.py`: descubrimiento, configuración, revisiones y certificación.
- `sales_coach/repositories/product_identity_repository.py`: correspondencias versionadas y bloqueo de ambigüedades.
- `sales_coach/repositories/stock_snapshot_repository.py`: cabeceras, observaciones y rechazos.
- `sales_coach/services/intelligence_context_service.py`: contexto comercial y cobertura.
- `sales_coach/services/intelligence_sales_service.py`: brief y drill-down comercial.
- `sales_coach/services/intelligence_stock_service.py`: brief y consulta de observaciones.
- `sales_coach/services/stock_sync_service.py`: captura secuencial de todos los depósitos elegibles.
- `sales_coach/routes/intelligence_routes.py`: adaptadores HTTP.
- `scripts/data/discovered_deposits_phase1.json`: evidencia inicial aprobada, no lista de sincronización.
- `scripts/migrate_intelligence_phase1.py`: aprovisionamiento aditivo e idempotente.
- `scripts/erp_identity_sync.py`: lectura independiente del maestro de artículos para identidades.
- `scripts/erp_stock_sync.py`: captura desde el catálogo administrado.
- `static/intelligence-admin.html` y `static/intelligence-admin.js`: administración.
- `tests/test_intelligence_sales.py`, `tests/test_intelligence_stock.py` y `tests/test_intelligence_http.py`.
- `docs/intelligence/PHASE1.md`: este informe y manual.
- `docs/intelligence/examples/sales-daily-brief.sanitized.json`.
- `docs/intelligence/examples/stock-daily-brief.sanitized.json`.
- `docs/intelligence/examples/stock-items.sanitized.json`.

## C. Archivos modificados

- `erp_client.py`: GET de stock y conservación de IDs físicos/estadísticos del maestro.
- `clickhouse_client.py`: opción de lectura sin inicialización de esquema y resultado vacío explícito.
- `sales_coach/repositories/sales_repository.py`: lectura para intelligence sin escrituras ni DDL.
- `sales_coach/repositories/auth_repository.py`: relación explícita `erp_company_keys` para empresas de acceso.
- `sales_coach/repositories/objective_repository.py`: inicialización de índices opcional.
- `sales_coach/services/objective_service.py`: proveedor de métricas y fecha de corte opcionales; conserva fórmulas.
- `sales_coach/services/sync_service.py`: publicación de identidades al sincronizar artículos.
- `sales_coach/routes/registry.py`: rutas, permisos y CSRF.
- `sales_coach/server.py`: integración de handlers y página de administración.
- `static/admin.html`: enlace al catálogo de depósitos.

Había cambios previos en `static/app.css` y `static/app.js`; esta implementación no
los modificó. No forman parte del cambio de Fase 1.

## D. Migraciones realizadas

Se ejecutó contra `Intel-Comercial`:

```sh
.venv/bin/python scripts/migrate_intelligence_phase1.py
```

Resultado real: 9 descubiertos, 0 configurados, 0 incluidos, universo `unknown`.
Se crearon índices aditivos en depósitos, snapshots, filas, identidades, rechazos
y auditoría. No se borraron colecciones, documentos ni índices existentes.
La reejecución conserva decisiones de configuración ya guardadas.

También se ejecutó:

```sh
.venv/bin/python scripts/erp_identity_sync.py
```

Chess devolvió 2.228 identidades físicas: 1.200 con una relación estadística
explícita y 1.028 sin ella. No se sustituye un ID estadístico ausente por el ID
físico. Las relaciones se publican por versión y los snapshots congelan esa versión.
La validación de cruce comprueba además la unicidad en sentido inverso.

Colecciones nuevas previstas por los repositorios:

- `intelligence_deposits`, `intelligence_settings`, `intelligence_deposit_audits`.
- `intelligence_product_identities`.
- `intelligence_stock_snapshots`, `intelligence_stock_rows`, `intelligence_stock_rejections`.
- `intelligence_stock_leases` para exclusión de capturas simultáneas.

Las colecciones sin documentos se crean cuando corresponde su primera escritura.
No se modificaron tablas de ClickHouse.

## E. Verificaciones

La suite previa tenía 115 tests aprobados. Se ejecutaron pruebas incrementales
de ventas, stock, HTTP, sincronización y objetivos; luego la suite completa.
Resultado final: **136 passed, 237 warnings, 15,76 segundos**, con
`.venv/bin/python -m pytest -q`. Los warnings corresponden a deprecaciones de
dependencias (`mongomock`/`datetime.utcnow` y código de tipo `u`), no a fallos.
Las pruebas específicas nuevas dieron **21 passed**.

Casos cubiertos: permisos y CSRF, filtros que nunca amplían alcance, nombres de
vendedores repetidos, reconciliación de drill-down, clientes sin venta actual,
objetivos con corte explícito, historia incompleta, sincronizaciones con advertencias,
depósitos sin configurar, fallo individual, respuesta vacía, filas inválidas,
duplicados fuente, persistencia idempotente, exclusión de capturas simultáneas,
congelamiento de catálogo/identidades y preservación del último snapshot completo.

Se verificó la sintaxis con `node --check static/intelligence-admin.js` y el diff
con `git diff --check`. La prueba HTTP levanta el servidor real en un puerto local,
con Mongo simulado; no utiliza usuarios ni contraseñas del entorno real.

Se consultaron los servicios contra las fuentes reales, sin publicar cifras de
negocio en estos ejemplos. No se efectuó una captura real de stock: no hay depósitos
configurados para ello. La integración GET se validó mediante mocks y contra el
contrato observado durante la auditoría.

## F. Endpoints y operación

Todos requieren sesión. Ventas exige `commercial.read` y aplica el alcance del
usuario antes del motor analítico. Stock/catálogo requieren rol administrador hasta
contar con permisos fiables por ubicación. Todos los POST exigen `X-CSRF-Token`.

| Método | Ruta | Uso |
| --- | --- | --- |
| GET | `/api/intelligence/sales/daily-brief` | Brief comercial estructurado |
| GET | `/api/intelligence/sales/drilldown` | Desglose paginado, mismo contexto |
| GET | `/api/intelligence/stock/daily-brief` | Captura, cobertura y dimensiones |
| GET | `/api/intelligence/stock/items` | Observaciones y campos fuente |
| GET | `/api/intelligence/deposits` | Catálogo, revisión y cobertura empresarial |
| POST | `/api/intelligence/deposits` | Configurar o agregar depósito |
| POST | `/api/intelligence/deposits/discover` | Importar descubrimientos de `erp_deposits`; body `{}` |
| POST | `/api/intelligence/deposits/universe` | Registrar/retirar certificación del catálogo |
| POST | `/api/intelligence/stock/sync` | Capturar todos los depósitos elegibles |

### Ventas

`as_of=YYYY-MM-DD` usa por defecto ayer en `America/Argentina/Cordoba`.
`dimension` y filtros disponibles: `company`, `structure`, `sales_force`,
`business_unit`, `channel`, `seller`, `route`, `supplier`, `family`, `line`,
`product`, `client`. Los filtros son igualdad exacta; el código para datos sin
clasificar es `__unclassified__`. Estructura/fuerza son alias del esquema comercial
existente. No se inventa otra jerarquía empresarial.

El cliente conserva los filtros al profundizar y cambia `dimension`, por ejemplo:

```text
/api/intelligence/sales/daily-brief?as_of=2026-09-28&dimension=structure
/api/intelligence/sales/drilldown?as_of=2026-09-28&structure=CODIGO&dimension=channel
```

`limit` acepta 1–200; `offset`, 0–1.000.000. `context_id` permite rechazar una
consulta si cambiaron datos, filtros, alcance o cobertura. Al cambiar filtros,
se crea un contexto nuevo; no se debe reutilizar el identificador anterior.

MTD, MoM y YoY alinean día calendario. No están ajustados por días hábiles ni
estacionalidad. Cada ventana lleva cobertura propia; un valor `null` no es cero.
La proyección utiliza el motor lineal existente. El benchmark histórico se distingue
de un objetivo aprobado. Las metas se aplican sólo cuando coinciden empresa,
período y alcance; no se prorratea una meta general sobre permisos restringidos.

Las explicaciones, alertas legacy, oportunidades y planes existentes sólo se exponen
cuando hay cobertura histórica certificada. Las alertas legacy declaran que no están
calibradas por estacionalidad. Las nuevas señales de desvío no inventan umbrales de
severidad, causalidad ni responsables. El presupuesto de preferencias no equivale a
un objetivo aprobado.

### Depósitos y captura

La UI `/admin/intelligence` permite configurar nombre, empresa, sucursal, ubicación,
activo, inclusión, tipo y notas. Una revisión desactualizada se rechaza. Cambiar
empresa/sucursal exige revalidar sus relaciones. La certificación se vincula al
fingerprint del catálogo y se invalida al cambiar la configuración o descubrir
otro depósito. Haber configurado todos los conocidos no prueba por sí solo que no
existan otros: la certificación es una declaración explícita del administrador.

No se encontró un endpoint maestro de depósitos en el contrato Chess inspeccionado.
El descubrimiento proviene de información comercial ya observada, no de un maestro
autoritativo. La configuración se sincroniza independientemente del stock.
El POST `deposits/discover` no consulta Chess ni amplía por sí mismo el universo:
recoge nuevos depósitos de las ventas previamente sincronizadas. También se pueden
agregar depósitos sin ventas desde la UI.

Para capturar:

```json
{"stock_date": "2026-09-29"}
```

O por CLI:

```sh
.venv/bin/python scripts/erp_stock_sync.py --date 2026-09-29
```

La CLI termina con 0 si la ejecución fue completa, 2 si quedó incompleta/no
disponible y 1 si falló la operación. No acepta una lista de depósitos alternativa.
El roster se congela al comenzar: `configured && active && include_in_stock_analysis`.

Cada consulta secuencial utiliza GET `/stock/`, `idDeposito`, `frescura=true` y
`fechaStock=dd/mm/yyyy`. Por depósito guarda inicio, fin, duración, filas recibidas,
válidas, artículos, almacenes observados, errores y cobertura. `attempts` cuenta
invocaciones al extractor; el transporte ERP conserva sus reintentos internos.

Se conservan `idArticulo`, `idDeposito`, `idAlmacen`, `cantBultos`, `cantUnidades`,
`fecha`, `fecVtoLote` y cualquier campo adicional dentro de `raw`.
`fecha` es la fecha de último movimiento; `captured_at` es tiempo real de captura.
No se deduplican filas por artículo/almacén porque podrían representar lotes no
identificados. Las filas inválidas se conservan en cuarentena y bloquean completitud.

Una respuesta vacía es `empty_unconfirmed`, no stock cero. El fallo de un depósito
no impide consultar los restantes, pero impide completar el snapshot general.
La captura seleccionada por defecto es la última completa; si hay un intento más
nuevo incompleto, se informa. `snapshot_id` permite consultar uno concreto.

`stock/items` filtra por `snapshot_id`, `deposit_id`, `warehouse_id`,
`physical_article_id`, `company`, `branch`, más paginación. Empresa/sucursal sólo
filtran relaciones validadas del catálogo congelado. No se usa `idDeposito` como
sucursal. `idAlmacen=0` se conserva sin asignarle una descripción inferida.
No se suman unidades de productos heterogéneos en un total físico general.

`execution_coverage` corresponde a la captura del roster configurado;
`universe_coverage` corresponde a la certificación empresarial. Una puede ser completa
y la otra desconocida. Antigüedad máxima informativa: 24 horas, configurable mediante
`INTELLIGENCE_STOCK_MAX_AGE_HOURS`. El valor debe ser un entero positivo.

## G. Ejemplo sanitizado de Sales Daily Brief

Archivo: [sales-daily-brief.sanitized.json](examples/sales-daily-brief.sanitized.json).
Generado ejecutando el servicio sobre fixtures sintéticos, con cobertura simulada.
Contiene los campos completos del contrato y el drill-down por cliente.
Los importes, nombres y resultados no describen la empresa real.

## H. Ejemplo sanitizado de Stock Daily Brief

Archivos: [stock-daily-brief.sanitized.json](examples/stock-daily-brief.sanitized.json)
y [stock-items.sanitized.json](examples/stock-items.sanitized.json).
Generados sobre Mongo simulado: depósitos ficticios 99001/99002, uno con error.
Muestran ejecución incompleta, universo desconocido, IDs preservados y cruce bloqueado.
No son capturas realizadas en Chess.

## I. Depósitos y cobertura real

| ID | Nombre descubierto |
| --- | --- |
| 1 | CASA CENTRAL |
| 4 | MAYORISTA |
| 5 | DEP PRODUNOA |
| 6 | DEPOSITO PYMES INDEPENDENCIA |
| 7 | DEPOSITO PYMES LUGONES |
| 8 | DEPOSITO PYMES BELGRANO |
| 20 | FRESCO |
| 22 | DEP TP LA BANDA |
| 23 | TP CAMINOCOSTA |

Todos: `discovered=true`, `configured=false`, `active=null`,
`include_in_stock_analysis=false`. Empresa, sucursal, ubicación y tipo no inferidos.
Cobertura empresarial `unknown`; ejecución `not_captured`. Estos nueve registros no
constituyen una declaración de totalidad ni de actividad.

## J. Limitaciones pendientes

- Certificar integridad histórica y trazas de sincronización comercial; no fabricar
  trazas exitosas para habilitar números. Mongo conserva una ventana reducida.
- Completar catálogo y validar depósitos activos, exclusiones y relaciones de ubicación.
- Resolver identidades desconocidas/ambiguas con evidencia del maestro Chess.
- Validar moneda, unidades comerciales/físicas y equivalencias de packs/bultos.
- Objetivos con alcance restringido o varios filtros no se aplican automáticamente.
  Metas de clientes nuevos/recuperados requieren cobertura histórica adicional.
- Stock es físico. El contrato observado no certifica disponible, comprometido,
  reservas ni identidad única de lote. No se calculan días de cobertura o transferencias.
- Las señales tienen estados `detected`, `assigned`, `in_progress`, `resolved`,
  `escalated`; se prepara el modelo, sin crear un nuevo workflow persistido de gestión.
- La atribución comercial reutiliza cartera vigente y lo declara; no reconstruye
  automáticamente la asignación histórica del vendedor.

## K. Riesgos encontrados

- Las consultas históricas y capturas secuenciales pueden durar varios minutos.
  El POST de captura es síncrono; un proxy puede agotar su timeout aunque el backend
  continúe. Para la primera operación extensa conviene usar la CLI y revisar cobertura.
- Leer datos durante una sincronización no garantiza una foto transaccional. Las
  trazas activas o fallidas invalidan cobertura; una certificación histórica más fuerte
  requiere versionar particiones y reconciliar fuentes.
- Un proceso terminado abruptamente puede dejar una cabecera `running`; nunca se
  presenta como completa. La exclusión expira después de una hora y se renueva por depósito.
- El historial de snapshots, raw, cuarentena e identidades crece sin TTL ni eliminación
  automática. Debe dimensionarse almacenamiento y acordarse retención posteriormente.
- Descubrir depósitos desde ventas omite potencialmente depósitos sin ventas.
- Cambios de maestros y taxonomía pueden modificar desgloses; el contexto lleva una
  huella de los datos enriquecidos, pero no almacena todavía todo el histórico de maestros.
- Moneda y unidades sin validar impiden interpretar las cantidades como equivalencias
  físicas. Las relaciones empresa/sucursal dependen de la validación administrativa.

## L. Recomendaciones para Fase 2

1. Completar catálogo/relaciones y cobertura histórica reconciliada; establecer
   responsables de datos y ejecutar una primera captura real con depósitos validados.
2. Validar identidades y unidades antes de cruzar ventas con stock; documentar
   agregaciones legítimas de múltiples artículos físicos a un estadístico.
3. Agregar calendario hábil y expectativas por canal, fuerza y momento del mes;
   calibrar severidad con evidencia histórica, sin porcentajes universales.
4. Incorporar jobs con seguimiento, reanudación y retención; versionar particiones
   comerciales y permisos de stock por ubicación.
5. Persistir ciclo de gestión y evaluación de señales antes de integrar agentes o
   canales externos. Evaluar candidatos de transferencia sólo después de validar
   disponibilidad, reservas y reglas operativas; mantener aprobación humana.
