from __future__ import annotations

from typing import Any

from sales_coach.repositories.auth_repository import AuthRepository
from sales_coach.schemas import LoginRequest, UserCreateRequest
from security.authorization import require_permission, resolve_data_scope
from security.models import AuthContext


class AuthService:
    def __init__(self, repository: AuthRepository):
        self.repository = repository

    def login(
        self,
        payload: Any,
        *,
        ip_address: str,
        user_agent: str,
    ):
        request = LoginRequest.parse(payload)
        return self.repository.authenticate(
            request.email,
            request.password,
            ip_address,
            user_agent,
        )

    def authenticate_request(
        self,
        cookie_header: str | None,
        permission: str,
    ) -> tuple[AuthContext, dict[str, list[str]]]:
        context = self.repository.resolve_session(cookie_header)
        if context is None:
            raise LookupError("Autenticación requerida.")
        require_permission(context.user, permission)
        return context, resolve_data_scope(context.user, self.repository.db)

    def create_user(self, payload: Any) -> dict[str, Any]:
        request = UserCreateRequest.parse(payload)
        return self.repository.create_user(request.as_repository_payload())
