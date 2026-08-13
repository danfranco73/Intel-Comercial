from __future__ import annotations

from typing import Any

from sales_coach.repositories.auth_repository import AuthRepository
from sales_coach.schemas import (
    LoginRequest,
    UserCreateRequest,
    UserPasswordResetRequest,
    UserStatusRequest,
)
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

    def set_user_active(self, payload: Any, actor_id: str) -> dict[str, Any]:
        request = UserStatusRequest.parse(payload)
        if not request.is_active and request.user_id == actor_id:
            raise ValueError("No podés desactivar tu propio usuario")
        return self.repository.set_user_active(request.user_id, request.is_active)

    def reset_user_password(self, payload: Any) -> dict[str, Any]:
        request = UserPasswordResetRequest.parse(payload)
        return self.repository.reset_password(request.user_id, request.new_password)
