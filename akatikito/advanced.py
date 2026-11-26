"""Advanced developer features: 1 switch per feature, enforced across all 3 doors (PRO-PLAN D0+D6).

Doors enforced when a feature is disabled (default for new installs):
1. UI: menu controls / buttons hidden or gated behind explicit opt-in with warning.
2. API: protected routes return HTTP 403 with clear Greek message.
3. Model: disabled feature tools are never advertised and never executable.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    description: str
    tools: tuple[str, ...] = ()
    routes: tuple[str, ...] = ()
    warning: str = ""


FEATURES: dict[str, Feature] = {
    f.key: f
    for f in (
        Feature(
            "self_update",
            "Αυτοαναβάθμιση",
            "Ο agent μπορεί να βελτιώσει τον κώδικα του Akatikito σε απομονωμένο υποψήφιο με ελέγχους.",
            routes=("/api/upgrade/start", "/api/upgrade/check", "/api/upgrade/apply"),
            warning="Ο agent θα μπορεί να αλλάξει τον κώδικα του Akatikito σε υποψήφιο. Κάθε αλλαγή περνά από τους ελέγχους.",
        ),
        Feature(
            "subtasks",
            "Παράλληλοι βοηθοί",
            "Ανάθεση ανεξάρτητων υποεργασιών σε παράλληλους workers με κοινό όριο tokens.",
            tools=("run_subtask", "run_subtasks"),
            warning="Επιτρέπει στον agent να ανοίγει υποεργασίες. Ισχύουν τα ίδια όρια κόστους και προφίλ.",
        ),
        Feature(
            "git",
            "Εργαλεία Git",
            "Έλεγχος κατάστασης, διαφορών, commit και draft PR με υποχρεωτικούς ελέγχους (μόνο σε φακέλους Ανάπτυξης).",
            tools=("git_status_diff", "git_prepare_commit", "git_commit", "prepare_draft_pr"),
            warning="Ενεργοποιεί εργαλεία Git (status, diff, commit με έλεγχο). Ποτέ force-push ή --no-verify.",
        ),
        Feature(
            "cli",
            "Terminal (akatikito CLI)",
            "Εντολές από PowerShell/cmd μέσω του ίδιου τοπικού API, με τα ίδια όρια κόστους και εγκρίσεις.",
            warning="Επιτρέπει στο akatikito CLI να στέλνει αιτήματα στο τοπικό API με τοπικό token.",
        ),
        Feature(
            "acp",
            "Σύνδεση με editor (ACP)",
            "Σύνδεση με VS Code / Zed μέσω stdio ACP πάνω στο ίδιο τοπικό API, με diff πριν από κάθε αλλαγή.",
            warning="Επιτρέπει σε εξωτερικό editor να στέλνει ανοιχτά αρχεία και επιλογές μέσω ACP.",
        ),
        Feature(
            "plugin_sdk",
            "Δικά μου plugins και skills (SDK)",
            "Φάκελος ανάπτυξης, σκελετοί plugins (`akatikito plugin new`) και τοπική υπογραφή.",
            warning="Επιτρέπει φόρτωση μη υπογεγραμμένων plugins ΜΟΝΟ από τον φάκελο ανάπτυξης (με κόκκινη ένδειξη).",
        ),
    )
}


def enabled(settings, key: str) -> bool:
    if key not in FEATURES or settings is None:
        return False
    active = getattr(settings, "advanced_features", ()) or ()
    return key in active


def blocked_tools(settings) -> set[str]:
    """Return all model tool names blocked by currently disabled Advanced features."""
    return {
        tool
        for feat in FEATURES.values()
        if not enabled(settings, feat.key)
        for tool in feat.tools
    }


def filter_tools(settings, tools: list[dict]) -> list[dict]:
    """Remove tool specs belonging to disabled Advanced features."""
    blocked = blocked_tools(settings)
    if not blocked:
        return list(tools)
    return [t for t in tools if t.get("function", {}).get("name") not in blocked]


def route_denial(settings, path: str) -> str | None:
    """Return a clear Greek 403 message if `path` belongs to a disabled Advanced feature."""
    for feat in FEATURES.values():
        if path in feat.routes and not enabled(settings, feat.key):
            return f"Η λειτουργία «{feat.label}» είναι κλειστή. Ενεργοποίησέ την από Ρυθμίσεις → Advanced."
    return None


def catalog(settings) -> list[dict]:
    """UI-ready catalog of Advanced features with current enabled state and warnings."""
    return [
        {
            "key": f.key,
            "label": f.label,
            "description": f.description,
            "warning": f.warning,
            "enabled": enabled(settings, f.key),
        }
        for f in FEATURES.values()
    ]


def sanitize_feature_list(raw) -> list[str]:
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [k for k in dict.fromkeys(str(x) for x in raw) if k in FEATURES]


def apply_preferences(settings, agent, data: dict) -> str | None:
    """Validate and apply `advanced_features` preference update (owner UI only)."""
    if "advanced_features" not in data:
        return None
    raw = data.get("advanced_features")
    if not isinstance(raw, list) or any(not isinstance(x, str) or x not in FEATURES for x in raw):
        return "Μη έγκυρη λίστα ρυθμίσεων Advanced."
    settings.advanced_features = sanitize_feature_list(raw)
    if agent is not None and data.get("apply_now", True):
        agent._blocked_advanced_tools = blocked_tools(settings)
        if getattr(agent, "ws", None) is not None:
            agent.ws.advanced_settings = settings
    return None
