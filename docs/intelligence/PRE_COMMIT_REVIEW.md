# Resumen previo al commit — congelamiento Intel-Comercial

Estado al cierre: rama `feature/codenoa-sales-coach`, HEAD `dbb0824`. **No se hizo commit, tag, push ni deploy. El índice está vacío.** Este inventario propone incluir todos los cambios no ignorados enumerados abajo; requiere aprobación de Daniel antes de versionarlos.

## Resultado y alcance

- [CURRENT_STATE.md](CURRENT_STATE.md) documenta implementación, experimentos, restricciones, arquitectura y futura relación con Daniel OS. La Fase 1 inicial ya está en HEAD; este lote incorpora trabajo posterior y documentación de cierre.
- Se conserva el cambio previo de certificación de cobertura comercial y sus pruebas; no se alteran métricas ni V1 productivo.
- Se incluyen componentes de investigación/piloto offline y sus tests, sin activación productiva. La atribución por rutas sigue suspendida; la regla primaria es depósito directo de Chess.
- Se endurece `.gitignore`, se reemplazan tres contraseñas de ejemplo en documentación por placeholders explícitos y se aíslan los tests del entorno real.
- Dos scripts offline dejan de contener importes comerciales literales: comparan con la evidencia privada ya archivada. El control permanece; no se recalculó ni cambió histórico. Ambos scripts compilaron correctamente.
- No se consultó Chess ni bases productivas; no se ejecutaron capturas, migraciones, backfill, compras, transferencias, logística, finanzas ni la capa de agentes.

## Tests y controles

Comando final: `.venv/bin/python -m pytest -q`. **184 tests: 184 aprobados, 0 fallidos, 0 omitidos, 7,38 s; 245 advertencias de dependencias.** La prueba ClickHouse nativa local sí se ejecutó; en otros equipos puede omitirse si falta el binario privado.

La primera corrida protegida pasó 179 tests, pero registró 453 intentos de conexión externa bloqueados. Se corrigió el aislamiento en `tests/conftest.py`: no carga `.env`, elimina configuración heredada de conectores y bloquea DNS, TCP y datagramas externos en Python; permite loopback para las pruebas HTTP y sockets Unix. Se agregaron cinco tests de esta protección y se repitió la suite completa. No se modificó la conectividad de la aplicación productiva.

`git diff --check`: sin errores. Git advierte que la configuración local puede convertir LF a CRLF; no se cambió esa configuración ni se hizo una conversión masiva.

## Revisión de seguridad

Inspección de archivos ya tracked y nuevos no ignorados: búsqueda de claves privadas, tokens, JWT, URIs con credenciales y asignaciones literales; comparación adicional con secretos disponibles en `.env`, sin imprimir sus valores. No se detectaron credenciales reales para incorporar. Los siete candidatos del escáner fueron tres placeholders documentales y cuatro atributos de interfaz, no secretos. Los passwords de tests son sintéticos, no credenciales reales.

No hay `.env`, originales de Chess, CSV comerciales, dumps, backups, bases de piloto ni archivos nuevos mayores a 1 MB entre los candidatos. La biblioteca ECharts de aproximadamente 1 MB ya está versionada y no se modifica. Los fixtures sintéticos y configuraciones funcionales de depósitos se conservan.

Esta revisión corresponde al árbol actual y al lote propuesto; no es una certificación del historial Git ni de clones/remotos. `.gitignore` no protege archivos ya tracked ni impide `git add -f`: se revisó el índice y permanece vacío.

## Git status exacto

```text
M .gitignore
 M README.md
 M docs/PHASE_0_1_IMPLEMENTATION_REPORT.md
 M docs/SECURITY_PHASE1.md
 M docs/intelligence/PHASE1.md
 M sales_coach/services/intelligence_context_service.py
 M tests/conftest.py
 M tests/test_intelligence_sales.py
?? docs/intelligence/CURRENT_STATE.md
?? docs/intelligence/PRE_COMMIT_REVIEW.md
?? sales_coach/domain/deposit_attribution.py
?? sales_coach/domain/sales_v2.py
?? sales_coach/domain/sales_v2_movement.py
?? sales_coach/domain/stock_sales.py
?? sales_coach/repositories/attribution_rule_repository.py
?? sales_coach/repositories/sales_v2_local_repository.py
?? sales_coach/services/sales_coverage_service.py
?? sales_coach/services/sales_v2_capture.py
?? scripts/analyze_sales_v2_pilot.py
?? scripts/audit_intelligence_history.py
?? scripts/build_stock_movement_offline.py
?? scripts/data/deposit_attribution/v1.json
?? scripts/diagnose_sales_v2_offline.py
?? scripts/finalize_sales_v2_pilot.py
?? scripts/pilot_sales_v2.py
?? scripts/pilot_sales_v2_auxiliary.py
?? scripts/prepare_sales_v2_pilot.py
?? scripts/simulate_deposit_attribution.py
?? tests/test_deposit_attribution.py
?? tests/test_sales_coverage.py
?? tests/test_sales_v2_movement.py
?? tests/test_sales_v2_pilot.py
?? tests/test_test_isolation.py
```

## Resumen de diff tracked

```text
.gitignore                                         | 49 ++++++++++++++++
 README.md                                          |  2 +-
 docs/PHASE_0_1_IMPLEMENTATION_REPORT.md            |  2 +-
 docs/SECURITY_PHASE1.md                            |  2 +-
 docs/intelligence/PHASE1.md                        |  5 ++
 .../services/intelligence_context_service.py       | 68 ++++------------------
 tests/conftest.py                                  | 56 ++++++++++++++++++
 tests/test_intelligence_sales.py                   |  7 ++-
 8 files changed, 130 insertions(+), 61 deletions(-)
```

Además hay **25 archivos nuevos**, enumerados abajo. `git diff --stat` no los cuenta hasta agregarlos al índice. En total se proponen **33 archivos: 8 modificados y 25 nuevos**, sin eliminaciones.

## Archivos que entrarían

| Estado | Archivo |
|---|---|
| Modificado | `.gitignore` |
| Modificado | `README.md` |
| Modificado | `docs/PHASE_0_1_IMPLEMENTATION_REPORT.md` |
| Modificado | `docs/SECURITY_PHASE1.md` |
| Modificado | `docs/intelligence/PHASE1.md` |
| Modificado | `sales_coach/services/intelligence_context_service.py` |
| Modificado | `tests/conftest.py` |
| Modificado | `tests/test_intelligence_sales.py` |
| Nuevo | `docs/intelligence/CURRENT_STATE.md` |
| Nuevo | `docs/intelligence/PRE_COMMIT_REVIEW.md` |
| Nuevo | `sales_coach/domain/deposit_attribution.py` |
| Nuevo | `sales_coach/domain/sales_v2.py` |
| Nuevo | `sales_coach/domain/sales_v2_movement.py` |
| Nuevo | `sales_coach/domain/stock_sales.py` |
| Nuevo | `sales_coach/repositories/attribution_rule_repository.py` |
| Nuevo | `sales_coach/repositories/sales_v2_local_repository.py` |
| Nuevo | `sales_coach/services/sales_coverage_service.py` |
| Nuevo | `sales_coach/services/sales_v2_capture.py` |
| Nuevo | `scripts/analyze_sales_v2_pilot.py` |
| Nuevo | `scripts/audit_intelligence_history.py` |
| Nuevo | `scripts/build_stock_movement_offline.py` |
| Nuevo | `scripts/data/deposit_attribution/v1.json` |
| Nuevo | `scripts/diagnose_sales_v2_offline.py` |
| Nuevo | `scripts/finalize_sales_v2_pilot.py` |
| Nuevo | `scripts/pilot_sales_v2.py` |
| Nuevo | `scripts/pilot_sales_v2_auxiliary.py` |
| Nuevo | `scripts/prepare_sales_v2_pilot.py` |
| Nuevo | `scripts/simulate_deposit_attribution.py` |
| Nuevo | `tests/test_deposit_attribution.py` |
| Nuevo | `tests/test_sales_coverage.py` |
| Nuevo | `tests/test_sales_v2_movement.py` |
| Nuevo | `tests/test_sales_v2_pilot.py` |
| Nuevo | `tests/test_test_isolation.py` |

El fixture `scripts/data/deposit_attribution/v1.json` conserva reglas y nombres de configuración de negocio ya aprobados para simulación, no ventas ni credenciales. Permanece inactivo y no autoriza inferencia de depósitos.

## Archivos excluidos, conservados localmente

| Directorio privado | Archivos | Tamaño aproximado |
|---|---:|---:|
| `docs/intelligence/attribution-v1-audit/` | 14 | 3.86 MiB |
| `docs/intelligence/history-audit-2026-09-29/` | 7 | 0.28 MiB |
| `docs/intelligence/load-sheet-audit-2026-09-29/` | 8 | 0.22 MiB |
| `docs/intelligence/sales-logistics-migration-plan/` | 1 | 0.04 MiB |
| `docs/intelligence/sales-v2-offline-diagnosis/` | 24 | 7.24 MiB |
| `docs/intelligence/sales-v2-pilot/` | 14 | 2.26 MiB |
| `docs/intelligence/stock-physical-experimental/` | 17 | 30.57 MiB |
| `docs/intelligence/supply-api-investigation/` | 7 | 0.05 MiB |

También quedan fuera `.env`/`.env.*`, entornos virtuales, logs/cachés, CSV (salvo fixtures sintéticos bajo tests), capturas JSONL/JSON comprimido, Parquet, dumps, backups, bases locales, claves/cookies/tokens y temporales conforme a `.gitignore`.

Los originales, baseline, manifiestos y ClickHouse local del piloto están fuera del workspace en `~/.local/share/intel-comercial-v2-pilot`. El log de tests y la evidencia de revisión están en `~/.local/share/intel-comercial-freeze`, también fuera del repositorio. No se borraron ni movieron datos para ocultarlos.

## Riesgos y pendientes explícitos

1. **Congelamiento operativo, no bloqueo global:** los scripts existentes de captura siguen disponibles. Requieren nueva aprobación explícita antes de una consulta masiva; no se modificaron schedulers, servicios ni producción para detener tareas externas.
2. **V2 no oficial:** unidades, impacto físico, universo empresarial y proceso histórico de V1 mantienen las limitaciones documentadas. No habilitar compras, transferencias, riesgo ni otras decisiones automáticas.
3. **Reproducibilidad privada:** los scripts de análisis necesitan evidencia excluida. Un clon nuevo puede ejecutar tests sintéticos, pero no reproducir el piloto real sin transferencia segura autorizada. El binario nativo no se versiona.
4. **Dependencias:** la suite pasa con avisos de deprecación; no se actualizaron librerías durante el cierre.
5. **Depósito 5:** transitorio, saldo esperado cero, sin observación en la foto del piloto. Mantener sus ventas como 5. El control futuro de saldo y la trazabilidad 5→1 quedan pendientes.
6. **OLE-DB/ODBC prioritario:** esperar acceso de lectura y diccionario de Chess antes de investigar tablas, órdenes, recepciones, movimientos y lead time; no inventar esquema.
7. **Daniel OS:** sólo se documentó su arquitectura y el rol de Intel-Comercial como fuente. No hay agentes externos, LLM, mensajería, n8n ni nuevas integraciones activadas.

**Punto de parada:** revisión entregada. No ejecutar commit/push hasta recibir aprobación explícita de este resumen.
