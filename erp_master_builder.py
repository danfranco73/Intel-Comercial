from __future__ import annotations


def derive_sellers_records(sales_records: list[dict]) -> list[dict]:
    sellers = {}
    for row in sales_records:
        seller_key = row.get("seller_key")
        seller_name = row.get("seller_name")
        sales_force = row.get("sales_force")
        route_description = row.get("route_description")
        if not seller_key and not seller_name:
            continue
        key = seller_key or normalize_key(seller_name)
        current = sellers.get(key, {})
        sellers[key] = {
            "seller_key": seller_key or current.get("seller_key"),
            "seller_name": seller_name or current.get("seller_name") or seller_key,
            "route_description": route_description or current.get("route_description"),
            "sales_force": sales_force or current.get("sales_force"),
            "source": "ChessERP",
        }
    return sorted(sellers.values(), key=lambda item: (item.get("seller_name") or "", item.get("seller_key") or ""))


def derive_routes_records(sales_records: list[dict]) -> list[dict]:
    routes = {}
    for row in sales_records:
        seller_name = row.get("seller_name")
        route_description = row.get("route_description")
        sales_force = row.get("sales_force")
        if not seller_name and not route_description:
            continue
        key = normalize_key(route_description or seller_name)
        current = routes.get(key, {})
        routes[key] = {
            "sales_force": sales_force or current.get("sales_force"),
            "seller_name": seller_name or current.get("seller_name") or route_description,
            "route_description": route_description or current.get("route_description") or seller_name,
            "source": "ChessERP",
        }
    return sorted(routes.values(), key=lambda item: (item.get("route_description") or "", item.get("seller_name") or ""))


def build_dataset(dataset_type: str, file_label: str, sheet_label: str, records: list[dict], source_kind: str = "mongo") -> dict:
    return {
        "datasetType": dataset_type,
        "sourceKind": source_kind,
        "file": file_label,
        "sheet": sheet_label,
        "headerRow": 0,
        "rowsRead": len(records),
        "rowsValid": len(records),
        "headers": [],
        "mapping": {},
        "records": records,
    }


def normalize_key(value):
    return str(value or "").strip().lower()
