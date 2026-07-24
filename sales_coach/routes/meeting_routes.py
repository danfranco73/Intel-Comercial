from __future__ import annotations

from urllib.parse import parse_qs

from sales_coach.services.meeting_service import MeetingService


class MeetingRoutesMixin:
    def _meeting_service(self):
        return MeetingService(self.database())

    def handle_list_meetings(self):
        self.send_json(
            {"reports": self._meeting_service().list(self.auth_context.user, self.data_scope)}
        )

    def handle_get_meeting(self, parsed):
        report_id = str(parse_qs(parsed.query).get("report_id", [""])[0]).strip()
        if not report_id:
            self.send_json({"error": "report_id es obligatorio"}, status=400)
            return
        try:
            report = self._meeting_service().get(
                report_id, self.auth_context.user, self.data_scope
            )
        except LookupError as exc:
            self.send_json({"error": str(exc)}, status=404)
            return
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, status=403)
            return
        self.send_json({"report": report})

    def handle_create_meeting(self):
        try:
            payload = self._read_json_body()
            report = self._meeting_service().create(
                payload, self.auth_context.user, self.data_scope
            )
        except PermissionError as exc:
            self.send_json({"error": str(exc)}, status=403)
            return
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        except Exception as exc:
            self.log_application_error("meeting_create", exc)
            self.send_json({"error": "No se pudo generar el informe de reunión"}, status=500)
            return
        self.send_json({"report": report}, status=201)

    def handle_meeting_pdf(self):
        self._meeting_download("pdf")

    def handle_meeting_pptx(self):
        self._meeting_download("pptx")

    def _meeting_download(self, file_type):
        try:
            payload = self._read_json_body()
            content, filename = getattr(self._meeting_service(), file_type)(
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
            self.log_application_error(f"meeting_{file_type}", exc)
            self.send_json(
                {"error": f"No se pudo construir el {file_type.upper()}"},
                status=500,
            )
            return
        self.send_response(200)
        self._send_security_headers()
        content_type = (
            "application/pdf"
            if file_type == "pdf"
            else "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)
