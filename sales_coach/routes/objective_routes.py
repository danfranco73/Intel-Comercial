from __future__ import annotations

from urllib.parse import parse_qs

from sales_coach.services import ObjectiveService


class ObjectiveRoutesMixin:
    def _objective_service(self):
        return ObjectiveService(self.database())

    def handle_list_objectives(self, parsed):
        query = parse_qs(parsed.query)
        period = str(query.get("period", [""])[0]).strip() or None
        status = str(query.get("status", [""])[0]).strip() or None
        include_progress = str(query.get("includeProgress", ["true"])[0]).lower() not in {
            "0",
            "false",
            "no",
        }
        try:
            objectives = self._objective_service().list(
                self.auth_context.user,
                self.data_scope,
                period=period,
                status=status,
                include_progress=include_progress,
            )
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        self.send_json({"objectives": objectives})

    def handle_objective_history(self, parsed):
        query = parse_qs(parsed.query)
        objective_id = str(query.get("objective_id", [""])[0]).strip()
        if not objective_id:
            self.send_json({"error": "objective_id es obligatorio"}, status=400)
            return
        try:
            history = self._objective_service().history(
                objective_id, self.auth_context.user, self.data_scope
            )
        except LookupError as exc:
            self.send_json({"error": str(exc)}, status=404)
            return
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, status=403)
            return
        self.send_json({"history": history})

    def handle_create_objective(self):
        self._objective_mutation("create", created_status=201)

    def handle_update_objective(self):
        self._objective_mutation("update")

    def handle_approve_objective(self):
        self._objective_mutation("approve")

    def handle_close_objective(self):
        self._objective_mutation("close")

    def _objective_mutation(self, operation: str, created_status: int = 200):
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        try:
            result = getattr(self._objective_service(), operation)(
                payload, self.auth_context.user, self.data_scope
            )
        except LookupError as exc:
            self.send_json({"error": str(exc)}, status=404)
            return
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, status=403)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        except Exception as exc:
            self.log_application_error(f"objective_{operation}", exc)
            self.send_json({"error": "No se pudo procesar el objetivo"}, status=500)
            return
        self.send_json({"objective": result}, status=created_status)
