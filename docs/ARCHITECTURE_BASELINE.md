# Línea base técnica — FASE 0

## Inicio

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/migrate_phase1_auth.py
.venv/bin/python app.py
```

La aplicación escucha por defecto en `http://127.0.0.1:8765`. `/login` es la única página funcional pública. `/bi`, `/admin` y todas las APIs de negocio requieren sesión.

## Arquitectura

```text
Navegador (HTML/CSS/JS, ECharts, Tabulator)
  -> AppHandler / ThreadingHTTPServer (app.py)
     -> seguridad (security/)
     -> carga/orquestación (app.py)
     -> analítica (analyzer.py, analysis_engine.py, bi/)
     -> Chess (erp_client.py)
     -> MongoDB (mongo_client.py)
     -> ClickHouse (clickhouse_client.py)
```

La FASE 1 agregó una frontera de seguridad sin reescribir los módulos analíticos.
La modularización de FASE 2 está documentada en `docs/ARCHITECTURE_PHASE2.md`.

## Fuentes

- Chess: ventas, artículos, personal comercial, rutas y jerarquía de marketing.
- MongoDB: ventas recientes, maestros, trazabilidad de sync, configuración por usuario y autenticación.
- ClickHouse: hechos históricos compactados.
- Excel: ventas y maestros cargados manualmente por administradores.

## Variables de entorno

| Variable | Propósito | Ejemplo no secreto |
|---|---|---|
| `MONGO_URI` | conexión MongoDB | `mongodb://localhost:27017` |
| `CHESS_ERP_BASE_URL` | base API Chess | `https://erp.example.test/api` |
| `ERP_LOGIN_PATH` | login Chess | `/auth/login` |
| `CHESS_ERP_USERNAME` | usuario técnico | `integration-user` |
| `CHESS_ERP_PASSWORD` | contraseña técnica | `change-me` |
| `CHESS_ERP_TIMEOUT` | timeout HTTP | `30` |
| `CHESS_ERP_VERIFY_SSL` | verificar TLS | `true` |
| `CLICKHOUSE_HOST` | host ClickHouse | `ch.example.test` |
| `CLICKHOUSE_PORT` | puerto HTTPS | `8443` |
| `CLICKHOUSE_USERNAME` | usuario | `app_reader_writer` |
| `CLICKHOUSE_PASSWORD` | contraseña | `change-me` |
| `CLICKHOUSE_DATABASE` | base | `gestion_comercial` |
| `CLICKHOUSE_SALES_TABLE` | tabla actual | `fact_sales_compact` |
| `APP_ENV` | entorno | `development` o `production` |
| `APP_COOKIE_SECURE` | fuerza cookie Secure | `true` |
| `APP_SESSION_HOURS` | expiración sesión | `12` |
| `APP_LOGIN_WINDOW_MINUTES` | ventana antifuerza bruta | `15` |
| `APP_LOGIN_MAX_ATTEMPTS` | fallos permitidos | `5` |
| `BOOTSTRAP_ADMIN_EMAIL` | admin inicial | `admin@example.test` |
| `BOOTSTRAP_ADMIN_PASSWORD` | clave inicial fuerte | `definir-en-entorno` |
| `BOOTSTRAP_ADMIN_NAME` | nombre visible | `Administrador` |

`APP_ADMIN_TOKEN` queda como compatibilidad heredada, pero no es el mecanismo principal y no permite omitir la sesión de usuario.

## MongoDB

Colecciones existentes preservadas:

- `erp_sales`, `erp_articles`, `erp_sellers`, `erp_routes`, `erp_marketing`;
- `erp_sync_runs`, `registros`, `sessions`.

Colecciones de FASE 1:

- `users`: identidad, hash bcrypt, rol y asignaciones;
- `user_sessions`: token opaco hasheado, CSRF hasheado, expiración/revocación;
- `login_attempts`: intentos mínimos para limitación de fuerza bruta.

La configuración antes global en `sessions/default` queda preservada. Las nuevas lecturas/escrituras usan como `_id` el ID del usuario.

## ClickHouse

La tabla existente se conserva sin cambios:

- base por defecto: `gestion_comercial`;
- tabla por defecto: `fact_sales_compact`;
- motor: `MergeTree`;
- partición: `toYYYYMM(date)`;
- orden: fecha, cliente, vendedor, producto y factura.

## Sincronización

- UI admin: `POST /api/erp/sync`.
- CLI: `scripts/erp_sync_range.py`.
- Ventas se descargan por rangos y chunks, se compactan y escriben en MongoDB/ClickHouse.
- Maestros se refrescan a pedido o cuando faltan.
- No existe scheduler interno en FASE 0/1.

## Pruebas

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Los fixtures de `tests/fixtures/` son sintéticos y no contienen información comercial real.
