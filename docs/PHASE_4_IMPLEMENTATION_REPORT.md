# Informe de implementación — FASE 4

Fecha: 23/07/2026  
Rama: `feature/codenoa-sales-coach`

## Resumen

Se implementaron objetivos comerciales persistentes, versionados, autorizados y
comparables contra métricas gobernadas. El simulador anterior se conserva para
compatibilidad, pero deja de ser la fuente formal del objetivo.

No se implementaron fichas Sales Coach, comentarios automáticos, alertas,
exportaciones ni IA.

## Modelo

Colección `commercial_objectives`:

- `objective_id` estable y `version` incremental;
- `current` identifica la versión vigente;
- empresa, período, alcance y métrica;
- meta y baseline `Decimal128`;
- creador, aprobador y cerrador;
- timestamps y notas;
- estados `draft`, `active`, `closed`.

Editar un borrador conserva la versión anterior. Un objetivo activo no puede
editarse: debe cerrarse y crearse uno nuevo.

## Autorización

- `admin` y `commercial_director`: todos los objetivos; pueden aprobar/cerrar.
- `supervisor`: sólo vendedores, supervisor, sucursales o fuerzas asignadas;
  puede crear/editar y cerrar objetivos de vendedor.
- `seller`: sólo consulta objetivos asociados a su `seller_key`.
- `viewer`: sólo consulta objetivos de empresa.
- Todo alcance se resuelve en backend.

## APIs

| Método | Ruta | Función |
|---|---|---|
| GET | `/api/objectives` | listar con progreso autorizado |
| GET | `/api/objectives/history` | historial de versiones |
| POST | `/api/objectives` | crear borrador |
| POST | `/api/objectives/update` | crear nueva versión del borrador |
| POST | `/api/objectives/approve` | aprobar |
| POST | `/api/objectives/close` | cerrar |

## Archivos principales

| Archivo | Acción | Motivo |
|---|---|---|
| `sales_coach/schemas/objectives.py` | nuevo | validación tipada |
| `sales_coach/repositories/objective_repository.py` | nuevo | persistencia/versiones |
| `sales_coach/services/objective_service.py` | nuevo | autorización y cumplimiento |
| `sales_coach/routes/objective_routes.py` | nuevo | transporte HTTP |
| `scripts/migrate_phase4_objectives.py` | nuevo | migración compatible |
| `static/app.js` | modificado | consulta, progreso y gobierno desde simulador |
| `tests/test_phase4_objectives.py` | nuevo | ciclo, métricas y aislamiento |

## Migración

La migración toma únicamente `planning.budget.monthlyTotals`, elige el valor más
reciente por período y crea un objetivo empresa/venta neta en `draft`.

No convierte:

- porcentajes globales;
- uplift por segmento;
- porcentajes por vendedor;
- participaciones.

Esos valores no son importes gobernados y convertirlos inventaría metas.

La migración fue ejecutada en el entorno conectado:

- 0 presupuestos mensuales convertibles encontrados;
- 0 objetivos creados;
- colección e índices creados correctamente;
- ninguna sesión ni venta modificada.

Índices verificados: `objective_version_unique`, `objective_current_unique`,
`objective_lookup`, `objective_status_period` y `objective_migration_unique`.

## Pruebas

Resultado integral: 31 tests aprobados. Se validaron además compilación Python,
sintaxis JavaScript y whitespace del diff.

## Rollback

1. Volver al código anterior: éste ignora `commercial_objectives`.
2. Conservar la colección para no perder historial.
3. Los documentos migrados se identifican con `migration_key` que comienza con
   `phase4:session-budget:`.
4. Si negocio decide descartarlos, exportarlos y eliminarlos por esa clave. No
   modificar `sessions`, `erp_sales` ni ClickHouse.

## Riesgos pendientes

- `new_clients` y `recovered_clients` dependen del histórico disponible.
- Marca/familia dependen de cobertura de `erp_articles`.
- El cálculo inicial usa MongoDB; una tabla agregada ClickHouse gobernada debe
  incorporarse con comparación dual antes de cambiar la fuente.
- Falta designar formalmente al responsable de negocio del catálogo.

## Próxima fase

La FASE 5 corresponde a la experiencia central Sales Coach y fichas de vendedor
y cliente. No fue implementada.
