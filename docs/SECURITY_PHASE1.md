# Seguridad — FASE 1

## Roles

- `admin`: operación, usuarios, sincronizaciones y toda la información.
- `commercial_director`: información comercial completa.
- `supervisor`: universo limitado por supervisor, sucursales o fuerzas asignadas.
- `seller`: universo limitado estrictamente por `seller_key`.
- `viewer`: lectura analítica completa; no puede operar administración.

## Flujo

1. Login valida bcrypt y registra el intento.
2. Se crea un token aleatorio; MongoDB guarda sólo SHA-256.
3. El navegador recibe cookie HttpOnly, SameSite=Lax y Secure en producción.
4. `/api/auth/me` rota el token CSRF y lo mantiene sólo en memoria JavaScript.
5. Cada POST exige CSRF.
6. Logout revoca la sesión server-side y elimina la cookie.

Los eventos de login, rechazo, logout y alta de usuarios se registran como JSON
en `logs/security.log`, sin contraseñas, cookies, tokens ni CSRF.

## Alcance

`resolve_data_scope()` resuelve los nombres de vendedores permitidos desde `erp_sellers`. El alcance se aplica en `analyzer.py` antes de:

- métricas;
- rankings;
- oportunidades;
- facetas;
- filtros disponibles;
- análisis dinámico;
- consistencia.

Un alcance sin asignaciones falla cerrado y produce un universo vacío. Los filtros enviados por el cliente nunca amplían el alcance.

## Bootstrap

```bash
export BOOTSTRAP_ADMIN_EMAIL='admin@example.test'
export BOOTSTRAP_ADMIN_PASSWORD='una-clave-larga-y-unica'
export BOOTSTRAP_ADMIN_NAME='Administrador'
.venv/bin/python scripts/migrate_phase1_auth.py
unset BOOTSTRAP_ADMIN_PASSWORD
```

No hay usuario ni contraseña por defecto. La migración no cambia un usuario existente.

## Crear perfiles

Con una sesión admin:

```http
POST /api/users
X-CSRF-Token: ...
Content-Type: application/json

{
  "email": "vendedor@example.test",
  "name": "Vendedor",
  "password": "ClaveTemporal123",
  "role": "seller",
  "seller_key": "S1",
  "supervisor_key": null,
  "branch_keys": [],
  "sales_force_keys": [],
  "is_active": true
}
```

La FASE 1 incluye API administrativa, no una pantalla completa de gestión de usuarios. Esa UI puede agregarse sin cambiar el modelo.

## Riesgos pendientes

- El rate limit de requests administrativos heredado sigue en memoria por proceso.
- No hay cambio/recuperación de contraseña ni MFA.
- No hay rotación de sesión durante una sesión activa más allá del login.
- `commercial_director` y `viewer` tienen alcance global de la instalación.
- La base actual sigue siendo monoempresa.
- La seguridad conserva `ThreadingHTTPServer`; la operación productiva debe terminar TLS en un proxy y limitar red.
- La modularización completa corresponde a FASE 2.

## Rollback

1. Detener el servidor.
2. Volver a `main`.
3. Reiniciar con el código anterior.

Las colecciones `users`, `user_sessions` y `login_attempts` son aditivas y el código anterior las ignora. No deben borrarse para hacer rollback. `sessions/default` se conserva; las nuevas sesiones por usuario tampoco interfieren.
