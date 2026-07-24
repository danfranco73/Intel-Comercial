from __future__ import annotations

from urllib.parse import parse_qs

from sales_coach.services import SalesCoachService


class SalesCoachRoutesMixin:
    def _sales_coach_service(self):
        return SalesCoachService(self.database())

    def handle_sales_coach_home(self, parsed):
        self._sales_coach_query(parsed, "home")

    def handle_sales_coach_sellers(self, parsed):
        self._sales_coach_query(parsed, "sellers")

    def handle_sales_coach_seller(self, parsed):
        query = parse_qs(parsed.query)
        seller_key = str(query.get("sellerKey", [""])[0]).strip()
        if not seller_key:
            self.send_json({"error": "sellerKey es obligatorio"}, status=400)
            return
        self._sales_coach_query(parsed, "seller_detail", seller_key)

    def handle_sales_coach_client(self, parsed):
        query = parse_qs(parsed.query)
        client_key = str(query.get("clientKey", [""])[0]).strip()
        if not client_key:
            self.send_json({"error": "clientKey es obligatorio"}, status=400)
            return
        self._sales_coach_query(parsed, "client_detail", client_key)

    def _sales_coach_query(self, parsed, operation, entity_key=None):
        query = parse_qs(parsed.query)
        start = str(query.get("fechaDesde", [""])[0]).strip()
        end = str(query.get("fechaHasta", [""])[0]).strip()
        if not start or not end:
            self.send_json({"error": "Faltan fechaDesde o fechaHasta"}, status=400)
            return
        service = self._sales_coach_service()
        try:
            if operation == "home":
                result = service.home(
                    start, end, self.auth_context.user, self.data_scope
                )
            elif operation == "sellers":
                result = service.sellers(start, end, self.data_scope)
            else:
                result = getattr(service, operation)(
                    entity_key,
                    start,
                    end,
                    self.auth_context.user,
                    self.data_scope,
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
            self.log_application_error(f"sales_coach_{operation}", exc)
            self.send_json({"error": "No se pudo construir Sales Coach"}, status=500)
            return
        self.send_json(result)
