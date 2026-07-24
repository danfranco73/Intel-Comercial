# Auditoría técnica previa — Commercial Intelligence / Codenoa Sales Coach

Fecha de auditoría: 23 de julio de 2026  
Repositorio auditado: `GESTION`  
Modalidad: inspección de código y consultas operativas de solo lectura. No se ejecutaron sincronizaciones, migraciones ni cambios de dependencias.

## Criterio y alcance

Se revisaron los archivos versionados, la configuración por nombres de variables (sin exponer secretos), las rutas HTTP, los consumidores del frontend, la persistencia y los procesos manuales. También se ejecutó:

- compilación sintáctica de todos los módulos Python mediante `py_compile`: correcta;
- consulta de solo lectura a MongoDB: conexión correcta;
- consulta de solo lectura a ClickHouse: falló por timeout de red a los 10 segundos;
- búsqueda de tests, código de autenticación, aislamiento multiempresa, IA y exportaciones.

La evidencia operativa es una fotografía al 23/07/2026. “No confirmado” significa que el repositorio o el sistema accesible no permitieron verificar el punto, no que la capacidad sea necesariamente inexistente.

---

## 1. Arquitectura

### Conclusión

Commercial Intelligence **no utiliza Next.js**, ni App Router ni Pages Router. Es una aplicación monolítica local:

- backend Python sobre `http.server.ThreadingHTTPServer`;
- frontend estático en HTML, CSS y JavaScript sin framework;
- API JSON implementada en el mismo proceso Python;
- analítica ejecutada en memoria por módulos Python;
- MongoDB y ClickHouse accedidos directamente desde el backend.

### Evidencia

- `app.py:8`, `app.py:81`, `app.py:484` — `ThreadingHTTPServer` y `BaseHTTPRequestHandler`.
- `app.py:536-642` — dispatch manual de rutas GET/POST.
- `app.py:1520-1529` — arranque directo del servidor en `HOST:PORT`.
- `static/index.html`, `static/admin.html`, `static/app.js`, `static/app.css` — frontend estático.
- No existen `package.json`, `next.config.*`, `app/`, `pages/`, `src/app/`, `layout.tsx`, `page.tsx`, `route.ts`, `_app.tsx` ni `_document.tsx`.

### Organización real

| Capa | Implementación | Estado |
|---|---|---|
| Presentación | `static/index.html`, `static/admin.html`, `static/app.js`, `static/app.css` | Implementado |
| Transporte HTTP | `AppHandler` en `app.py` | Implementado |
| Orquestación | handlers y `_resolve_datasets()` en `app.py` | Implementado, muy concentrado |
| Dominio/analítica | `analyzer.py`, `analysis_engine.py`, `bi/*.py`, `rule_engine.py` | Implementado |
| Persistencia | `mongo_client.py`, `clickhouse_client.py` | Implementado/parcial |
| Integración ERP | `erp_client.py` | Implementado |

No existe migración incompleta entre routers de Next.js: Next.js nunca forma parte de este repositorio.

### Riesgos arquitectónicos

- `app.py` tiene 1.533 líneas y mezcla HTTP, autenticación admin, carga de archivos, orquestación ERP, persistencia y análisis.
- `static/app.js` tiene 5.863 líneas y concentra estado, llamadas API, formularios y renderizado.
- No hay una capa explícita de servicios/casos de uso ni repositorios abstraídos.
- Los cálculos se ejecutan sin cola de trabajos; el frontend puede informar que una operación “puede seguir procesando”, pero el request HTTP sigue siendo síncrono (`static/app.js:5389`).
- El servidor estándar de Python es válido para uso local/controlado, pero no aporta por sí solo las garantías operativas habituales de un framework web productivo.

---

## 2. Biblioteca de UI

No se usa Tailwind, MUI, shadcn/ui, Chakra, Ant Design, CSS Modules ni Styled Components.

| Biblioteca | Evidencia | Componentes principales | Nivel de uso | Estado |
|---|---|---|---|---|
| CSS propio | `static/app.css`; clases usadas por ambos HTML | paneles, grids, formularios, KPIs, navegación, modal | Predominante | Implementado |
| Tabulator 6.3.1 | `static/vendor/tabulator/*`; `static/app.js:2265+` | mesas ejecutiva, vendedores, clientes, histórico, oportunidades, metas | Alto | Implementado |
| Apache ECharts | `static/vendor/echarts/echarts.min.js`; `static/index.html:604`; `static/app.js:3117` | todos los dashboards BI | Alto | Implementado |

### Design system

Hay un **sistema visual propio informal**, no un paquete desacoplado:

- variables CSS globales en `static/app.css`;
- patrones reutilizados: `.panel`, `.inner-panel`, `.executive-grid`, `.metric-card`, `.echart-host`, `.tabulator-host`;
- breakpoints en `static/app.css:481`, `487` y `1509`;
- componentes funcionales creados con funciones JavaScript, por ejemplo `renderMetricTiles`, `mountDashboardChart` y renderizadores de tablas.

No hay versionado de componentes, documentación de tokens, Storybook ni pruebas visuales. La duplicación principal está en las numerosas funciones `build*Option()` de ECharts, que repiten tooltip, ejes, leyenda, colores y formato. El frontend también combina gráficos ECharts con gráficos SVG/HTML propios (`renderLineChart`, `renderMultiLineChart`, `static/app.js:4799+`), generando dos sistemas de visualización.

No se detectaron dependencias UI instaladas y sin uso porque no existe gestor de paquetes frontend. ECharts y Tabulator están vendorizados y consumidos.

---

## 3. Gráficos y visualizaciones

| Biblioteca | Componente/dashboard | Tipo | Fuente | Estado |
|---|---|---|---|---|
| ECharts | Ejecutivo | líneas, barras, torta | JSON de `/api/analyze` y `/api/bi/tactical-month` | Real |
| ECharts | Vendedores | barras de ventas, crecimiento, clientes, concentración | `build_sellers_dashboard()` | Real |
| ECharts | Clientes | torta de estados, ventas, riesgo y mix | `build_clients_dashboard()` | Real |
| ECharts | Histórico | mensual, cohortes, Pareto, HHI, presupuesto | `build_history_dashboard()` | Real |
| ECharts | Oportunidades | rankings por cliente/canal/vendedor | `build_opportunities_dashboard()` | Real, basado en reglas |
| ECharts | Informe interactivo | línea o barra con filtros | resultado consolidado | Real |
| SVG/HTML propio | Informe base y análisis dinámico | líneas/multilíneas | `charts`/`vizSpec` del backend | Real |
| Tabulator | Mesas comerciales | tablas interactivas | mismos resultados backend | Real |

Evidencia principal:

- hosts de gráficos: `static/index.html:92-452`;
- montaje ECharts: `static/app.js:3117-3128`;
- opciones: `static/app.js:3143-3825`;
- datos de vendedores/clientes: `bi/focus_dashboards.py:69+`;
- histórico/oportunidades: `bi/structural_dashboards.py:305+`;
- consistencia: `bi/consistency.py:12+`.

### Datos, filtros, exportación y responsive

- Los gráficos consumen datos reales cuando la fuente elegida es Chess, MongoDB, ClickHouse o Excel. No se encontraron datasets demo hardcodeados.
- Sí existen umbrales y heurísticas hardcodeadas: caída de vendedor de `-12%`, concentración, potencial de mix de `18%`, benchmark de `60%`, entre otros (`bi/focus_dashboards.py:121+`, `bi/structural_dashboards.py:424+`). Son reglas, no IA.
- Admiten filtros globales y el informe interactivo tiene segmentadores (`static/index.html:251-299`, `static/app.js:2086+`).
- No hay exportación de gráficos, datos, PDF ni imágenes: no se configura `toolbox.saveAsImage` ni existe flujo de descarga.
- Hay adaptación CSS y `chart.resize()` en el evento `resize` (`static/app.js:5853+`). Esto confirma intención responsive; no hay tests visuales/dispositivos que confirmen calidad en todos los móviles.
- Las tablas tienen paginación y filtros de columna; el contenedor usa overflow.

### Adecuación a ClickHouse

Los gráficos no consultan agregados en ClickHouse. `load_erp_sales_dataset_clickhouse()` hace `SELECT` de todas las filas del rango y las carga en memoria (`clickhouse_client.py:324-389`); después Python agrega y genera dashboards. Esto desaprovecha la agregación columnar, escala mal para históricos amplios y puede provocar respuestas grandes/costosas.

---

## 4. Autenticación y autorización

### Conclusión

No existe autenticación de usuarios con JWT, NextAuth, Clerk, Firebase, OAuth ni equivalente. Sólo hay un **token administrativo compartido y opcional**.

| Capacidad | Implementación | Evidencia | Estado | Riesgo |
|---|---|---|---|---|
| Login de usuarios | Ninguno | no hay modelos, endpoints ni UI | No implementado | Crítico si se expone en red |
| Token admin | secreto fijo por variable de entorno | `app.py:183-188`, `518-534` | Parcial | Alto |
| Almacenamiento del token | `sessionStorage` | `static/app.js:348-364` | Implementado | Medio |
| Sesión/renovación | no existe para usuarios | — | No implementado | Alto |
| Roles/permisos | no existen | “roles” BI son etiquetas de alertas, no seguridad | No implementado | Crítico |
| Protección de páginas | ninguna | `/`, `/bi`, `/admin` públicas en `app.py:536-550` | No implementado | Alto |
| Protección de endpoints admin | `_ensure_admin_access()` | uploads, limpieza, sync, análisis listados y errores | Parcial | Alto |
| Protección de análisis/datos | ninguna | `/api/analyze`, `/api/session`, status y prefiltros abiertos | No implementado | Crítico |
| Rate limit | memoria por IP/scope | `app.py:191-213`, `518-524` | Parcial | Medio |
| Headers de seguridad | CSP, nosniff, frame, referrer | `app.py:485-493` | Implementado | Bajo |
| CSRF | no hay token CSRF | mutaciones abiertas o bearer manual | No implementado | Alto |
| Logout/recuperación | no existe | — | No implementado | Alto |

### Detalle

- Si `APP_ADMIN_TOKEN` no está configurado, `_ensure_admin_access()` permite la operación (`app.py:524-525`). En el entorno auditado no se confirmó dicha variable, por lo que la protección efectiva debe considerarse **no confirmada**.
- El token se compara como string normal, no con comparación de tiempo constante (`app.py:531`).
- No hay cookies de la aplicación. La cookie de Chess sólo vive en memoria del backend y se envía al ERP (`erp_client.py:748+`).
- El token admin queda en `sessionStorage`, no en cookie HttpOnly; una vulnerabilidad XSS podría leerlo.
- La UI “Comercial” se describe como sólo lectura, pero esto es control visual. Los endpoints de análisis, sesión y acceso a datos siguen abiertos.
- `/api/session` GET devuelve configuración y planificación compartidas, y POST permite reemplazarlas sin autenticación (`app.py:558-560`, `631-633`, `1090-1106`).
- `/api/workbook`, `/api/preview`, prefiltros y estados no están protegidos.
- Los secretos están en `.env`, que está ignorado por Git y no está versionado. No se observaron secretos hardcodeados en archivos versionados.

---

## 5. MongoDB

### Configuración y uso

Se usa PyMongo directo:

- conexión: `mongo_client.py:55-66`;
- base fija: `Intel-Comercial` (`mongo_client.py:16`);
- índices: `_ensure_erp_indexes()` (`mongo_client.py:147-175`);
- escrituras con reintentos y lotes: `_run_mongo_write()` y `_iter_batches()`;
- retención configurada en 7 días y modo de ventas `compact`;
- no se usa TTL index: la retención es un borrado aplicativo posterior a cada sync.

### Estado operativo confirmado

La consulta de solo lectura encontró:

| Colección/modelo | Función | Campos relevantes | Índices | Fuente | Consumidores | Estado |
|---|---|---|---|---|---|---|
| `erp_sales` | hechos compactados de venta | fecha, cliente, factura, producto, vendedor, ruta, canal, importes, cantidad | `_id`, `date` | Chess `/ventas/` | análisis y fallback comercial | Implementado; 30.626 docs, 25–30/06/2026 |
| `erp_articles` | maestro de productos y agrupaciones | producto, familia, línea, marca, proveedor, segmento, UxB | `_id`, producto unique, row_version | Chess `/articulos/` | enriquecimiento y filtros | Implementado; 2.098 docs |
| `erp_sellers` | personal comercial | vendedor, fuerza, sucursal, cargo, supervisor | `_id`, vendedor, nombre, fuerza | Chess `/personalComercial/` | dashboard/filtros | Implementado; 76 docs |
| `erp_routes` | rutas y cartera asociada | ruta, vendedor, fuerza, sucursal, vigencia, clientes, días | `_id`, ruta, descripción, vendedor, fuerza | Chess `/rutasVenta/` | enriquecimiento y recorridos | Implementado; 844 docs |
| `erp_marketing` | segmento/canal/subcanal | claves y nombres | `_id`, marketing unique, segmento, canal | Chess `/jerarquiaMkt/` | disponible como dataset, uso analítico limitado | Implementado; 12 docs |
| `erp_sync_runs` | trazabilidad de cargas | entidad, rango, estado, contadores, origen, timestamp | entidad+fecha; entidad+estado+rango | syncs | cobertura/status | Implementado; 9 docs |
| `sessions` | configuración y planificación global | datasets, planning | `_id` | frontend | inicialización UI | Implementado; 1 doc |
| `registros` | resúmenes de análisis | timestamp, filtros, meta, summary, insightsSummary | timestamp | `/api/analyze` | listado admin | Implementado; 5 docs |

Evidencia de código:

- serialización/compactación/deduplicación: `mongo_client.py:178-391`;
- cobertura por registros de sync: `mongo_client.py:507-593`;
- maestros: `mongo_client.py:682-881`;
- sesión global: `mongo_client.py:967-1005`;
- registro de análisis: `app.py:1005-1017`.

### Qué no guarda

No hay modelos/colecciones de usuarios, roles, permisos, empresas, stock, pedidos, cobranzas, objetivos formales, alertas persistidas, comentarios automáticos, facturas como entidad separada ni promociones. Las facturas sólo aparecen como identificador dentro del hecho de venta; los clientes también son atributos del hecho y listas de rutas, no un maestro propio.

### Calidad y riesgos

- **Duplicación deliberada Mongo/ClickHouse:** ventas compactadas en ambos. No existe conciliación automática entre almacenes.
- **Retención:** Mongo conserva 7 días; el histórico depende de ClickHouse. Es coherente con el diseño, pero vuelve crítica su disponibilidad.
- **Trazabilidad:** existe a nivel de sync; los documentos compactos eliminan `row_version`, `line_key`, `document_key`, `storedAt` y detalle original. Dificulta auditoría de una línea ERP.
- **Deduplicación:** `_id` se deriva de fecha+cliente+factura+producto+vendedor+esquema+ruta+canal. Agrupa líneas iguales y suma métricas. No preserva ítems individuales.
- **Índices:** `erp_sales` sólo tiene `date`; suficiente para el patrón actual por rango, insuficiente para consultas directas por cliente/vendedor/producto. Mongo actualmente se usa como carga masiva a memoria, no como motor analítico filtrado.
- **Documentos grandes:** `erp_routes.client_keys` puede crecer sin cota y acercarse al límite documental en rutas grandes; no hay validación de tamaño.
- **Validación:** no hay esquemas Mongoose/Pydantic/JSON Schema; se confía en normalizadores manuales.
- **Sesión global:** `_SESSION_ID = "default"` mezcla configuración y objetivos de todos los usuarios.
- **Reemplazo de maestros:** vendedores, rutas y marketing usan `delete_many({})` antes de escribir (`mongo_client.py:764-788`); un fallo intermedio puede dejar el maestro vacío o parcial.
- **Errores silenciados:** fallos de sesión/registro se convierten en `False`/`None` o se ignoran.
- **Sin `companyId`:** ninguna colección aísla empresa.

---

## 6. ClickHouse

### Modelo implementado

| Tabla | Motor | Granularidad | Origen | Frecuencia | Datos | Estado |
|---|---|---|---|---|---|---|
| `gestion_comercial.fact_sales_compact` | MergeTree | fecha+cliente+factura+producto+vendedor+esquema+ruta+canal | Chess `/ventas/` | manual por UI/CLI; por chunks | claves/dimensiones de venta, importes y cantidad | Implementado en código; carga pasada confirmada; disponibilidad actual no confirmada |

Evidencia:

- configuración/cliente: `clickhouse_client.py:56-100`;
- DDL: `clickhouse_client.py:107-159`;
- compactación: `clickhouse_client.py:166-218`;
- carga: `clickhouse_client.py:240-321`;
- lectura: `clickhouse_client.py:324-413`;
- estado: `clickhouse_client.py:416-467`.

El registro operativo `erp_sync_runs` confirma que el batch del 25/05/2026 al 30/06/2026 leyó 171.546 registros válidos y reportó **166.519 filas almacenadas en ClickHouse**. La consulta actual a ClickHouse terminó en timeout; por eso no se confirma recuento, rango ni disponibilidad presentes.

### Respuestas específicas

- Recibe **datos parcialmente detallados/compactados**, no sólo KPIs, pero tampoco conserva cada línea original.
- La fila mínima representa una combinación comercial compacta; múltiples líneas con la misma clave se suman.
- Dimensiones: fecha, cliente, vendedor, fuerza/esquema, producto, factura, ruta, canal.
- Métricas: importe, neto, final, impuestos internos, neto+internos y cantidad.
- No almacena descripción/familia/marca del producto, stock, pedido, cobranza, costo/margen, promoción, sucursal explícita, recorrido ejecutado ni estado/anulación.
- La carga es por rango/chunk. Inserta un `sync_run_id`, luego elimina versiones antiguas del rango mediante mutation.
- No hay views, materialized views, diccionarios, TTL, cron, webhook, cola ni migraciones versionadas.
- No hay scheduler: la automatización posible es externa al repo. Los dos mecanismos propios son POST manual `/api/erp/sync` y CLI `scripts/erp_sync_range.py`.
- No hay control de calidad entre totals de Chess, Mongo y ClickHouse; sólo contadores por sync.

### Riesgos

- El flujo Mongo→ClickHouse no es transaccional: Mongo se escribe primero y si ClickHouse falla, el chunk queda sólo en Mongo y el batch aborta (`app.py:368-405`).
- La estrategia insert-then-delete reduce la ventana sin datos, pero una falla de mutation puede dejar duplicados temporales.
- `MergeTree` no deduplica por clave. La corrección depende completamente de la mutation.
- El `ORDER BY` comienza por fecha y es adecuado para rangos temporales; consultas centradas en cliente/vendedor sin rango amplio se benefician menos.
- No hay agregados/materialized views para dashboards.
- `Float64` para importes puede introducir diferencias de redondeo; para finanzas sería preferible una precisión decimal definida.
- DDL interpolado desde variables sin validación estricta de identificadores.

---

## 7. Integración con Chess

### Endpoints consumidos

| Endpoint | Método/parámetros | Uso real |
|---|---|---|
| `/auth/login` | POST `usuario`, `password` | obtiene `sessionId`/cookie |
| `/ventas/` | GET `fechaDesde`, `fechaHasta`, `nroLote`, `detallado=true` | ventas/facturas/pedidos embebidos |
| `/articulos/` | GET `nroLote` | productos y agrupaciones |
| `/personalComercial/` | GET `sucursal` | vendedores/supervisores |
| `/rutasVenta/` | GET `sucursal`, `fuerzaventa`, `anulada=false` | rutas, clientes y días |
| `/jerarquiaMkt/` | GET `CodScan=` | segmentos, canales y subcanales |

Evidencia: `erp_client.py:113-136`, `140-365`, `380-434`. Persistencia activa desde `app.py:772-806` y `scripts/erp_sync_range.py:59-73`.

### Entidades

| Entidad | Disponible en la API | Consumida por el proyecto | Persistida | Destino | Estado/observaciones |
|---|---|---|---|---|---|
| Clientes | Parcial | Sí | Parcial | MongoDB/ClickHouse | clave/nombre en ventas; claves en rutas; no hay maestro de clientes |
| Productos | Sí | Sí | Sí | MongoDB; clave en ClickHouse | 2.098 artículos confirmados |
| Stock | No confirmado | No | No | Ninguno | sin endpoint, tipo ni consumo |
| Facturas | Parcial | Sí | Parcial | MongoDB/ClickHouse | número compuesto dentro de venta; no entidad/cabecera completa |
| Pedidos | Parcial | Parcial | Parcial | MongoDB/ClickHouse | `idPedido` sólo es fallback de factura; no flujo de pedidos |
| Cobranzas | No confirmado | No | No | Ninguno | sin código |
| Vendedores | Sí | Sí | Sí | MongoDB; atributos en ClickHouse | 76 confirmados |
| Recorridos | Parcial | Sí | Sí | MongoDB | rutas planificadas, días y clientes; no GPS/visita ejecutada |
| Canales | Sí | Sí | Sí | MongoDB y ventas Mongo/ClickHouse | jerarquía y atributos de venta |
| Familias | Sí | Sí | Sí | MongoDB | agrupación del artículo; no columna ClickHouse |
| Subfamilias | No confirmado | No | No | Ninguno | no se mapea agrupación `SUBFAMILIA` |
| Marcas | Sí | Sí | Sí | MongoDB | agrupación del artículo; no columna ClickHouse |
| Promociones | No confirmado | No | No | Ninguno | sin código |

“Disponible” sólo se marca Sí cuando el endpoint consumido entrega y el normalizador usa la entidad/campo. La persistencia real está respaldada por las colecciones y sus recuentos, no sólo por interfaces.

### Autenticación, errores y actualización

- La sesión Chess se cachea en memoria con TTL por defecto de 1.200 s; ante 401/403 se invalida y renueva una vez (`erp_client.py:83-109`, `748-764`).
- Reintenta 408/409/425/429/5xx y errores de red hasta 3 veces por defecto, con backoff lineal (`erp_client.py:704-745`).
- Ventas y artículos tienen paginación y límite de seguridad de 500 lotes.
- `fetch_staff_dataset()` llama dos veces seguidas al mismo endpoint y descarta la primera respuesta (`erp_client.py:220-222`): carga y riesgo innecesarios.
- No hay tratamiento explícito de rate-limit headers.
- Ventas se fragmentan por fecha; los maestros se refrescan manualmente cuando se solicita o faltan (`app.py:795-806`).
- No hay cron interno, webhook ni cola. Todas las entidades pueden quedar desactualizadas si nadie ejecuta la sincronización.
- La sustitución total de rutas/vendedores/marketing no conserva históricos de vigencia del maestro.

### Datos perdidos durante mapeo

El normalizador de ventas conserva sólo un subconjunto. En modo compacto se pierden además `row_version`, `document_key`, `line_key`, `detail_level`, nombres de producto, segmento/canal/subcanal separados, ruta comercial vs planilla y `source`. Para Sales Coach faltan especialmente:

- maestro completo y estado de clientes;
- costos/margen;
- stock actual e histórico;
- pedidos abiertos/cancelados;
- cobranzas, deuda y mora;
- promociones aplicadas/elegibilidad;
- visitas realizadas, geolocalización y resultado;
- devoluciones/notas de crédito tipificadas;
- objetivos versionados por período;
- catálogo de producto recomendado y disponibilidad.

---

## 8. Multiempresa

### Conclusión

El soporte multiempresa es **inexistente**. El sistema funciona como una instalación única de Codenoa y requerirá una refactorización importante para ser SaaS/multiempresa seguro.

| Capacidad multiempresa | Evidencia | Estado | Riesgo |
|---|---|---|---|
| Identificador tenant/company | no aparece en modelos ni consultas | Inexistente | Crítico |
| Usuarios por empresa | no hay usuarios | Inexistente | Crítico |
| Credenciales Chess por empresa | un único `.env` | Inexistente | Crítico |
| Base/colecciones Mongo aisladas | DB fija `Intel-Comercial` | Inexistente | Crítico |
| ClickHouse aislado | una DB/tabla sin company key | Inexistente | Crítico |
| Sesión/configuración aislada | `_SESSION_ID="default"` | Inexistente | Crítico |
| Selección de empresa | no hay UI | Inexistente | Alto |
| Roles por empresa | no hay autorización | Inexistente | Crítico |
| Caché ERP aislada | cookie global de proceso | Inexistente | Crítico |

La sucursal (`branch_key`) y fuerza de venta no equivalen a empresa/tenant. Agregar sólo `companyId` a algunas tablas no sería suficiente: hacen falta identidad, autorización, credenciales por tenant, filtros obligatorios, claves compuestas, partición lógica/física, cachés y pruebas contra fugas.

---

## 9. Preparación para inteligencia artificial

No existe integración con modelos de IA, proveedor LLM, embeddings, vector store ni MLOps. Los “insights”, alertas, proyecciones y oportunidades actuales son **algoritmos deterministas y textos por plantilla**, por ejemplo `insight_writer.py`, `bi/tactical_month.py:353+` y `bi/structural_dashboards.py:413+`.

| Funcionalidad | Valor | Datos necesarios | Datos actuales suficientes | Complejidad | Riesgos | Prioridad |
|---|---|---|---|---|---|---|
| 1. Consulta en lenguaje natural | acceso rápido a BI | esquema semántico, métricas gobernadas, permisos | Parcialmente | Media | SQL incorrecto, fuga, costo | P1 |
| 2. Resúmenes de desempeño | lectura ejecutiva | KPIs y evidencia trazable | Sí | Baja | alucinación narrativa | P0 |
| 3. Comentarios por vendedor | coaching focalizado | KPIs por vendedor, objetivos, cartera | Parcialmente | Media | tono, privacidad, falta de contexto | P1 |
| 4. Detección de anomalías | reacción temprana | serie histórica estable y fresca | Parcialmente | Media | falsos positivos, poco histórico accesible | P1 |
| 5. Predicción de ventas | planificación | histórico largo, calendario, precio/promos | Parcialmente | Alta | drift, estacionalidad | P2 |
| 6. Predicción de objetivos | priorizar intervención | histórico y objetivos versionados | No | Alta | metas hoy globales/editables sin historial | P2 |
| 7. Riesgo de pérdida | retención | frecuencia/recencia, cliente, visitas, deuda | Parcialmente | Alta | etiquetas proxy, sesgo | P1 |
| 8. Producto por cliente | cross-sell | detalle cliente-producto, catálogo, stock | Parcialmente | Alta | recomendar sin stock/margen | P1 |
| 9. Recomendación de mix | ampliar cartera | familias compradas, pares, segmento | Parcialmente | Media | heurísticas confundidas con causalidad | P1 |
| 10. Stock recomendado | reducir quiebres | stock, lead time, demanda, vencimiento | No | Muy alta | no hay stock | P2 |
| 11. Promociones | mejorar conversión/margen | promos, precio, elasticidad, margen | No | Muy alta | incentivos destructivos | P3 |
| 12. Alertas inteligentes | foco operativo | eventos, series, dueños, feedback | Parcialmente | Media | fatiga de alertas | P1 |
| 13. Asistente de vendedores | productividad | cartera autorizada, ficha 360°, acciones | Parcialmente | Alta | exposición entre vendedores | P2 |
| 14. Preparación de reuniones | mejor supervisión | ficha vendedor/cliente, objetivos, alertas | Parcialmente | Media | resumen incompleto | P1 |
| 15. Informes PDF | distribución | plantillas y snapshots | Sí | Media | cifras fuera de fecha | P1 |
| 16. Presentaciones | comité comercial | narrativa, gráficos exportables | Sí | Alta | consistencia visual/datos | P2 |
| 17. Comparación de recorridos | eficiencia territorial | ruta planificada y visita real/GPS | No | Alta | sólo hay planificación | P2 |
| 18. Oportunidades comerciales | crecimiento | ventas/mix/benchmark/stock | Parcialmente | Media | potencial no validado | P0 |
| 19. Explicar caída/crecimiento | diagnóstico | descomposición por dimensiones | Parcialmente | Media | confundir correlación y causa | P0 |
| 20. Simular pack adicional | acciones concretas | UxB, precio, stock, elegibilidad | Parcialmente | Media | no hay stock/precio/promos completos | P1 |

### Controles mínimos antes de IA

- catálogo semántico de métricas y fecha de actualización visible;
- consultas autorizadas y filtradas por usuario/empresa/vendedor;
- respuestas con cifras y período citados, no texto libre sin evidencia;
- límites de costo y caché de respuestas;
- registro de prompt, fuentes, modelo y respuesta;
- evaluación offline con casos reales;
- feedback humano y prohibición de ejecutar acciones automáticamente;
- PII/comercial minimizada y contratos de tratamiento con el proveedor.

---

## 10. Soporte para Codenoa Sales Coach

| Módulo | Soporte actual | Datos disponibles | Brecha principal | Complejidad |
|---|---|---|---|---|
| Dashboard ejecutivo | Implementado | ventas, mix, clientes | seguridad y escala | Baja |
| Ranking vendedores | Implementado | vendedor/ventas/clientes | objetivos formales | Baja |
| Comparación mensual | Implementado | histórico por fecha | disponibilidad ClickHouse | Baja |
| Evolución por vendedor | Implementado | vendedor/mes | consultas agregadas | Baja |
| Participación ventas | Implementado | dimensiones de venta | — | Baja |
| Mix comercial | Implementado | producto/familia/marca | stock/margen | Baja |
| Oportunidades vendedor | Parcial | reglas de benchmark/mix | validación y siguiente acción | Media |
| Comentarios automáticos | Parcial | plantillas deterministas | IA, auditoría y feedback | Media |
| Objetivos comerciales | Parcial | planning global/segmento | modelo persistente/versionado | Media |
| Seguimiento objetivos | Parcial | metas en sesión + real | multiusuario, historial y workflow | Media |
| Reconocimientos | No implementado | ranking disponible | reglas/eventos/persistencia | Media |
| Alertas | Parcial | reglas en respuesta | persistencia, asignación, estado | Media |
| Clientes inactivos | Implementado | recencia/frecuencia | maestro cliente | Baja |
| Riesgo pérdida | Parcial | clasificación heurística | modelo validado y señales extra | Alta |
| Próximo producto | No implementado | compras cliente-producto parciales | recomendador/stock | Alta |
| Próxima categoría | Parcial | oportunidades de mix | ranking personalizado | Media |
| Preparación reuniones | No implementado | varios KPIs reutilizables | ficha/plantilla/generación | Media |
| Fichas individuales | Parcial | filas cliente/vendedor | ruta dedicada, permisos, histórico | Media |
| PDF | No implementado | datos renderizados | motor/plantillas | Media |
| PowerPoint | No implementado | datos renderizados | generación y QA visual | Alta |
| Históricos | Parcial | ClickHouse diseñado; Mongo 7 días | disponibilidad/conciliación | Media |
| Gamificación | No implementado | ranking/objetivos parciales | reglas, justicia, eventos | Alta |
| Asistente conversacional | No implementado | BI suficiente para MVP acotado | auth, semántica, IA | Alta |
| Multiempresa | No implementado | ninguna base | aislamiento integral | Muy alta |

La base analítica acerca el producto a Sales Coach, pero hoy es un tablero avanzado de una sola empresa, no una plataforma de coaching segura y operacional.

---

## 11. Calidad del código

### Hallazgos ordenados por severidad

| Severidad | Hallazgo | Evidencia | Impacto | Recomendación |
|---|---|---|---|---|
| Crítico | Sin autenticación/autorización de usuarios | `app.py:536-642` | exposición de ventas y configuración | identidad y RBAC server-side antes de publicar |
| Crítico | Sin aislamiento multiempresa/vendedor | DB y sesión globales | fuga comercial | modelo de tenancy y filtros obligatorios |
| Alto | Histórico depende de ClickHouse sin disponibilidad confirmada | Mongo 7 días; timeout actual | dashboards históricos frágiles | healthcheck, alertas, réplica/backup y SLA |
| Alto | Endpoints de análisis/sesión abiertos | `app.py:558`, `619-635` | lectura/modificación no autorizada | proteger toda API, no sólo admin |
| Alto | Sin tests automatizados | no hay archivos test/spec ni framework | regresiones silenciosas | tests unitarios, integración ERP/DB y E2E |
| Alto | Maestro reemplazado con delete+insert no atómico | `mongo_client.py:770-788` | maestro vacío/parcial | colección staging + swap/versionado |
| Alto | Escritura Mongo y ClickHouse no transaccional | `app.py:368-405` | divergencia de almacenes | outbox/checkpoint/reconciliación idempotente |
| Alto | Sin scheduler/monitor operativo | sólo UI y CLI | datos atrasados | job scheduler, métricas y alertas |
| Alto | Agregación ClickHouse en Python | `clickhouse_client.py:324+` | memoria/latencia | queries agregadas y vistas materializadas |
| Medio | Archivos monolíticos | `app.py`, `static/app.js`, `analyzer.py` | mantenibilidad | separar handlers, servicios, repositorios y componentes |
| Medio | Fallos silenciados | `app.py:1016`, `1367+`; `mongo_client.py:987+` | estados falsamente exitosos | logging estructurado y errores explícitos |
| Medio | Doble request de personal | `erp_client.py:220-222` | carga y rate limit | eliminar llamada duplicada |
| Medio | `Float64` para dinero | DDL ClickHouse | diferencias | Decimal con escala acordada |
| Medio | Validación manual sin esquema | handlers/normalizadores | payloads inconsistentes | Pydantic/JSON Schema |
| Medio | Logs mínimos | archivos vacíos; `_log_error` local | diagnóstico insuficiente | logs estructurados, request/sync IDs, métricas |
| Medio | Umbrales comerciales hardcodeados | módulos `bi` | recomendaciones opacas | configuración versionada y validada |
| Medio | Sesión/objetivos globales | `mongo_client.py:17`, `967+` | usuarios se pisan | modelo por usuario/empresa/período |
| Medio | Sin historial de maestros | replace-all | análisis temporal incorrecto | SCD/vigencias |
| Bajo | Código muerto/legado potencial | sistema SVG y ECharts paralelos | duplicación visual | consolidar componentes gradualmente |
| Bajo | README desactualizado | menciona CDN, pero assets son locales | confusión operativa | actualizar documentación |
| Bajo | Sin type checking estático efectivo | Python con tipos parciales; JS sin TS | errores tardíos | mypy/pyright y módulos JS tipados |

### Fortalezas

- Normalización ERP explícita y legible.
- Reintentos de red y renovación de sesión Chess.
- Protección contra borrar rangos ante respuestas vacías ambiguas.
- Carga por chunks y trazabilidad básica.
- CSP y headers defensivos.
- Resolución segura de rutas de archivo.
- Métricas BI amplias y separación parcial en `bi/`.
- Compilación sintáctica correcta de todos los módulos.
- No se hallaron mocks de producción ni secretos versionados.

---

# A. Resumen ejecutivo

Commercial Intelligence funciona realmente como una aplicación Python local con frontend estático, no como una aplicación Next.js. Tiene una base BI considerable: carga ventas desde Excel, Chess, MongoDB o ClickHouse; las enriquece con artículos, vendedores y rutas; calcula KPIs, evolución, rankings, concentración, cohortes, riesgo de clientes, oportunidades, objetivos editables y alertas deterministas; y presenta el resultado con ECharts y Tabulator.

La integración Chess está activa para ventas, artículos, personal comercial, rutas y jerarquía de marketing. MongoDB confirmó 30.626 hechos de venta recientes, 2.098 artículos, 76 vendedores, 844 rutas y 12 nodos de marketing. Los logs de sincronización confirman una carga anterior de 166.519 filas a ClickHouse. Sin embargo, ClickHouse no respondió durante esta auditoría, por lo que su disponibilidad y contenido actuales quedan no confirmados.

El dato de venta no es el detalle ERP íntegro: se compacta por fecha, cliente, factura, producto, vendedor, esquema, ruta y canal. Es mejor que un agregado diario de KPIs, pero pierde trazabilidad de línea. Mongo conserva sólo siete días; por diseño, el histórico depende de ClickHouse.

El producto carece de identidad de usuario, roles reales y aislamiento de datos. Sólo algunas operaciones administrativas admiten un token compartido opcional; análisis, sesiones y varias lecturas permanecen abiertos. Tampoco existe multiempresa: hay una única base, credenciales Chess, cookie ERP, sesión y tabla analítica globales.

No hay IA. Los comentarios, oportunidades, proyecciones y alertas son reglas y plantillas deterministas. Esto es una buena base explicable, pero no debe presentarse como inteligencia artificial. Tampoco existen PDF, PowerPoint, flujo de coaching, fichas protegidas, reconocimientos ni gamificación.

La distancia hasta “Codenoa Sales Coach” es intermedia: el motor analítico y varios dashboards ya existen, pero faltan la capa de producto seguro y operativo, la calidad/observabilidad del dato, objetivos persistentes, alertas gestionables y datos clave como stock, deuda, promociones y visitas reales. Conviene construir primero una base confiable y segura, y añadir IA temprana sólo en tareas narrativas con evidencia.

# B. Matriz general de capacidades

| Capacidad | Implementado | Parcial | No implementado | No confirmado | Evidencia |
|---|:---:|:---:|:---:|:---:|---|
| Next.js |  |  | X |  | servidor Python/static |
| UI | X |  |  |  | CSS propio |
| Gráficos | X |  |  |  | ECharts/Tabulator |
| Autenticación |  | X |  |  | token admin opcional |
| Autorización |  |  | X |  | sin roles/permisos |
| MongoDB | X |  |  |  | colecciones verificadas |
| ClickHouse |  | X |  | X | código+carga pasada; timeout actual |
| Chess | X |  |  |  | 6 endpoints activos |
| Ventas | X |  |  |  | Mongo y sync |
| Clientes |  | X |  |  | atributos, sin maestro |
| Productos | X |  |  |  | `erp_articles` |
| Stock |  |  | X |  | sin consumo |
| Facturas |  | X |  |  | identificador en venta |
| Pedidos |  | X |  |  | fallback `idPedido` |
| Cobranzas |  |  | X |  | sin código |
| Vendedores | X |  |  |  | `erp_sellers` |
| Recorridos |  | X |  |  | rutas planificadas |
| Canales | X |  |  |  | ventas+marketing |
| Familias | X |  |  |  | agrupación artículo |
| Marcas | X |  |  |  | agrupación artículo |
| Promociones |  |  | X |  | sin código |
| Multiempresa |  |  | X |  | sin tenant |
| IA |  |  | X |  | reglas, no modelos |
| PDF |  |  | X |  | sin exportación |
| PowerPoint |  |  | X |  | sin exportación |
| Objetivos |  | X |  |  | planning global |
| Alertas |  | X |  |  | en respuesta, no workflow |
| Fichas individuales |  | X |  |  | filas BI, no ruta/ficha |

# C. Principales brechas técnicas

## 1. Bloqueantes

1. Autenticación, autorización y protección uniforme de la API.
2. Aislamiento por usuario/vendedor y diseño multiempresa futuro.
3. Disponibilidad, monitoreo y reconciliación del histórico ClickHouse.
4. Calidad y trazabilidad del dato de venta.
5. Automatización confiable de sincronizaciones.

## 2. Importantes

1. Maestro de clientes y datos de stock, cobranzas, pedidos y promociones.
2. Objetivos versionados por empresa, período, vendedor y responsable.
3. Alertas persistentes con propietario, estado, vencimiento y resultado.
4. Queries agregadas en ClickHouse.
5. Tests automatizados y observabilidad.

## 3. Mejoras recomendadas

1. Modularizar `app.py` y `static/app.js`.
2. Validar payloads y modelos.
3. Versionar reglas/umbrales comerciales.
4. Exportar PDF y luego PowerPoint.
5. Fichas navegables de vendedor y cliente.

## 4. Deuda secundaria

1. Unificar los dos sistemas de gráficos.
2. Corregir request duplicado de personal.
3. Actualizar README.
4. Mejorar tipado estático.

# D. Recomendación sobre IA

**Sí conviene incorporar IA ahora, pero sólo de forma acotada y no como núcleo decisorio.** Primero deben resolverse seguridad, calidad/fecha del dato y disponibilidad histórica en paralelo.

Se pueden implementar inmediatamente:

- resumen ejecutivo fundamentado en KPIs estructurados;
- explicación descriptiva de variaciones mediante descomposición calculada;
- preparación de reuniones con cifras citadas;
- redacción de oportunidades que ya fueron detectadas por reglas;
- consulta en lenguaje natural sólo sobre un catálogo de métricas permitido y con resultados tabulares verificables.

Deben esperar:

- predicción robusta de ventas/objetivos;
- churn supervisado;
- recomendación de stock/promociones;
- asistente autónomo por vendedor;
- comparación inteligente de recorridos reales.

Antes hacen falta: histórico ClickHouse confiable, maestro de clientes, objetivos versionados, stock, precios/margen, cobranzas, promociones, visitas reales y control de acceso.

# E. Roadmap sugerido

## Corto plazo — base confiable

| Iniciativa | Dependencias | Complejidad | Impacto | Orden |
|---|---|---|---|---:|
| Autenticación y RBAC | identidad elegida | Alta | Crítico | 1 |
| Proteger toda API | auth | Media | Crítico | 2 |
| Healthchecks/alertas ClickHouse | observabilidad | Media | Alto | 3 |
| Scheduler e idempotencia | jobs | Alta | Alto | 4 |
| Reconciliación Chess/Mongo/CH | métricas de sync | Alta | Alto | 5 |
| Modelo de objetivos versionado | identidad/período | Media | Alto | 6 |
| Tests críticos | fixtures anonimizados | Media | Alto | 7 |
| Catálogo de métricas | definiciones negocio | Media | Alto | 8 |

## Mediano plazo — Sales Coach operativo

| Iniciativa | Dependencias | Complejidad | Impacto | Orden |
|---|---|---|---|---:|
| Fichas vendedor/cliente | auth, RBAC | Media | Alto | 1 |
| Objetivos y seguimiento | modelo versionado | Media | Alto | 2 |
| Alertas gestionables | jobs, usuarios | Alta | Alto | 3 |
| Query agregada/materialized views | CH estable | Media | Alto | 4 |
| Maestro de clientes | Chess/datos | Alta | Alto | 5 |
| PDF | snapshots y plantillas | Media | Medio | 6 |
| Resúmenes IA con evidencia | catálogo, auditoría | Media | Alto | 7 |
| PowerPoint | export de gráficos | Alta | Medio | 8 |

## Largo plazo — plataforma inteligente y multiempresa

| Iniciativa | Dependencias | Complejidad | Impacto | Orden |
|---|---|---|---|---:|
| Stock/cobranzas/promos/visitas | nuevas integraciones | Muy alta | Alto | 1 |
| Recomendador cliente-producto | datos anteriores | Alta | Alto | 2 |
| Predicciones y churn | histórico validado | Alta | Alto | 3 |
| Asistente por vendedor | RBAC+IA+fichas | Alta | Alto | 4 |
| Gamificación | objetivos justos | Alta | Medio | 5 |
| Multiempresa | tenancy integral | Muy alta | Estratégico | 6 |
| SaaS | multiempresa, billing, soporte | Muy alta | Estratégico | 7 |

---

## Dictamen final

Commercial Intelligence ya es una base BI comercial funcional y con integración real a Chess. No es todavía un Sales Coach completo: carece principalmente de seguridad por usuario, procesos persistentes de coaching, histórico operacional garantizado, fuentes comerciales clave e IA real. La evolución recomendada es incremental: conservar el motor analítico y las integraciones que funcionan, fortalecer la plataforma y luego sumar IA verificable sobre métricas gobernadas.
