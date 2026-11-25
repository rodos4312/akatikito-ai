"""Memory screen endpoints (MEMORY-PLAN M6): ``/api/memory`` and ``/api/memory/action``."""
from __future__ import annotations


class MemoryStateMixin:
    def memory_view(self, data: dict) -> dict:
        from . import memory_central, memory_screen
        try:
            root = self._files_root()
            with memory_central.opened() as s:
                memory_central.refresh(s, root)
                if data.get("history") is not None:
                    return {"history": memory_screen.history(s, int(data["history"]))}
                view = memory_screen.view(s, root, query=str(data.get("query") or "")[:300],
                                         scope=str(data.get("scope") or "all"), kind=str(data.get("kind") or ""),
                                         offset=int(data.get("offset") or 0))
                return {"project": str(root), **view}
        except memory_central.FAILURES as exc:
            return {"error": str(exc)}

    def memory_action(self, data: dict) -> dict:
        from . import memory_central, memory_screen
        if self.busy:
            return {"error": "Περίμενε να ολοκληρωθεί η εργασία."}
        root = self._files_root()
        if data.get("project") != str(root):
            return {"error": "Το project άλλαξε· ανανέωσε την οθόνη μνήμης."}
        action, memory_id = data.get("action"), data.get("id")
        try:
            with memory_central.opened() as s:
                if action == "edit":
                    result = memory_screen.edit(s, root, int(memory_id), str(data.get("text") or ""))
                elif action in ("forget", "forget_hard"):
                    result = memory_screen.forget(s, root, int(memory_id), hard=action == "forget_hard")
                elif action in ("helpful", "unhelpful"):
                    result = memory_screen.feedback(s, int(memory_id), action == "helpful")
                elif action in ("proposal_approve", "proposal_reject"):
                    result = memory_screen.answer_proposal(s, int(memory_id), action == "proposal_approve",
                                                           data.get("text"))
                elif action == "suggest":
                    result = self._memory_suggest(s, root)
                elif action == "tidy":
                    from .memory_tidy import suggestions
                    result = {"ok": True, "suggestions": suggestions(s, root, settings=self.settings)}
                elif action == "export":
                    from .memory_import import export_markdown
                    result = {"ok": True, "markdown": export_markdown(s)}
                else:
                    return {"error": "Άγνωστη ενέργεια μνήμης."}
            self.push("learning_changed")
            return result
        except (memory_central.FAILURES + (TypeError,)) as exc:
            return {"error": str(exc)}

    def _memory_suggest(self, s, root) -> dict:
        from .memory_learn import suggest
        if not self.agent:
            return {"error": "Δεν υπάρχει συζήτηση για να μάθω από αυτήν."}
        return suggest(self.agent, s, root)
