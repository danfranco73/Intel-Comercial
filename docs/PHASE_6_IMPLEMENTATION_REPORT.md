# Informe de implementación — FASE 6

Fecha: 23/07/2026  
Rama: `feature/codenoa-sales-coach`

## Resumen

Se reemplazaron los comentarios embebidos de FASE 5 por un motor determinista,
versionado, configurable y auditable. No se incorporó IA.

## Archivos

| Archivo | Acción | Motivo |
|---|---|---|
| `sales_coach/domain/comment_engine.py` | nuevo | evaluación segura y priorización |
| `sales_coach/domain/default_coach_rules.py` | nuevo | catálogo inicial |
| `sales_coach/repositories/coach_rule_repository.py` | nuevo | versiones y auditorías |
| `sales_coach/routes/coach_rule_routes.py` | nuevo | administración de reglas |
| `sales_coach/services/sales_coach_service.py` | modificado | evaluación y auditoría de ficha |
| `bi/focus_dashboards.py` | modificado | métricas fuente y eliminación de reglas embebidas |
| `scripts/migrate_phase6_coach_rules.py` | nuevo | índices y seed idempotente |
| `static/app.js` | modificado | versión/auditoría visibles |
| `tests/test_phase6_comment_engine.py` | nuevo | condiciones, conflictos y versiones |

## Base de datos

### `coach_rule_sets`

Versiones inmutables del catálogo por tipo de entidad.

### `coach_comment_audits`

Resultado auditable por usuario, entidad, período y versión.

Índices:

- rule set + versión único;
- entidad + activo + versión;
- audit ID único;
- entidad/usuario + fecha.

La migración fue ejecutada:

- `seller_comments` versión 1 activa;
- 17 reglas iniciales;
- índices de rule sets y auditorías verificados;
- ninguna venta, sesión u objetivo modificado.

## Resolución

1. Se evalúan sólo reglas habilitadas.
2. Se descartan reglas sin evidencia completa.
3. Se ordenan por prioridad.
4. Se conserva sólo la prioridad mayor por `conflict_group`.
5. Se limita a tres fortalezas y tres oportunidades.
6. El plan usa las oportunidades ya seleccionadas.

## Seguridad

- Consultar reglas y auditorías requiere `admin`.
- Crear una versión requiere `admin` y CSRF.
- Las fichas siguen sujetas al alcance de FASE 5.
- No se ejecuta contenido configurable como código.

## Pruebas

Resultado integral: 43 tests aprobados. Se validaron cambios de comentario al
cambiar KPIs, evidencia obligatoria, límites, prioridades, conflictos,
versionado, auditoría, aislamiento comercial, compilación Python y JavaScript.

## Rollback

1. Volver al código de FASE 5.
2. Conservar ambas colecciones; el código anterior las ignora.
3. La configuración por defecto también existe en código como fallback.
4. No eliminar auditorías sin una política de retención aprobada.

## Próxima fase

La FASE 7 corresponde a alertas comerciales persistentes y gestionables. No fue
implementada.
