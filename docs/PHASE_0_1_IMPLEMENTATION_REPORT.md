# Informe de implementación — FASE 0 y FASE 1

Fecha: 23/07/2026  
Rama: `feature/codenoa-sales-coach`

## 1. Resumen

Se implementó una frontera de seguridad sobre la aplicación existente sin reescribir el motor analítico ni reemplazar Chess, MongoDB, ClickHouse, ECharts o Tabulator.

Incluido:

- línea base y catálogo de endpoints;
- fixtures anónimos y tests de caracterización;
- usuarios MongoDB con cinco roles;
- bcrypt;
- sesiones opacas server-side;
- cookie HttpOnly/SameSite y Secure configurable;
- CSRF para POST autenticados;
- login, logout y `/api/auth/me`;
- rate limit persistido de login;
- permisos server-side;
- alcance de vendedor/supervisor aplicado antes de KPIs y facetas;
- configuración de UI separada por usuario;
- API admin de alta/listado de usuarios;
- migración idempotente y bootstrap sin contraseña predeterminada;
- logging de eventos de seguridad;
- protección de páginas y APIs sensibles.

Preservado:

- endpoints y payloads comerciales;
- integración Chess y normalizadores;
- persistencia comercial MongoDB/ClickHouse;
- reglas/KPIs/dashboards;
- frontend HTML/JS/CSS;
- sesión histórica `sessions/default`;
- token admin heredado sólo como compatibilidad secundaria tras autenticar un admin.

Pendiente por decisión de fase:

- modularización completa de `app.py`;
- edición/desactivación/recuperación de usuarios en UI;
- MFA;
- scheduler, reconciliación y vistas ClickHouse;
- objetivos, alertas persistentes, exportaciones, IA y multiempresa.

El admin inicial no se creó automáticamente porque no se proporcionó una contraseña explícita. Sí se aplicaron los índices. Esto evita introducir credenciales inseguras.

## 2. Archivos

| Archivo | Acción | Motivo |
|---|---|---|
| `security/models.py` | Creado | modelos tipados de usuario/contexto |
| `security/passwords.py` | Creado | bcrypt y política mínima |
| `security/auth.py` | Creado | usuarios, sesiones, CSRF, brute force, cookies |
| `security/authorization.py` | Creado | roles, permisos y alcance |
| `security/audit.py` | Creado | logging JSON sin secretos |
| `scripts/migrate_phase1_auth.py` | Creado | índices y bootstrap idempotente |
| `app.py` | Modificado | proteger rutas y exponer auth/usuarios |
| `analyzer.py` | Modificado | aplicar alcance antes de cálculos/facetas |
| `mongo_client.py` | Modificado | sesión por usuario y prefiltros scoped |
| `static/login.html/js` | Creados | login compatible |
| `static/app.js` | Modificado | CSRF, sesión y logout |
| `static/index.html`, `admin.html`, `app.css` | Modificados | identidad/logout y estilo login |
| `xlsx_reader.py` | Corregido | compatibilidad OOXML con targets `/xl/...` |
| `tests/**` | Creados | caracterización, fuentes y seguridad |
| `requirements.txt` | Modificado | bcrypt |
| `requirements-dev.txt` | Creado | pytest/mongomock |
| `docs/**` | Creados | arquitectura, endpoints, seguridad e informe |
| `README.md` | Modificado | inicio seguro |
| `.gitignore` | Modificado | caché pytest |

`AUDITORIA_TECNICA_COMMERCIAL_INTELLIGENCE.md` proviene de la auditoría previa y se conserva.

## 3. Base de datos

Colecciones creadas al verificar índices:

- `users`;
- `user_sessions`;
- `login_attempts`.

Índices:

- `users_email_unique`;
- `users_role_active`;
- `users_seller_key`;
- `sessions_token_unique`;
- `sessions_user_active`;
- `sessions_expiry_ttl`;
- `login_attempt_lookup`;
- `login_attempt_expiry_ttl`.

Estado tras migración:

- usuarios: 0;
- sesiones: 0;
- intentos: 0.

No se modificaron colecciones comerciales ni ClickHouse. Para crear el primer admin:

```bash
BOOTSTRAP_ADMIN_EMAIL=admin@example.test \
BOOTSTRAP_ADMIN_PASSWORD='una-clave-fuerte-y-unica' \
.venv/bin/python scripts/migrate_phase1_auth.py
```

## 4. Seguridad

- Contraseñas: bcrypt, coste 12.
- Sesión: 48 bytes URL-safe; sólo SHA-256 persiste.
- Cookie: HttpOnly, SameSite=Lax, Secure por defecto en `APP_ENV=production`.
- CSRF: token aleatorio rotado por `/api/auth/me`, hash en Mongo, header obligatorio en POST.
- Fuerza bruta: ventana/umbral configurables, intentos persistidos con TTL.
- Logout: revocación server-side.
- Roles: admin, commercial_director, supervisor, seller, viewer.
- Alcance: resuelto desde maestros ERP y aplicado al universo antes de calcular.
- Rutas: toda página/API sensible requiere sesión; operación/archivos/sync/usuarios requieren admin.
- Secretos: no se exponen al frontend ni se registran.

Riesgos pendientes: MFA, recuperación de contraseña, rate limit distribuido de requests generales, TLS directo y modularización del servidor.

## 5. Tests

Comandos:

```bash
.venv/bin/python -m py_compile *.py bi/*.py scripts/*.py security/*.py tests/*.py
.venv/bin/python -m pytest -q
node --check static/app.js
node --check static/login.js
```

Resultado: **15 passed**. Los warnings provienen de compatibilidad interna de `mongomock` con Python 3.14 y no representan fallos.

Cobertura funcional creada:

- Chess y normalización;
- compactación/deduplicación;
- KPIs, ranking, mix, oportunidades y evolución;
- Excel, MongoDB y ClickHouse;
- contraseñas, login, sesión, CSRF, logout y brute force;
- permisos y alcance seller/supervisor;
- HTTP 401/403 y administración bloqueada.

## 6. Validación manual

### Preparación

1. Crear el admin con la migración.
2. Iniciar `app.py`.
3. Abrir `/login`.
4. Entrar como admin.
5. Obtener CSRF desde `/api/auth/me` o usar la interfaz.
6. Crear perfiles con `POST /api/users`.

### Admin

- debe acceder a `/admin` y `/bi`;
- debe listar/crear usuarios;
- debe sincronizar y operar archivos.

### Director

- debe acceder a `/bi`;
- no debe acceder a `/admin` ni `/api/users`;
- debe ver información comercial completa.

### Supervisor

- crear con `supervisor_key`, `branch_keys` o `sales_force_keys`;
- verificar que rankings/filtros sólo contengan vendedores resueltos;
- modificar filtros en DevTools no debe ampliar el universo.

### Vendedor

- crear con `seller_key` existente;
- verificar que sólo aparece su nombre y sus KPIs;
- enviar manualmente otro `seller_name`: el resultado debe quedar vacío/422, nunca devolver al otro vendedor.

### Viewer

- debe consultar `/bi`;
- no debe operar `/admin`, archivos, sync ni usuarios.

### No autorizado

- abrir `/bi` sin cookie: redirección a `/login`;
- llamar `/api/datasets` sin cookie: 401;
- POST sin CSRF: 403;
- vendedor llamando `/api/users`: 403.

## 7. Variables

Consultar `docs/ARCHITECTURE_BASELINE.md`. Variables nuevas:

| Nombre | Propósito | Ejemplo |
|---|---|---|
| `APP_ENV` | producción/desarrollo | `production` |
| `APP_COOKIE_SECURE` | cookie Secure | `true` |
| `APP_SESSION_HOURS` | duración | `12` |
| `APP_LOGIN_WINDOW_MINUTES` | ventana de intentos | `15` |
| `APP_LOGIN_MAX_ATTEMPTS` | máximo de fallos | `5` |
| `BOOTSTRAP_ADMIN_EMAIL` | email inicial | `admin@example.test` |
| `BOOTSTRAP_ADMIN_PASSWORD` | clave inicial | `definir-en-entorno` |
| `BOOTSTRAP_ADMIN_NAME` | nombre | `Administrador` |

## 8. Rollback

1. Detener el proceso nuevo.
2. Volver a `main`.
3. Reiniciar el código anterior.

No borrar colecciones: son aditivas y el código anterior las ignora. Las ventas, maestros, ClickHouse y `sessions/default` no fueron alterados. Para rollback de código no se requiere rollback de datos.

## 9. Próxima fase

FASE 2 debe separar gradualmente:

- arranque/composición;
- rutas de auth, análisis, dashboard, sync y admin;
- servicios sin dependencia HTTP;
- repositorios Mongo/ClickHouse;
- esquemas de validación;
- integraciones.

No se implementó esa estructura todavía para respetar el límite solicitado.
