# Informe de implementación — FASE 7

Fecha: 23/07/2026  
Rama: `feature/codenoa-sales-coach`

## Resumen

Las señales comerciales temporales se convirtieron en alertas persistentes,
idempotentes y gestionables. Cada alerta conserva evidencia numérica,
responsable, estado, vencimiento, resultado e historial de eventos. También se
incorporaron alertas operativas por sincronización atrasada y divergencia entre
MongoDB y ClickHouse.

Se preservaron el análisis, los dashboards, las reglas deterministas, las
conexiones y el frontend actuales. No se implementó IA ni trabajo de fases
posteriores.

## Modelo y deduplicación

Colección `commercial_alerts`:

- `alert_id`: identificador público estable;
- `dedupe_key`: hash único de empresa, período, tipo, entidad y versión;
- `type`, `severity`, `period`, `metric`, `evidence`, `message`;
- `seller_key`, `client_key`, `category_key`;
- `assignee`, `due_date`, `status`;
- `comment`, `result`, `resolved_at`;
- `source`, `rule_id`, `rule_version`;
- `created_at`, `updated_at`, `last_seen_at`;
- `history`: eventos inmutables de creación, asignación y transición.

Regenerar una señal actualiza su evidencia y `last_seen_at`, pero no reemplaza
el responsable, el estado ni el historial gestionado por una persona.

Índices:

- `alert_dedupe_unique`;
- `alert_status_period`;
- `alert_seller_created`;
- `alert_assignee_status`;
- `alert_due_date`.

## Estados

Estados persistidos:

- `new` — nueva;
- `assigned` — asignada;
- `in_progress` — en gestión;
- `resolved` — resuelta;
- `dismissed` — descartada;
- `overdue` — vencida.

Una resolución exige resultado y un descarte exige motivo. Las transiciones
cerradas no pueden reabrirse en esta fase.

## Alertas iniciales

- caída de vendedor;
- objetivo en riesgo;
- cliente inactivo;
- caída abrupta de cliente;
- pérdida o profundidad crítica de mix;
- concentración elevada;
- categoría en retroceso;
- sincronización atrasada;
- divergencia MongoDB/ClickHouse.

Los umbrales comerciales pertenecen a la versión 1 de cada regla y toda alerta
incluye `evidence`. La categoría en retroceso reutiliza la señal estructurada
que ya genera el análisis actual.

## API

| Método | Ruta | Uso |
|---|---|---|
| GET | `/api/alerts` | lista alertas dentro del alcance |
| GET | `/api/alerts/history?alert_id=...` | historial autorizado |
| POST | `/api/alerts/generate` | genera/actualiza señales del período |
| POST | `/api/alerts/assign` | asigna responsable y vencimiento |
| POST | `/api/alerts/transition` | inicia, resuelve o descarta |

Todos los endpoints requieren sesión. Las mutaciones requieren CSRF y el
backend aplica el alcance antes de leer o modificar.

## Permisos

- `admin` y `commercial_director`: visibilidad y gestión total;
- `supervisor`: sólo vendedores de su alcance;
- `seller`: sólo sus alertas; puede gestionar y resolver, no reasignar ni
  descartar;
- `viewer`: sólo lectura.

Las alertas operativas sólo se muestran a roles con alcance global y se asignan
por defecto a dirección o administración. Las alertas de vendedor se asignan al
usuario activo vinculado por `seller_key`; si no existe, quedan bajo el rol
administrador, nunca sin responsable.

## Interfaz

Se agregó la vista `Alertas` al selector de dashboards. Tabulator muestra
prioridad, evidencia, vendedor/cliente, responsable, estado y vencimiento.
Desde la tabla se puede iniciar gestión, resolver con resultado o descartar con
motivo según el rol.

## Migración

```bash
.venv/bin/python scripts/migrate_phase7_alerts.py
```

La migración sólo crea/verifica colección e índices. No crea señales ni
modifica ventas, objetivos, sesiones o reglas.

Variable opcional:

| Variable | Propósito | Ejemplo |
|---|---|---|
| `ALERT_SYNC_MAX_AGE_HOURS` | edad máxima de la última sync antes de alertar | `30` |

## Pruebas

```bash
.venv/bin/python -m pytest -q tests/test_phase7_alerts.py
.venv/bin/python -m pytest -q
node --check static/app.js
```

Se cubren idempotencia, conservación de gestión humana, transiciones, historial,
aislamiento seller/supervisor, permisos del vendedor, atraso, divergencia y
vencimiento persistido.

## Validación manual

1. Ejecutar la migración y levantar la aplicación.
2. Generar un informe para un período con datos.
3. Abrir `Alertas` y pulsar `Actualizar alertas`.
4. Como admin/director, verificar todas las alertas y descartar una con motivo.
5. Como supervisor, comprobar que sólo aparezcan vendedores del alcance.
6. Como vendedor, iniciar y resolver una alerta propia; comprobar que no puede
   descartar ni ver otra cartera.
7. Como viewer, comprobar lectura sin botones de gestión.
8. Repetir la generación: no debe duplicar alertas y debe conservar el estado.
9. Consultar el historial de la alerta y verificar actor, fecha, estado y
   comentario/resultado.

## Rollback

1. Volver al código de FASE 6.
2. Conservar `commercial_alerts`; FASE 6 no la consulta.
3. Las alertas pueden exportarse antes de cualquier política futura de
   retención.
4. No es necesario revertir MongoDB, ventas, objetivos ni sincronizaciones.

## Riesgos pendientes

- Los umbrales deben validarse con negocio y versionarse en una futura
  configuración administrativa.
- `mix_loss` usa profundidad crítica disponible; para medir pérdida estricta de
  familias se requiere gobernar el histórico por categoría.
- No hay todavía notificaciones externas ni SLA escalonado.
- La reapertura de alertas cerradas requiere una política de auditoría.

## Próxima fase

La FASE 8 corresponde a preparación de reuniones y exportación PDF, seguida
posteriormente por PowerPoint. No fue implementada.
