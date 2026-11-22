"""Shared organization tools: read-only history and explicitly owner-reviewed mutations."""
from __future__ import annotations

import json
from .features import spec
from .file_organization import Organizer

S = {"type": "string"}
OPERATIONS = {
    "type": "array",
    "minItems": 1,
    "maxItems": 32,
    "items": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["move", "copy", "mkdir", "trash"]},
            "source": S,
            "destination": S,
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

SPECS = [
    spec(
        "plan_file_operations",
        "Preview exact workspace move/copy/mkdir/trash operations with counts and integrity checks. No public files change. Destinations are exact new paths; never overwrite or merge. Create destination folders first in the same batch. Returns id/review for explicit owner approval.",
        {"operations": OPERATIONS},
        ["operations"],
    ),
    spec(
        "apply_file_operations",
        "Apply the exact previewed plan after owner approval. Revalidates sources/destinations, backs up first and reports incomplete rollback honestly.",
        {"id": S, "review": S},
        ["id", "review"],
    ),
    spec(
        "plan_file_undo",
        "Preview a verified undo/recovery of an organization transaction. Newer content is never overwritten.",
        {"id": S},
        ["id"],
    ),
    spec(
        "undo_file_operations",
        "Undo only the exact reviewed organization state, with owner approval.",
        {"id": S, "review": S},
        ["id", "review"],
    ),
    spec(
        "file_operation_history",
        "Read local organization status/recovery IDs; id selects one exact transaction for verified readback. No file changes.",
        {"id": S},
    ),
]


def service(ws):
    return Organizer(ws, scope=getattr(ws, "organization_scope", "documents"))


def plan_file_operations(ws, operations):
    return json.dumps(service(ws).preview(operations), ensure_ascii=False)


def plan_file_undo(ws, id):
    return json.dumps(service(ws).undo_preview(id), ensure_ascii=False)


def file_operation_history(ws, id=""):
    from .tools import _clip_with_archive

    organizer = service(ws)
    if id:
        journal = organizer._load(id)
        rows = (
            [
                {
                    "id": id,
                    "state": journal.get("state"),
                    "error": journal.get("error", ""),
                    "backup": str(organizer._folder(id)),
                }
            ]
            if journal
            else []
        )
    else:
        rows = organizer.history(limit=20)
    return _clip_with_archive(ws, json.dumps(rows, ensure_ascii=False))


def approval_details(preview, undo=False):
    names = {
        "move": "Μετακίνηση / μετονομασία",
        "copy": "Αντιγραφή",
        "mkdir": "Νέος φάκελος",
        "trash": "Κάδος με επαναφορά",
    }
    lines = [("Επαναφορά οργάνωσης" if undo else "Οργάνωση αρχείων"), "Project: " + preview["project"]]
    for row in preview["operations"]:
        lines.append(
            names[row["action"]]
            + ": "
            + row.get("source", "")
            + " → "
            + (row.get("destination") or "Ασφαλής κάδος")
            + " · "
            + str(row.get("files", 0))
            + " αρχεία"
        )
    return {**preview, "diff": "\n".join(lines)}


def apply_file_operations(ws, id, review):
    gate = getattr(ws, "external_guard", None)
    return service(ws).apply(
        id,
        review,
        (lambda details: gate("file_organization", approval_details(details))) if gate else None,
    )


def undo_file_operations(ws, id, review):
    gate = getattr(ws, "external_guard", None)
    return service(ws).undo(
        id,
        review,
        (lambda details: gate("file_organization_undo", approval_details(details, True))) if gate else None,
    )


HANDLERS = {
    name: globals()[name]
    for name in (
        "plan_file_operations",
        "apply_file_operations",
        "plan_file_undo",
        "undo_file_operations",
        "file_operation_history",
    )
}


def record_owner_changes(state, result, notify_agent=True):
    """Refresh recent paths without making a new model request or resuming an old goal."""
    paths = getattr(result, "changed_paths", ())
    if not paths:
        return
    root = state._files_root()
    kept = []
    for row in state.files:
        try:
            path = root / row["path"]
            if path.is_file():
                kept.append(row)
        except (OSError, ValueError):
            pass
    state.files = kept
    for path in paths:
        try:
            if (root / path).is_file():
                state._remember_file(path, "οργάνωση")
        except (OSError, ValueError, PermissionError):
            pass
    if state.agent and notify_agent:
        state.agent.activity.begin_turn()
        state.agent._mutation_generation += 1
        state.agent._verified_generation = -1
        note = (
            "[ΕΝΕΡΓΕΙΑ ΙΔΙΟΚΤΗΤΗ — ΟΡΓΑΝΩΣΗ ΑΡΧΕΙΩΝ] "
            + str(result)
            + "\n"
            + json.dumps(getattr(result, "organization_changes", []), ensure_ascii=False)
            + "\nΠαλιά αποτελέσματα αυτών των διαδρομών δεν αποτελούν τρέχουσα έκδοση. Δεν είναι νέο αίτημα υλοποίησης."
        )
        state.agent.messages.append(
            {"role": "user", "_origin": "internal", "content": note, "_written_paths": list(paths)}
        )
    if notify_agent and state.session_id and state.store:
        state._save_session()
