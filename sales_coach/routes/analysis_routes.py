from __future__ import annotations

from sales_coach.repositories import AnalysisRepository, PreferencesRepository
from sales_coach.services import AnalysisService


class AnalysisRoutesMixin:
    def _analysis_service(self):
        return AnalysisService(self.resolve_datasets)

    def handle_analyze(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        if not isinstance(data.get("datasets"), dict) or not data["datasets"]:
            self.send_json({"error": "No llegaron datasets para analizar"}, status=400)
            return
        try:
            result = self._analysis_service().analyze(data, self.data_scope)
        except ValueError as exc:
            self.log_application_error(
                "analyze_validation",
                exc,
                {
                    "filters": data.get("filters", {}),
                    "datasets": list((data.get("datasets") or {}).keys()),
                },
            )
            self.send_json({"error": str(exc)}, status=422)
            return
        except Exception as exc:
            self.log_application_error(
                "analyze",
                exc,
                {
                    "filters": data.get("filters", {}),
                    "datasets": list((data.get("datasets") or {}).keys()),
                },
            )
            self.send_json({"error": str(exc)}, status=500)
            return

        db = self.database()
        if db is not None:
            try:
                AnalysisRepository(db).record(data.get("filters", {}), result)
            except Exception as exc:
                self.log_application_error("analysis_record", exc)
        PreferencesRepository(db).save(
            self.auth_context.user.id,
            data.get("datasets", {}),
            planning=data.get("planning"),
        )
        self.send_json(result)

    def handle_analyze_dynamic(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        if not str(data.get("task_id") or "").strip():
            self.send_json({"error": "Falta task_id"}, status=400)
            return
        if not isinstance(data.get("datasets"), dict) or not data["datasets"]:
            self.send_json({"error": "No llegaron datasets para analizar"}, status=400)
            return
        try:
            self.send_json(self._analysis_service().dynamic(data, self.data_scope))
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=422)
        except Exception as exc:
            self.log_application_error("analyze_dynamic", exc)
            self.send_json({"error": str(exc)}, status=500)

    def handle_bi_consistency(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        datasets = data.get("datasets", {})
        if not isinstance(datasets, dict) or not datasets:
            self.send_json({"error": "No llegaron datasets para validar"}, status=400)
            return
        try:
            result = self._analysis_service().consistency(datasets, self.data_scope)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=422)
            return
        except Exception as exc:
            self.log_application_error("bi_consistency", exc)
            self.send_json({"error": str(exc)}, status=500)
            return
        self.send_json(result)

    def handle_bi_tactical_month(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        if not isinstance(data.get("datasets"), dict) or not data["datasets"]:
            self.send_json({"error": "No llegaron datasets para analizar"}, status=400)
            return
        try:
            result = self._analysis_service().tactical(data, self.data_scope)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=422)
            return
        except Exception as exc:
            self.log_application_error("bi_tactical_month", exc)
            self.send_json({"error": str(exc)}, status=500)
            return
        self.send_json(result)

    def handle_get_session(self):
        session = PreferencesRepository(self.database()).load(self.auth_context.user.id)
        self.send_json(session if session else {"datasets": None})

    def handle_save_session(self):
        try:
            data = self._read_json_body()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        saved = PreferencesRepository(self.database()).save(
            self.auth_context.user.id,
            data.get("datasets", {}),
            planning=data.get("planning"),
        )
        self.send_json({"saved": saved})

    def handle_list_analyses(self, parsed):
        db = self.database()
        if db is None:
            self.send_json({"error": "MongoDB no configurado"}, status=503)
            return
        from urllib.parse import parse_qs

        query = parse_qs(parsed.query)
        try:
            limit = int(query.get("limit", [50])[0])
        except (TypeError, ValueError):
            limit = 50
        self.send_json({"analyses": AnalysisRepository(db).list_recent(limit)})
