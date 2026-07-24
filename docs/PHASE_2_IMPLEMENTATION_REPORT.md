# Informe de implementación — FASE 2

Fecha: 23/07/2026  
Rama: `feature/codenoa-sales-coach`

## Resultado

- `app.py`: de 1.761 líneas a 18.
- Configuración centralizada y `APP_HOST`/`APP_PORT` configurables.
- Registro declarativo de rutas, permisos y CSRF.
- Rutas de autenticación y análisis separadas.
- Servicios sin dependencia HTTP para auth, análisis y sync.
- Repositorios para auth, preferencias, análisis y selección de ventas.
- Schemas tipados antes de servicios.
- Análisis dinámico extraído del handler.
- Imports históricos conservados.

No se modificaron reglas, métricas, modelos comerciales, Chess, esquemas de ventas
ni ClickHouse.

## Archivos

| Área | Archivos |
|---|---|
| Entrada | `app.py`, `sales_coach/server.py` |
| Configuración | `sales_coach/config.py` |
| Rutas | `sales_coach/routes/*.py` |
| Servicios | `sales_coach/services/*.py` |
| Repositorios | `sales_coach/repositories/*.py` |
| Schemas | `sales_coach/schemas/*.py` |
| Pruebas | `tests/test_phase2_architecture.py` |
| Documentación | `docs/ARCHITECTURE_PHASE2.md` |

## Decisiones técnicas

1. Se eligió `sales_coach/` para evitar conflicto con `app.py`.
2. `app.py` es una fachada, no duplica implementación.
3. El registro de rutas define seguridad antes de invocar handlers.
4. Los servicios devuelven datos o excepciones; las rutas deciden HTTP.
5. Las preferencias ahora usan repositorio con DB inyectable.
6. `SalesRepository` conserva fallback MongoDB→ClickHouse.
7. La sincronización usa un servicio pero conserva callbacks legacy de chunks.

## Compatibilidad

- Mismas URLs y payloads.
- Mismos códigos HTTP de validación para los contratos caracterizados.
- Mismo frontend.
- Mismo comando `.venv/bin/python app.py`.
- `scripts/erp_sync_range.py` conserva imports.
- La monkeypatch de tests se realiza sobre `sales_coach.server`, la implementación
  real, no sobre la fachada.

## Pruebas y validación

Comandos ejecutados:

```bash
.venv/bin/python -m py_compile app.py *.py bi/*.py scripts/*.py security/*.py \
  sales_coach/*.py sales_coach/routes/*.py sales_coach/services/*.py \
  sales_coach/repositories/*.py sales_coach/schemas/*.py tests/*.py
.venv/bin/python -m pytest -q
node --check static/app.js
node --check static/login.js
git diff --check
```

Resultado:

- 20 tests aprobados.
- Python compiló sin errores.
- JavaScript validó sin errores de sintaxis.
- El diff no contiene errores de whitespace.
- Quedan advertencias de deprecación de `mongomock` bajo Python 3.14 y avisos
  preexistentes de normalización LF/CRLF; no afectan el resultado.

## Riesgos pendientes

- `server.py` sigue siendo grande por helpers legacy y administración.
- No hay DI container; la composición es explícita y liviana.
- Algunos repositorios legacy (`mongo_client.py`, `clickhouse_client.py`) siguen
  siendo funciones y se migrarán gradualmente.
- La validación no cubre todavía cada campo interno de filtros/datasets.
- No se cambió el runtime `ThreadingHTTPServer`.

## Próximo trabajo sugerido

La siguiente fase funcional sigue siendo FASE 3. Antes o durante ella conviene:

1. extraer `DatasetService`;
2. crear repositorio de archivos;
3. encapsular proceso de sync/checkpoints;
4. agregar contratos de integración Mongo/ClickHouse/Chess;
5. introducir logging/request ID en el nuevo servidor modular.

No se implementó FASE 3.
