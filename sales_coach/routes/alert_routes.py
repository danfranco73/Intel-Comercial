from __future__ import annotations

from urllib.parse import parse_qs

from sales_coach.services.alert_service import AlertService


class AlertRoutesMixin:
    def _alert_service(self):
        return AlertService(self.database())

    def handle_list_alerts(self, parsed):
        query = parse_qs(parsed.query)
        try:
            alerts = self._alert_service().list(
                self.auth_context.user,
                self.data_scope,
                status=str(query.get("status", [""])[0]).strip() or None,
                alert_type=str(query.get("type", [""])[0]).strip() or None,
                period=str(query.get("period", [""])[0]).strip() or None,
            )
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        self.send_json({"alerts": alerts})

    def handle_alert_history(self, parsed):
        query = parse_qs(parsed.query)
        alert_id = str(query.get("alert_id", [""])[0]).strip()
        if not alert_id:
            self.send_json({"error": "alert_id es obligatorio"}, status=400)
            return
        try:
            history = self._alert_service().history(
                alert_id, self.auth_context.user, self.data_scope
            )
        except LookupError as exc:
            self.send_json({"error": str(exc)}, status=404)
            return
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, status=403)
            return
        self.send_json({"history": history})

    def handle_generate_alerts(self):
        self._alert_mutation("generate", response_key="generation")

    def handle_assign_alert(self):
        self._alert_mutation("assign")

    def handle_transition_alert(self):
        self._alert_mutation("transition")

    def _alert_mutation(self, operation, response_key="alert"):
        try:
            payload = self._read_json_body()
            result = getattr(self._alert_service(), operation)(
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
            self.log_application_error(f"alert_{operation}", exc)
            self.send_json({"error": "No se pudo procesar la alerta"}, status=500)
            return
        self.send_json({response_key: result})
