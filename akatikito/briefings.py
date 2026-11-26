"""Ready-made 1-click scheduled briefings with visible cost footer (FEATURES-PLAN L3)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .agenda import Agenda, parse_time
from .scheduler import ScheduledQueue


@dataclass(frozen=True)
class BriefingPreset:
    id: str
    title: str
    skill: str
    repeat: str
    default_time: str
    description: str
    prompt: str


BRIEFING_PRESETS: dict[str, BriefingPreset] = {
    p.id: p
    for p in (
        BriefingPreset(
            id="morning-briefing",
            title="Πρωινή ενημέρωση",
            skill="morning-briefing",
            repeat="daily",
            default_time="09:00",
            description="Καθημερινή σύνοψη ατζέντας, προτεραιοτήτων και πρόσφατων εγγράφων.",
            prompt="Χρησιμοποίησε το skill /morning-briefing για να ετοιμάσεις τη σημερινή πρωινή ενημέρωση.",
        ),
        BriefingPreset(
            id="weekly-project-summary",
            title="Εβδομαδιαία σύνοψη έργου",
            skill="weekly-project-summary",
            repeat="weekly",
            default_time="09:00",
            description="Εβδομαδιαία ανασκόπηση προόδου, ανοιχτών θεμάτων και επόμενων βημάτων.",
            prompt="Χρησιμοποίησε το skill /weekly-project-summary για να φτιάξεις την εβδομαδιαία σύνοψη έργου.",
        ),
        BriefingPreset(
            id="bill-reminders",
            title="Υπενθύμιση λογαριασμών",
            skill="bill-reminders",
            repeat="monthly",
            default_time="09:00",
            description="Μηνιαίος έλεγχος επερχόμενων λογαριασμών και προθεσμιών από τοπικά έγγραφα.",
            prompt="Χρησιμοποίησε το skill /bill-reminders για να ελέγξεις επερχόμενους λογαριασμούς και προθεσμίες.",
        ),
    )
}


def list_presets() -> list[dict]:
    return [
        {
            "id": p.id,
            "title": p.title,
            "skill": p.skill,
            "repeat": p.repeat,
            "default_time": p.default_time,
            "description": p.description,
        }
        for p in BRIEFING_PRESETS.values()
    ]


def estimate_usage_cost(usage: dict | None, prices: dict | None = None) -> tuple[int, float | None]:
    """Compute (total_tokens, usd_cost_or_None) for a briefing run."""
    info = usage or {}
    prompt = max(0, int(info.get("prompt_tokens", 0) or 0))
    completion = max(0, int(info.get("completion_tokens", 0) or 0))
    hit = min(prompt, max(0, int(info.get("cache_hit_tokens", 0) or 0)))
    total = prompt + completion
    if not isinstance(prices, dict) or not all(k in prices for k in ("input", "cached", "output")):
        return total, None
    cost = ((prompt - hit) * float(prices["input"]) + hit * float(prices["cached"]) + completion * float(prices["output"])) / 1_000_000.0
    return total, round(cost, 6)


def format_briefing_with_cost(title: str, body: str, usage: dict | None = None, prices: dict | None = None) -> str:
    """Attach a clear, visible cost/token footer to every briefing sent to App or Telegram."""
    tokens, cost = estimate_usage_cost(usage, prices)
    if cost is not None:
        footer = f"Κόστος ενημέρωσης: ${cost:.4f} ({tokens:,} tokens)"
    else:
        footer = f"Κατανάλωση ενημέρωσης: {tokens:,} tokens (χωρίς ορισμένη τιμή μοντέλου)"
    clean_body = str(body or "").strip() or "(Χωρίς νέα στοιχεία σήμερα.)"
    return f"{title}\n\n{clean_body}\n\n— {footer}"


def activate_briefing(
    project: str | Path,
    queue: ScheduledQueue,
    preset_id: str,
    *,
    start_date: date | str | None = None,
    time_hhmm: str | None = None,
    delivery: str = "app",
) -> dict:
    """One-click activation: adds a recurring Agenda task and authorizes it in ScheduledQueue."""
    preset = BRIEFING_PRESETS.get(preset_id)
    if preset is None:
        raise ValueError(f"Άγνωστο πρότυπο ενημέρωσης: {preset_id}")
    if delivery not in ("app", "telegram"):
        raise ValueError("Το κανάλι παράδοσης πρέπει να είναι 'app' ή 'telegram'.")
    when_date = start_date if start_date is not None else (date.today() + timedelta(days=1))
    when_time = parse_time(time_hhmm or preset.default_time)
    root = Path(project).resolve()
    agenda = Agenda(root / ".agent" / "agenda.json")
    note = f"{preset.prompt}\n[Παράδοση: {delivery}]"
    task = agenda.add(
        preset.title,
        when_date,
        when_time,
        note=note,
        repeat=preset.repeat,
        auto_run=True,
        auto_mode="assistant",
    )
    queue.authorize_task(root, task)
    return {
        "ok": True,
        "preset": preset.id,
        "task_id": task.id,
        "title": task.title,
        "date": task.date,
        "time": task.time,
        "repeat": task.repeat,
        "delivery": delivery,
    }


def deactivate_briefing(project: str | Path, queue: ScheduledQueue, task_id: str) -> bool:
    """One-click deactivation: cancels authorization in queue and removes task from Agenda."""
    root = Path(project).resolve()
    queue.cancel_task(root, task_id)
    agenda = Agenda(root / ".agent" / "agenda.json")
    return agenda.remove(task_id)
