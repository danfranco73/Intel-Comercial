from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDefinition:
    handler: str
    permission: str = "commercial.read"
    csrf: bool = False
    pass_parsed_url: bool = False


GET_ROUTES = {
    "/api/auth/me": RouteDefinition("handle_auth_me"),
    "/api/users": RouteDefinition("handle_list_users", permission="admin"),
    "/api/access/companies": RouteDefinition("handle_list_access_companies", permission="admin"),
    "/api/access/options": RouteDefinition("handle_access_scope_options", permission="admin"),
    "/api/session": RouteDefinition("handle_get_session"),
    "/api/schema": RouteDefinition("handle_schema", permission="admin", pass_parsed_url=True),
    "/api/possible-analyses": RouteDefinition("handle_possible_analyses", permission="admin", pass_parsed_url=True),
    "/api/analyses": RouteDefinition("handle_list_analyses", permission="admin", pass_parsed_url=True),
    "/api/workbook": RouteDefinition("handle_workbook", permission="admin", pass_parsed_url=True),
    "/api/preview": RouteDefinition("handle_preview", permission="admin", pass_parsed_url=True),
    "/api/erp/status": RouteDefinition("handle_erp_status", permission="admin"),
    "/api/erp/storage-status": RouteDefinition("handle_erp_storage_status", permission="admin"),
    "/api/clickhouse/storage-status": RouteDefinition("handle_clickhouse_storage_status", permission="admin"),
    "/api/sync/runs": RouteDefinition("handle_sync_runs", permission="admin", pass_parsed_url=True),
    "/api/sync/reconciliation": RouteDefinition("handle_sync_reconciliation", permission="admin", pass_parsed_url=True),
    "/api/data/freshness": RouteDefinition("handle_data_freshness"),
    "/api/health": RouteDefinition("handle_health", permission="admin"),
    "/api/objectives": RouteDefinition("handle_list_objectives", pass_parsed_url=True),
    "/api/objectives/history": RouteDefinition("handle_objective_history", pass_parsed_url=True),
    "/api/sales-coach/home": RouteDefinition("handle_sales_coach_home", pass_parsed_url=True),
    "/api/sales-coach/sellers": RouteDefinition("handle_sales_coach_sellers", pass_parsed_url=True),
    "/api/sales-coach/seller": RouteDefinition("handle_sales_coach_seller", pass_parsed_url=True),
    "/api/sales-coach/client": RouteDefinition("handle_sales_coach_client", pass_parsed_url=True),
    "/api/coach/rules": RouteDefinition("handle_coach_rules", permission="admin"),
    "/api/coach/audits": RouteDefinition("handle_coach_comment_audits", permission="admin", pass_parsed_url=True),
    "/api/alerts": RouteDefinition("handle_list_alerts", pass_parsed_url=True),
    "/api/alerts/history": RouteDefinition("handle_alert_history", pass_parsed_url=True),
    "/api/meetings": RouteDefinition("handle_list_meetings"),
    "/api/meetings/report": RouteDefinition("handle_get_meeting", pass_parsed_url=True),
    "/api/erp/prefilter-options": RouteDefinition("handle_erp_prefilter_options"),
    "/api/admin/errors": RouteDefinition("handle_admin_errors", permission="admin", pass_parsed_url=True),
}


POST_ROUTES = {
    "/api/auth/logout": RouteDefinition("handle_logout", csrf=True),
    "/api/users": RouteDefinition("handle_create_user", permission="admin", csrf=True),
    "/api/access/companies": RouteDefinition("handle_save_access_company", permission="admin", csrf=True),
    "/api/upload": RouteDefinition("handle_upload", permission="admin", csrf=True),
    "/api/clear-uploads": RouteDefinition("handle_clear_uploads", permission="admin", csrf=True),
    "/api/analyze": RouteDefinition("handle_analyze", csrf=True),
    "/api/bi/consistency": RouteDefinition("handle_bi_consistency", csrf=True),
    "/api/bi/tactical-month": RouteDefinition("handle_bi_tactical_month", csrf=True),
    "/api/analyze-dynamic": RouteDefinition("handle_analyze_dynamic", csrf=True),
    "/api/session": RouteDefinition("handle_save_session", csrf=True),
    "/api/erp/sync": RouteDefinition("handle_erp_sync", permission="admin", csrf=True),
    "/api/erp/sync-access-catalogs": RouteDefinition("handle_erp_sync_access_catalogs", permission="admin", csrf=True),
    "/api/objectives": RouteDefinition("handle_create_objective", permission="commercial.write", csrf=True),
    "/api/objectives/update": RouteDefinition("handle_update_objective", permission="commercial.write", csrf=True),
    "/api/objectives/approve": RouteDefinition("handle_approve_objective", permission="commercial.write", csrf=True),
    "/api/objectives/close": RouteDefinition("handle_close_objective", permission="commercial.write", csrf=True),
    "/api/coach/rules": RouteDefinition("handle_create_coach_rule_version", permission="admin", csrf=True),
    "/api/alerts/generate": RouteDefinition("handle_generate_alerts", csrf=True),
    "/api/alerts/assign": RouteDefinition("handle_assign_alert", csrf=True),
    "/api/alerts/transition": RouteDefinition("handle_transition_alert", csrf=True),
    "/api/meetings": RouteDefinition("handle_create_meeting", csrf=True),
    "/api/meetings/pdf": RouteDefinition("handle_meeting_pdf", csrf=True),
    "/api/meetings/pptx": RouteDefinition("handle_meeting_pptx", csrf=True),
}
