from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


VALID_ROLES = {
    "admin",
    "commercial_director",
    "supervisor",
    "seller",
    "viewer",
}


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str
    name: str
    role: str
    seller_key: str | None = None
    supervisor_key: str | None = None
    branch_keys: tuple[str, ...] = field(default_factory=tuple)
    sales_force_keys: tuple[str, ...] = field(default_factory=tuple)
    company_key: str | None = None
    business_units: tuple[str, ...] = field(default_factory=tuple)
    seller_keys: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "AuthenticatedUser":
        role = str(document.get("role") or "")
        if role not in VALID_ROLES:
            raise ValueError("El usuario tiene un rol inválido")
        return cls(
            id=str(document["_id"]),
            email=str(document.get("email") or ""),
            name=str(document.get("name") or ""),
            role=role,
            seller_key=_optional_text(document.get("seller_key")),
            supervisor_key=_optional_text(document.get("supervisor_key")),
            branch_keys=tuple(_string_list(document.get("branch_keys"))),
            sales_force_keys=tuple(_string_list(document.get("sales_force_keys"))),
            company_key=_optional_text(document.get("company_key")),
            business_units=tuple(_string_list(document.get("business_units"))),
            seller_keys=tuple(_string_list(document.get("seller_keys"))),
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "sellerKey": self.seller_key,
            "supervisorKey": self.supervisor_key,
            "branchKeys": list(self.branch_keys),
            "salesForceKeys": list(self.sales_force_keys),
            "companyKey": self.company_key,
            "businessUnits": list(self.business_units),
            "sellerKeys": list(self.seller_keys),
        }


@dataclass(frozen=True)
class AuthContext:
    user: AuthenticatedUser
    session_id: str
    csrf_token: str


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
