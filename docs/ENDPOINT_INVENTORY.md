# Inventario de endpoints — FASE 0/1

Todas las respuestas son JSON salvo páginas/assets. Las APIs autenticadas usan cookie `codenoa_session`. Todo POST autenticado exige `X-CSRF-Token`.

| Método | Ruta | Propósito | Acceso | Payload/parámetros | Responsable |
|---|---|---|---|---|---|
| GET | `/login` | formulario de acceso | Público | — | `AppHandler.do_GET` |
| POST | `/api/auth/login` | crear sesión | Público + rate limit | `{email,password}` | `handle_login` |
| GET | `/api/auth/me` | usuario y CSRF rotado | Autenticado | — | `handle_auth_me` |
| POST | `/api/auth/logout` | revocar sesión | Autenticado + CSRF | `{}` | `handle_logout` |
| GET | `/`, `/bi` | dashboard comercial | `commercial.read` | — | `do_GET` |
| GET | `/admin` | operación/ingestión | `admin` | — | `do_GET` |
| GET | `/api/session` | configuración del usuario | `commercial.read` | — | `handle_get_session` |
| POST | `/api/session` | guardar configuración | `commercial.read` + CSRF | `{datasets,planning}` | `handle_save_session` |
| GET | `/api/datasets` | esquema de datasets | `commercial.read` | — | `build_dataset_schema` |
| GET | `/api/erp/prefilter-options` | prefiltros autorizados | `commercial.read` | — | `handle_erp_prefilter_options` |
| POST | `/api/analyze` | informe completo | `commercial.read` + CSRF | `{datasets,filters,supplierFocus,planning}` | `handle_analyze` |
| POST | `/api/analyze-dynamic` | tarea analítica | `commercial.read` + CSRF | `{datasets,filters,task_id,combo}` | `handle_analyze_dynamic` |
| POST | `/api/bi/consistency` | consistencia scoped | `commercial.read` + CSRF | `{datasets}` | `handle_bi_consistency` |
| POST | `/api/bi/tactical-month` | dashboard táctico | `commercial.read` + CSRF | `{datasets,filters,planning}` | `handle_bi_tactical_month` |
| GET | `/api/files` | biblioteca | `admin` | `scope` | `do_GET` |
| POST | `/api/upload` | cargar Excel | `admin` + CSRF | multipart `files` | `handle_upload` |
| POST | `/api/clear-uploads` | limpiar uploads | `admin` + CSRF | `{}` | `handle_clear_uploads` |
| GET | `/api/workbook` | hojas de archivo | `admin` | `file` | `handle_workbook` |
| GET | `/api/preview` | previsualizar hoja | `admin` | `file,sheet,datasetType` | `handle_preview` |
| GET | `/api/schema` | detectar esquema | `admin` | `file,sheet,headerRow` | `handle_schema` |
| GET | `/api/possible-analyses` | análisis posibles | `admin` | `file,sheet,headerRow` | `handle_possible_analyses` |
| GET | `/api/analyses` | ejecuciones registradas | `admin` | `limit` | `handle_list_analyses` |
| GET | `/api/erp/status` | salud Chess | `admin` | — | `handle_erp_status` |
| GET | `/api/erp/storage-status` | salud Mongo/ERP | `admin` | — | `handle_erp_storage_status` |
| GET | `/api/clickhouse/storage-status` | salud ClickHouse | `admin` | — | `handle_clickhouse_storage_status` |
| GET | `/api/admin/errors` | errores recientes | `admin` | `limit` | `handle_admin_errors` |
| POST | `/api/db-status` | ping Mongo | `admin` + CSRF | `{}` | `do_POST` |
| POST | `/api/erp/sync` | sincronizar Chess | `admin` + CSRF | `{fechaDesde,fechaHasta,refreshMasters,forceRefreshSales}` | `handle_erp_sync` |
| GET | `/api/users` | listar usuarios sin hashes | `admin` | — | `handle_list_users` |
| POST | `/api/users` | crear usuario | `admin` + CSRF | modelo de usuario + `password` | `handle_create_user` |
| GET | `/api/data/freshness` | frescura comercial | `commercial.read` | — | `handle_data_freshness` |
| GET | `/api/sync/runs` | corridas de sincronización | `admin` | `limit` | `handle_sync_runs` |
| GET | `/api/sync/reconciliation` | conciliación Mongo/ClickHouse | `admin` | `fechaDesde,fechaHasta` | `handle_sync_reconciliation` |
| GET | `/api/health` | salud integral | `admin` | — | `handle_health` |
| GET | `/api/objectives` | objetivos visibles y cumplimiento | `commercial.read` | `period,status,includeProgress` | `handle_list_objectives` |
| GET | `/api/objectives/history` | versiones de un objetivo | `commercial.read` | `objective_id` | `handle_objective_history` |
| POST | `/api/objectives` | crear objetivo borrador | `commercial.write` + CSRF | modelo de objetivo | `handle_create_objective` |
| POST | `/api/objectives/update` | versionar borrador | `commercial.write` + CSRF | `objective_id` + cambios | `handle_update_objective` |
| POST | `/api/objectives/approve` | aprobar objetivo | dirección/admin + CSRF | `objective_id` | `handle_approve_objective` |
| POST | `/api/objectives/close` | cerrar objetivo | según alcance + CSRF | `objective_id` | `handle_close_objective` |
| GET | `/api/sales-coach/home` | home ejecutivo Sales Coach | `commercial.read` | `fechaDesde,fechaHasta` | `handle_sales_coach_home` |
| GET | `/api/sales-coach/sellers` | ranking multidimensional | `commercial.read` | `fechaDesde,fechaHasta` | `handle_sales_coach_sellers` |
| GET | `/api/sales-coach/seller` | ficha de vendedor | `commercial.read` | `sellerKey,fechaDesde,fechaHasta` | `handle_sales_coach_seller` |
| GET | `/api/sales-coach/client` | ficha de cliente | `commercial.read` | `clientKey,fechaDesde,fechaHasta` | `handle_sales_coach_client` |
| GET | `/api/coach/rules` | configuración activa de comentarios | `admin` | — | `handle_coach_rules` |
| POST | `/api/coach/rules` | crear versión de reglas | `admin` + CSRF | `entity_type,rules,notes` | `handle_create_coach_rule_version` |
| GET | `/api/coach/audits` | auditoría de comentarios | `admin` | `limit` | `handle_coach_comment_audits` |

## Respuesta de autenticación

```json
{
  "user": {
    "id": "...",
    "email": "persona@example.test",
    "name": "Persona",
    "role": "seller",
    "sellerKey": "S1",
    "supervisorKey": null,
    "branchKeys": [],
    "salesForceKeys": []
  },
  "csrfToken": "token-efímero"
}
```

El token de sesión nunca aparece en JSON ni JavaScript: sólo se entrega como cookie HttpOnly.
