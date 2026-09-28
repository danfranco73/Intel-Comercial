from __future__ import annotations

from urllib.parse import parse_qs

from sales_coach.services.pdv_360_service import Pdv360Service

DEFAULT_WINDOW_DAYS = 90


class PdvRoutesMixin:
    def _pdv_service(self):
        return Pdv360Service(self.database())

    def handle_pdv_search(self, parsed):
        query = parse_qs(parsed.query)
        text = str(query.get("q", [""])[0]).strip()
        try:
            result = self._pdv_service().search(text, self.auth_context.user, self.data_scope)
        except Exception as exc:
            self.log_application_error("pdv_search", exc)
            self.send_json({"error": "No se pudo buscar el PDV"}, status=500)
            return
        self.send_json(result)

    def handle_pdv_360(self, parsed):
        query = parse_qs(parsed.query)
        client_key = str(query.get("clientKey", [""])[0]).strip()
        fecha_hasta = str(query.get("fechaHasta", [""])[0]).strip() or None
        try:
            window_days = int(str(query.get("windowDays", [DEFAULT_WINDOW_DAYS])[0]).strip() or DEFAULT_WINDOW_DAYS)
            result = self._pdv_service().build(
                client_key, fecha_hasta, window_days, self.auth_context.user, self.data_scope
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
            self.log_application_error("pdv_360", exc)
            self.send_json({"error": "No se pudo construir la vista 360 del PDV"}, status=500)
            return
        self.send_json(result)

    def handle_pdv_portfolio_options(self):
        try:
            result = self._pdv_service().portfolio_options(self.auth_context.user, self.data_scope)
        except Exception as exc:
            self.log_application_error("pdv_portfolio_options", exc)
            self.send_json({"error": "No se pudieron cargar los filtros de cartera"}, status=500)
            return
        self.send_json(result)

    def handle_pdv_portfolio(self, parsed):
        query = parse_qs(parsed.query)
        value = lambda key: str(query.get(key, [""])[0]).strip()
        filters = {key: value(key) for key in ("seller", "route", "salesForce", "businessType")}
        try:
            window_days = int(value("windowDays") or DEFAULT_WINDOW_DAYS)
            result = self._pdv_service().portfolio(
                filters, value("fechaHasta") or None, window_days, self.auth_context.user, self.data_scope
            )
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        except Exception as exc:
            self.log_application_error("pdv_portfolio", exc)
            self.send_json({"error": "No se pudo construir la cartera"}, status=500)
            return
        self.send_json(result)
