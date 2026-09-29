from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs
from sales_coach.repositories.deposit_repository import DepositRepository
from sales_coach.services.intelligence_sales_service import IntelligenceSalesService
from sales_coach.services.intelligence_stock_service import IntelligenceStockService
from sales_coach.services.stock_sync_service import StockSyncService


class IntelligenceRoutesMixin:
    def _intelligence_call(self, operation):
        try:
            result = operation()
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, status=403)
        except LookupError as exc:
            self.send_json({"error": str(exc)}, status=404)
        except (ValueError, TypeError) as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception:
            # Never return upstream response bodies, connection strings or credentials.
            self.send_json({"error": "Intelligence no disponible", "code": "INTELLIGENCE_UNAVAILABLE"}, status=503)
        else:
            self.send_json(result, headers={"Cache-Control": "no-store"})

    @staticmethod
    def _intelligence_query(parsed):
        values = parse_qs(parsed.query, keep_blank_values=True)
        if any(len(v) != 1 for v in values.values()):
            raise ValueError("Parámetros repetidos")
        return {k: v[0] for k, v in values.items()}

    def handle_intelligence_sales(self, parsed):
        self._intelligence_call(lambda: IntelligenceSalesService(self.database()).brief(
            self._intelligence_query(parsed), self.auth_context.user))

    def handle_intelligence_drilldown(self, parsed):
        self._intelligence_call(lambda: IntelligenceSalesService(self.database()).drilldown(
            self._intelligence_query(parsed), self.auth_context.user))

    def handle_intelligence_stock(self, parsed):
        self._intelligence_call(lambda: IntelligenceStockService(self.database()).brief(
            self._intelligence_query(parsed), self.auth_context.user))

    def handle_intelligence_stock_items(self, parsed):
        self._intelligence_call(lambda: IntelligenceStockService(self.database()).items(
            self._intelligence_query(parsed), self.auth_context.user))

    def handle_intelligence_deposits(self):
        self._intelligence_call(lambda: DepositRepository(self.database()).configuration())

    def handle_intelligence_deposit_discovery(self):
        def discover():
            payload = self._read_json_body()
            if payload != {}:
                raise ValueError("Enviar un objeto vacío")
            db = self.database()
            repository = DepositRepository(db)
            repository.discover(list(db["erp_deposits"].find({}, {"_id": 0})), "sales_catalog")
            return repository.configuration()
        self._intelligence_call(discover)

    def handle_intelligence_deposit_save(self):
        self._intelligence_call(lambda: DepositRepository(self.database()).configure(
            self._read_json_body(), self.auth_context.user.id))

    def handle_intelligence_universe(self):
        self._intelligence_call(lambda: DepositRepository(self.database()).certify(
            self._read_json_body(), self.auth_context.user.id))

    def handle_intelligence_stock_sync(self):
        def run():
            payload = self._read_json_body()
            if not isinstance(payload, dict) or set(payload) - {"stock_date"}:
                raise ValueError("Sólo se admite stock_date; el universo proviene del catálogo")
            day = payload.get("stock_date") or datetime.now(ZoneInfo("America/Argentina/Cordoba")).date().isoformat()
            return StockSyncService(self.database()).run(day, self.auth_context.user.id)
        self._intelligence_call(run)
