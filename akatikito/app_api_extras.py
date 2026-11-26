"""Email assistant drafts & 24/7 background service AppState mixin (G1, G2)."""
from __future__ import annotations

from pathlib import Path


class ExtrasStateMixin:
    def _email_dir(self) -> Path:
        paths = self.active_profile_paths()
        return paths.root if paths is not None else self._base_data_dir()

    def _email_account_path(self) -> Path:
        return self._email_dir() / "email_account.json"

    def _configured_email_account_str(self) -> str:
        path = self._email_account_path()
        return str(path) if path.is_file() else ""

    def _email_drafts_path(self) -> Path:
        return self._email_dir() / "email_drafts.json"

    def _build_email_assistant(self):
        override = getattr(self, "_email_assistant_override", None)
        if override is not None:
            return override
        path = self._email_account_path()
        if not path.is_file():
            return None
        from .email_assistant import EmailAccount, EmailAssistant

        acc = EmailAccount.load(path)
        if not acc.address or not acc.secret_masked:
            return None
        return EmailAssistant(
            acc,
            self._email_drafts_path(),
            fetcher=getattr(self, "_email_fetcher", None),
            sender=getattr(self, "_email_sender", None),
        )

    def email_drafts_view(self) -> dict:
        from .email_assistant import EmailDraftStore

        asst = self._build_email_assistant()
        return {
            "configured": asst is not None,
            "account": asst.account.to_public_dict() if asst else None,
            "drafts": EmailDraftStore(self._email_drafts_path()).list_drafts(),
        }

    def _email_configure(self, payload: dict) -> dict:
        from .email_assistant import EmailAccount

        acc = EmailAccount.create(
            str(payload.get("address") or ""),
            str(payload.get("secret") or ""),
            imap_host=str(payload.get("imap_host") or "imap.gmail.com"),
            smtp_host=str(payload.get("smtp_host") or "smtp.gmail.com"),
            read_only=payload.get("read_only", True) is not False,
            auth_mode=str(payload.get("auth_mode") or "password"),
        )
        acc_path = self._email_account_path()
        acc.save(acc_path)
        if self.agent is not None:
            self.agent.config.email_account_path = str(acc_path)
            self.agent.ws.email_account_path = str(acc_path)
        return {"ok": True, **self.email_drafts_view()}

    def _email_assistant_op(self, action: str, payload: dict) -> dict:
        asst = self._build_email_assistant()
        if asst is None:
            return {"error": "Δεν υπάρχει ρυθμισμένος λογαριασμός email."}
        if action == "create_draft":
            draft = asst.draft_reply(
                str(payload.get("to") or ""),
                str(payload.get("subject") or ""),
                str(payload.get("body") or ""),
                in_reply_to=str(payload.get("in_reply_to") or ""),
            )
            return {"ok": True, "draft": draft, **self.email_drafts_view()}
        if action == "send":
            draft = asst.send_approved_draft(
                str(payload.get("id") or ""),
                user_approved=payload.get("user_approved") is True,
            )
            return {"ok": True, "draft": draft, **self.email_drafts_view()}
        return {"error": "Άγνωστη ενέργεια email."}

    def email_draft_action(self, data: dict) -> dict:
        if self.busy:
            return {"error": "Περίμενε να ολοκληρωθεί η εργασία."}
        from .email_assistant import EmailDraftStore

        payload = data or {}
        action = str(payload.get("action") or "").strip()
        try:
            if action == "configure":
                return self._email_configure(payload)
            if action == "delete":
                res = EmailDraftStore(self._email_drafts_path()).delete(str(payload.get("id") or ""))
                return {"ok": True, **res, **self.email_drafts_view()}
            return self._email_assistant_op(action, payload)
        except (PermissionError, ValueError, OSError) as exc:
            return {"error": str(exc)}

    def _bg_controller(self):
        if getattr(self, "_bg_ctrl", None) is None:
            from .background_service import BackgroundServiceController

            self._bg_ctrl = BackgroundServiceController(
                self._email_dir(),
                spawner=getattr(self, "_bg_spawner", None),
                pid_alive=getattr(self, "_bg_pid_alive", None),
            )
        return self._bg_ctrl

    def background_service_status(self) -> dict:
        from .background_service import is_windows_autostart_enabled

        backend = getattr(self, "_bg_registry_backend", None)
        ctrl = self._bg_controller()
        return ctrl.status(
            enabled=bool(self.settings.background_service),
            autostart=is_windows_autostart_enabled(backend=backend) if backend is not None else bool(self.settings.background_autostart),
            daily_budget=float(self.settings.daily_budget or 0.0),
        )

    def _apply_background_service_pref(self, data: dict) -> str | None:
        if "background_service" not in data:
            return None
        val = data["background_service"]
        if type(val) is not bool:
            return "Χρειάζεται boolean για τη λειτουργία παρασκηνίου."
        from .background_service import set_windows_autostart

        backend = getattr(self, "_bg_registry_backend", None)
        ctrl = self._bg_controller()
        if val:
            if float(self.settings.daily_budget or 0.0) <= 0.0:
                return "Για λειτουργία 24/7 στο παρασκήνιο πρέπει πρώτα να ορίσεις ημερήσιο όριο κόστους (> 0 $)."
            try:
                set_windows_autostart(True, backend=backend)
                ctrl.start()
            except (OSError, RuntimeError, ValueError) as exc:
                return str(exc)
        else:
            try:
                set_windows_autostart(False, backend=backend)
                ctrl.stop()
            except (OSError, RuntimeError, ValueError) as exc:
                return str(exc)
        self.settings.background_service = val
        self.settings.background_mode = val
        self.settings.background_autostart = val
        return None

    def background_service_stop(self) -> dict:
        if self.busy:
            return {"error": "Περίμενε να ολοκληρωθεί η εργασία."}
        err = self._apply_background_service_pref({"background_service": False})
        if err:
            return {"error": err}
        self._save_settings()
        return {"ok": True, "background_service_status": self.background_service_status()}

    def detect_local_ai(self, endpoints=None) -> dict:
        from .onboarding_helpers import detect_local_ai

        return detect_local_ai(endpoints=endpoints)
