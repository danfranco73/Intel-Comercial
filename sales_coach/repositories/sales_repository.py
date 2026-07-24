from __future__ import annotations

from clickhouse_client import load_erp_sales_dataset_clickhouse
from mongo_client import load_erp_sales_dataset


class SalesRepository:
    def load_mongo(self, fecha_desde: str, fecha_hasta: str, require_coverage: bool = True):
        return load_erp_sales_dataset(fecha_desde, fecha_hasta, require_coverage=require_coverage)

    def load_clickhouse(self, fecha_desde: str, fecha_hasta: str):
        return load_erp_sales_dataset_clickhouse(fecha_desde, fecha_hasta)

    def load_preferred(self, fecha_desde: str, fecha_hasta: str):
        mongo_error = None
        clickhouse_error = None
        try:
            return self.load_mongo(fecha_desde, fecha_hasta, require_coverage=True), "mongo"
        except Exception as exc:
            mongo_error = exc
        try:
            return self.load_clickhouse(fecha_desde, fecha_hasta), "clickhouse"
        except Exception as exc:
            clickhouse_error = exc
        if mongo_error and not clickhouse_error:
            raise ValueError(str(mongo_error))
        if clickhouse_error and not mongo_error:
            raise ValueError(str(clickhouse_error))
        raise ValueError(
            f"La base comercial no tiene información disponible para el rango "
            f"{fecha_desde} a {fecha_hasta}."
        )
