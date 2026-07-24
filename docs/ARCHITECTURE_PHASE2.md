# Arquitectura modular — FASE 2

## Objetivo

Separar responsabilidades sin cambiar contratos HTTP, cálculos ni integraciones.
La estructura evita el nombre `app/` porque coexistir con `app.py` vuelve ambiguos
los imports de Python.

```text
app.py                         fachada compatible + main
sales_coach/
  config.py                    paths, entorno y límites
  server.py                    composición HTTP y compatibilidad legacy
  routes/
    registry.py                ruta -> handler, permiso, CSRF
    auth_routes.py             transporte de autenticación/usuarios
    analysis_routes.py         transporte de análisis/preferencias
  schemas/
    requests.py                validación tipada de entrada
  services/
    auth_service.py            casos de uso de identidad
    analysis_service.py        análisis completo/dinámico/táctico
    sync_service.py            orquestación de Chess y persistencia
  repositories/
    auth_repository.py         users/user_sessions
    preferences_repository.py  preferencias por usuario
    analysis_repository.py     historial de ejecuciones
    sales_repository.py        selección Mongo/ClickHouse
security/                      primitivas criptográficas y permisos
```

## Dependencias permitidas

```text
routes -> services -> repositories/integrations
routes -> schemas
services -> dominio analítico existente
repositories -> MongoDB/ClickHouse
app.py -> sales_coach.server
```

Los servicios no reciben objetos `BaseHTTPRequestHandler` ni escriben respuestas
HTTP. Los repositorios no conocen rutas ni cookies.

## Compatibilidad

Los siguientes imports históricos siguen disponibles:

```python
from app import (
    AppHandler,
    ReusableHTTPServer,
    _erp_masters_available,
    _parse_iso_date,
    _sync_sales_range_chunked,
    main,
)
```

Esto mantiene `scripts/erp_sync_range.py` y automatizaciones existentes.

Las rutas y payloads no cambiaron. ECharts, Tabulator, Chess, MongoDB y ClickHouse
se conservan.

## Validación

Se agregaron schemas equivalentes a validadores de DTO para:

- login;
- creación de usuario;
- rangos y opciones de sincronización;
- análisis, filtros, planificación y datasets.

La validación ocurre antes de ejecutar servicios o integraciones.

## Decisiones

- No se introdujo un framework HTTP durante la modularización.
- No se movieron reglas comerciales a los handlers nuevos.
- La preferencia MongoDB→ClickHouse está encapsulada en `SalesRepository`.
- El dispatch declarativo centraliza permisos y CSRF.
- Los handlers usan mixins porque `BaseHTTPRequestHandler` instancia la clase
  directamente y no soporta inyección de controladores convencional.

## Deuda explícita

`sales_coach/server.py` todavía contiene:

- helpers de sincronización por chunks usados por el CLI;
- resolución de datasets y compatibilidad Excel/ERP;
- handlers administrativos de archivos y diagnóstico;
- servidor de archivos estáticos.

Extraerlos ahora habría ampliado la fase hacia una reescritura de integración. La
próxima extracción segura es `DatasetService` y `FileRepository`, acompañada por
tests de contrato de cada fuente.
