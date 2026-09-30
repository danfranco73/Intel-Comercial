# Intel-Comercial — estado congelado

Cierre de etapa: 29/09/2026. Implementación local documentada; commit/push pendientes de aprobación explícita del resumen previo. Este cierre no acredita un deploy ni una verificación actual de producción.

## Qué está implementado

Intelligence Core Fase 1 expone una API independiente de la UI: Sales Daily Brief, drill-down comercial (estructura/fuerza → canal → vendedor → proveedor → familia/línea → producto → cliente), Stock Daily Brief y consulta de observaciones. Reutiliza las métricas comerciales y aplica permisos, alcance del usuario y controles de cobertura.

El catálogo administrable distingue depósito descubierto, configurado, activo e incluido en análisis. La sincronización recorre depósitos elegibles y registra cobertura y errores por depósito. Cobertura de ejecución y conocimiento del universo empresarial son conceptos separados. Un fallo no convierte un snapshot parcial en completo.

La certificación histórica comercial utiliza evidencia de escritura, ventanas y filas observadas. `unavailable` o cobertura no verificada no significa ventas cero. La suite verifica la implementación con datos sintéticos y servicios simulados; la disponibilidad en una instalación depende de sus datos, permisos y configuración.

## Qué es experimental y no oficial

- Sales V2: piloto aislado de 60 días completos (31/07–28/09/2026), originales y revisiones auditables en ClickHouse local. No reemplaza V1 ni alimenta los endpoints oficiales.
- Depósito directo: `idDeposito`/`dsDeposito` de ventas Chess son la fuente primaria. `planillaCarga` es independiente de ruta. No inferir depósito por empresa, ruta, fuerza o planilla; no equiparar depósito con sucursal.
- La atribución histórica por reglas de rutas queda como investigación/simulación suspendida, sin activación. Su código y fixture versionado no constituyen una regla productiva vigente.
- Separación económica/física aprobada: bruto, bonificación, neto y final independientes de cantidades con cargo, sin cargo y físicas. Bonificación 100% implica neto cero; conserva salida física. Devoluciones mantienen sus signos. Cero explícito no se sustituye por bruto.
- Cruce stock + movimiento físico: conversión sólo con identidad y unidades compatibles; salidas, devoluciones y otros negativos separados. Coberturas teóricas por ventanas 7/30/60, sin umbrales ni decisiones automáticas.
- Las conciliaciones explican la diferencia monetaria del piloto con V1; no autorizan reparar V1 ni extrapolar a todo el histórico. El origen técnico exacto del tratamiento histórico de V1 no quedó certificado.
- Abastecimiento: se revisaron documentación y archivos existentes, sin implementar un motor nuevo. `/movStock/` sólo publica POST/PUT de escritura; no se ejecutan. No hay GET publicado de OC, recepciones o transferencias en el catálogo revisado. `/pedidos/` corresponde al circuito comercial de clientes.

## Límites para decisiones automáticas

No utilizar todavía las coberturas experimentales para compras, transferencias, clasificación de riesgo o disponibilidad prometida. El stock es una observación fechada, no una lectura en vivo. El movimiento derivado de ventas no certifica un libro logístico: `consumeStock` no está validado, el maestro no ofrece vigencia histórica completa y las devoluciones comerciales no prueban recepción física.

Los motivos comerciales de vencimiento, rotura o defecto son evidencia, no actas de decomiso. Un saldo neto cero no equivale a ausencia de actividad. La falta de datos sobre compras no significa “sin reposición prevista”. Los depósitos observados no deben declararse automáticamente como universo empresarial completo.

## Depósito 5

**DEP PRODUNOA (id 5) es transitorio**, utilizado para recepción/operaciones especiales y regularización posterior hacia depósito 1. Su saldo esperado normalmente es cero. **Nunca reasignar históricamente sus ventas al depósito 1.**

Queda pendiente un control automático de saldo, sin severidad definida. La observación de stock del piloto no cubre el depósito 5: su saldo es desconocido. La regularización 5→1 exige eventos explícitos; no se infiere por diferencias de snapshots.

## Restricciones de operación durante el congelamiento

- Chess permanece READ ONLY para esta etapa: consultas GET documentadas y autenticación cuando una lectura autorizada la requiera. No ejecutar POST/PUT de movimientos.
- **Toda consulta masiva, nueva descarga histórica, repetición del piloto o backfill requiere aprobación explícita previa para ese alcance**, con presupuesto de requests, una petición simultánea, límites y abandono ante errores/latencia anormal. La aprobación del piloto anterior no autoriza otra descarga.
- Priorizar evidencia local. Los scripts de captura existentes siguen siendo ejecutables: este cierre establece una restricción operativa documentada, no añade un bloqueo técnico global ni modifica tareas externas desconocidas.
- No realizar backfill 2022–2026, deploy, modificación de V1 productivo o TeMandoApp, compras/transferencias, logística, finanzas, n8n, LLM o mensajería sin una nueva instrucción de alcance.
- No ejecutar migraciones ni sincronizaciones como parte de tests o preparación del commit. No incluir evidencia privada mediante `git add -f`.

## Pendiente prioritario: OLE-DB / ODBC de lectura

Cuando Chess entregue acceso, obtener primero motor/driver, diccionario/esquema, claves, vistas permitidas, estados y significado de fechas. **No inventar nombres de tablas.** Preferir vistas de reporting o réplica aprobada y usuario exclusivamente de lectura.

Investigar órdenes de compra, pedidos a proveedores, remitos, recepciones/ingresos, transferencias y pendientes entre depósitos, devoluciones a proveedor, ajustes, decomisos y movimientos internos. Para lead time real hacen falta fecha de pedido al proveedor, fecha efectiva de recepción, vínculo entre líneas, cantidades/unidades y entregas parciales. Distinguir pedido pendiente de mercadería despachada en tránsito.

Otras tareas pendientes: certificar impactos físicos y vigencia de unidades, conocer el universo de depósitos, resolver el proceso histórico que originó las diferencias de V1, y aprobar por separado cualquier reparación, promoción V2 o ampliación del histórico.

## Arquitectura y preparación para Daniel OS

`Fuentes → repositorios/contexto y cobertura → servicios de Intelligence → /api/intelligence → consumidores autorizados`

Ventas utiliza la persistencia comercial existente; stock, catálogo e identidades tienen repositorios propios. Los modelos y servicios separan acceso, normalización, evidencia y presentación. La UI no es la fuente del cálculo. V2 y sus revisiones experimentales permanecen aislados del circuito oficial.

Lecturas principales: `/api/intelligence/sales/daily-brief`, `/api/intelligence/sales/drilldown`, `/api/intelligence/stock/daily-brief` y `/api/intelligence/stock/items`. Son rutas con sesión/permisos, no endpoints públicos ni un conector de agentes ya implementado. El consumidor debe respetar fecha de corte, estado de cobertura, evidencia, restricciones de alcance y versión del contrato.

Arquitectura superior futura, fuera de este repositorio:

**DANIEL → Agente Ejecutivo / Chief of Staff →**

1. General
2. Comercial
3. Logística
4. Administración
5. Depósito y Expedición
6. Seguridad / Auditor
7. Apps en Desarrollo
8. Finanzas y Patrimonio
9. Capacitación y Futuro
10. Monetización
11. Salud y Mejora Continua

Intel-Comercial será principalmente una fuente para Comercial y Depósito y Expedición, y luego otros agentes. No se implementan aquí coordinación, memoria de agentes, credenciales externas, LLM ni ejecución autónoma. Autenticación de servicio y versionado de integración se deberán acordar antes de conectar esa capa.

## Evidencia, tests y versionado

Los originales, bases locales, informes con cifras, CSV, manifiestos de capturas y resultados detallados permanecen privados y excluidos de Git. No se borraron. Su existencia local no garantiza disponibilidad en otro clon: para reproducir el piloto se necesita un traspaso seguro y autorizado, nunca incluir los datos en Git.

Los tests versionados utilizan fixtures sintéticos. La prueba nativa ClickHouse puede omitirse en un equipo sin el binario local; el resumen de cierre debe indicar aprobados, fallidos y omitidos reales. Los fixtures de depósitos/reglas son configuración funcional identificada, no extractos de ventas.

Ver [PRE_COMMIT_REVIEW.md](PRE_COMMIT_REVIEW.md) para resultados de suite, revisión de seguridad, lista exacta de archivos propuestos y exclusiones. Ningún commit, tag o push forma parte de este cierre sin aprobación posterior de Daniel.
