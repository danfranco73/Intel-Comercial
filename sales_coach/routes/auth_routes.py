from __future__ import annotations

from security.audit import audit_security_event
from security.auth import build_clear_cookie, build_session_cookie


class AuthRoutesMixin:
    def handle_login(self):
        email = ""
        try:
            data = self._read_json_body()
            email = str(data.get("email") or "").strip().lower()
            service = self._auth_service()
            if service is None:
                self.send_json({"error": "MongoDB no está configurado."}, status=503)
                return
            context, token = service.login(
                data,
                ip_address=self.request_ip(),
                user_agent=self.headers.get("User-Agent", ""),
            )
            self.send_json(
                {"user": context.user.public_dict(), "csrfToken": context.csrf_token},
                headers={"Set-Cookie": build_session_cookie(token)},
            )
            audit_security_event(
                "login",
                outcome="success",
                user_id=context.user.id,
                ip_address=self.request_ip(),
                details={"email": context.user.email, "role": context.user.role},
            )
        except PermissionError as exc:
            audit_security_event(
                "login",
                outcome="rejected",
                ip_address=self.request_ip(),
                details={
                    "email": email,
                    "reason": "rate_limited" if "Demasiados" in str(exc) else "invalid_credentials",
                },
            )
            self.send_json(
                {"error": str(exc)},
                status=429 if "Demasiados" in str(exc) else 401,
            )
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self.log_application_error("auth_login", exc)
            self.send_json({"error": "No se pudo iniciar sesión."}, status=500)

    def handle_auth_me(self):
        token = self._auth_service().repository.issue_csrf(self.auth_context)
        self.send_json({"user": self.auth_context.user.public_dict(), "csrfToken": token})

    def handle_logout(self):
        self._auth_service().repository.revoke_session(self.auth_context)
        audit_security_event(
            "logout",
            outcome="success",
            user_id=self.auth_context.user.id,
            ip_address=self.request_ip(),
        )
        self.send_json(
            {"loggedOut": True},
            headers={"Set-Cookie": build_clear_cookie()},
        )

    def handle_list_users(self):
        self.send_json({"users": self._auth_service().repository.list_users()})

    def handle_create_user(self):
        try:
            result = self._auth_service().create_user(self._read_json_body())
            audit_security_event(
                "user_created",
                outcome="success",
                user_id=self.auth_context.user.id,
                ip_address=self.request_ip(),
                details={"createdUserId": result["id"], "role": result["role"]},
            )
            self.send_json({"user": result}, status=201)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)

    def handle_update_user_status(self):
        try:
            payload = self._read_json_body()
            result = self._auth_service().set_user_active(payload, self.auth_context.user.id)
            audit_security_event(
                "user_status_changed",
                outcome="success",
                user_id=self.auth_context.user.id,
                ip_address=self.request_ip(),
                details={"targetUserId": result["id"], "isActive": result["is_active"]},
            )
            self.send_json({"user": result})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)

    def handle_reset_user_password(self):
        try:
            payload = self._read_json_body()
            result = self._auth_service().reset_user_password(payload)
            audit_security_event(
                "user_password_reset",
                outcome="success",
                user_id=self.auth_context.user.id,
                ip_address=self.request_ip(),
                details={"targetUserId": result["id"]},
            )
            self.send_json({"user": result})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)

    def handle_list_access_companies(self):
        repository = self._auth_service().repository
        self.send_json({"companies": repository.list_access_companies()})

    def handle_access_scope_options(self):
        repository = self._auth_service().repository
        self.send_json(repository.access_scope_options())

    def handle_save_access_company(self):
        try:
            company = self._auth_service().repository.save_access_company(
                self._read_json_body(),
                self.auth_context.user.id,
            )
            self.send_json({"company": company}, status=201)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
