"""Σημειωματάριο εργασιών: αποθήκευση ανά ημερομηνία/ώρα και υπολογισμός υπενθυμίσεων.

Όλα τοπικά, σε ένα απλό αρχείο JSON (default: data/agenda.json).
"""

from __future__ import annotations

import calendar
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "data" / "agenda.json"

DATE_FMT = "%Y-%m-%d"
TIME_FMT = "%H:%M"

# Εργασία χωρίς ώρα: θεωρείται «όλη μέρα» και χτυπάει σε αυτή την ώρα.
ALL_DAY_HOUR = 9

REPEATS = {
    "none": "καμία",
    "daily": "κάθε μέρα",
    "weekly": "κάθε εβδομάδα",
    "monthly": "κάθε μήνα",
    "yearly": "κάθε χρόνο",
}

GREEK_DAYS = ("Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή")
GREEK_MONTHS = (
    "Ιανουαρίου",
    "Φεβρουαρίου",
    "Μαρτίου",
    "Απριλίου",
    "Μαΐου",
    "Ιουνίου",
    "Ιουλίου",
    "Αυγούστου",
    "Σεπτεμβρίου",
    "Οκτωβρίου",
    "Νοεμβρίου",
    "Δεκεμβρίου",
)


class AgendaError(ValueError):
    """Λάθος στα δεδομένα της εργασίας (ημερομηνία/ώρα/τίτλος)."""


# --------------------------------------------------------------------------- βοηθητικά


def now() -> datetime:
    """Η τρέχουσα ώρα (ξεχωριστή συνάρτηση για να μπορούν να τη «κλειδώσουν» οι δοκιμές)."""
    return datetime.now()


def parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in (DATE_FMT, "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise AgendaError(f"Δεν καταλαβαίνω την ημερομηνία: {value!r} (θέλω ΕΤΟΣ-ΜΗΝΑΣ-ΜΕΡΑ)")


def parse_time(value: str) -> str:
    """Επιστρέφει ώρα σε μορφή HH:MM ή κενό (= όλη μέρα)."""
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in (TIME_FMT, "%H:%M:%S", "%H.%M"):
        try:
            return datetime.strptime(text, fmt).strftime(TIME_FMT)
        except ValueError:
            continue
    raise AgendaError(f"Δεν καταλαβαίνω την ώρα: {value!r} (θέλω ΩΡΑ:ΛΕΠΤΟ, π.χ. 18:30)")


def nice_date(day: date) -> str:
    """π.χ. «Δευτέρα 3 Φεβρουαρίου 2026»."""
    return f"{GREEK_DAYS[day.weekday()]} {day.day} {GREEK_MONTHS[day.month - 1]} {day.year}"


def short_date(day: date) -> str:
    """π.χ. «3 Φεβ»."""
    return f"{day.day} {GREEK_MONTHS[day.month - 1][:4]}"


def format_when(when: datetime, with_date: bool = True) -> str:
    stamp = when.strftime(TIME_FMT)
    if not with_date:
        return stamp
    return f"{nice_date(when.date())}, {stamp}"


def _add_month(day: date, months: int = 1) -> date:
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


# --------------------------------------------------------------------------- εργασία


@dataclass
class Task:
    title: str
    date: str
    time: str = ""
    note: str = ""
    repeat: str = "none"
    done: bool = False
    id: str = ""
    created: str = ""
    notified_at: str = ""
    done_at: str = ""
    auto_run: bool = False
    auto_mode: str = "assistant"

    # --- βασικά ---------------------------------------------------------

    @property
    def day(self) -> date:
        return parse_date(self.date)

    @property
    def all_day(self) -> bool:
        return not self.time

    def when(self) -> datetime:
        """Πότε χτυπάει το ξυπνητήρι (εργασίες χωρίς ώρα → 09:00)."""
        day = self.day
        if self.all_day:
            return datetime(day.year, day.month, day.day, ALL_DAY_HOUR, 0)
        return datetime.combine(day, datetime.strptime(self.time, TIME_FMT).time())

    def sort_key(self) -> tuple[str, str, str]:
        return (self.date, self.time or "00:00", self.title.lower())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        known = {f for f in cls.__dataclass_fields__}  # noqa: SLF001 — απλό φιλτράρισμα
        clean = {k: v for k, v in (data or {}).items() if k in known}
        task = cls(**clean)
        task.title = str(task.title).strip()
        task.date = parse_date(task.date).strftime(DATE_FMT)
        task.time = parse_time(task.time)
        task.repeat = task.repeat if task.repeat in REPEATS else "none"
        task.done = bool(task.done)
        task.auto_run = bool(task.auto_run)
        task.auto_mode = task.auto_mode if task.auto_mode in ("ask", "assistant", "code", "debug") else "assistant"
        return task

    def status(self, moment: datetime | None = None) -> str:
        """'done' | 'late' | 'now' | 'future'."""
        moment = moment or now()
        if self.done:
            return "done"
        when = self.when()
        if when > moment:
            return "future"
        if when.date() < moment.date() or when < moment:
            return "late"
        return "now"

    def describe(self) -> str:
        """Μία γραμμή για εμφάνιση."""
        clock = "όλη μέρα" if self.all_day else self.time
        line = f"{self.title} · {short_date(self.day)} {clock}"
        if self.repeat != "none":
            line += f" · {REPEATS[self.repeat]}"
        return line


# --------------------------------------------------------------------------- αποθήκη


class Agenda:
    """Φορτώνει/αποθηκεύει εργασίες και βρίσκει ποιες πρέπει να χτυπήσουν τώρα."""

    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)
        self.tasks: list[Task] = []
        self.warning = ""
        self.load()

    # --- αρχείο ---------------------------------------------------------

    def load(self) -> None:
        self.tasks = []
        self.warning = ""
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8") or "{}"
            data = json.loads(raw)
            rows = data.get("tasks", []) if isinstance(data, dict) else data
            for row in rows:
                try:
                    self.tasks.append(Task.from_dict(row))
                except Exception:  # noqa: BLE001 — μία χαλασμένη εργασία δεν τα χαλάει όλα
                    self.warning = "Κάποιες εργασίες δεν διαβάστηκαν σωστά και παραλείφθηκαν."
            self.sort()
        except Exception as exc:  # noqa: BLE001
            broken = self.path.with_suffix(".broken.json")
            try:
                self.path.replace(broken)
                self.warning = f"Το αρχείο ήταν χαλασμένο — το κράτησα ως {broken.name}."
            except OSError:
                self.warning = f"Δεν διάβασα το αρχείο εργασιών: {exc}"

    def save(self) -> None:
        self.sort()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "tasks": [t.to_dict() for t in self.tasks]}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(text + "\n", encoding="utf-8")
        os.replace(temp, self.path)

    # --- αλλαγές --------------------------------------------------------

    def sort(self) -> None:
        self.tasks.sort(key=lambda t: (t.done, t.sort_key()))

    def add(
        self,
        title: str,
        when_date: str | date,
        when_time: str = "",
        note: str = "",
        repeat: str = "none",
        auto_run: bool = False,
        auto_mode: str = "assistant",
    ) -> Task:
        title = str(title or "").strip()
        if not title:
            raise AgendaError("Γράψε πρώτα έναν τίτλο για την εργασία.")
        mode = auto_mode if auto_mode in ("ask", "assistant", "code", "debug") else "assistant"
        task = Task(
            title=title,
            date=parse_date(when_date).strftime(DATE_FMT),
            time=parse_time(when_time),
            note=str(note or "").strip(),
            repeat=repeat if repeat in REPEATS else "none",
            id=self._new_id(),
            created=now().strftime("%Y-%m-%dT%H:%M:%S"),
            auto_run=bool(auto_run),
            auto_mode=mode,
        )
        self.tasks.append(task)
        self.save()
        return task

    def get(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def update(self, task_id: str, **fields) -> Task:
        task = self.get(task_id)
        if task is None:
            raise AgendaError("Δεν βρήκα αυτή την εργασία.")
        if "title" in fields:
            title = str(fields["title"] or "").strip()
            if not title:
                raise AgendaError("Ο τίτλος δεν μπορεί να είναι άδειος.")
            task.title = title
        if "date" in fields:
            task.date = parse_date(fields["date"]).strftime(DATE_FMT)
        if "time" in fields:
            task.time = parse_time(fields["time"])
        if "note" in fields:
            task.note = str(fields["note"] or "").strip()
        if "repeat" in fields:
            task.repeat = fields["repeat"] if fields["repeat"] in REPEATS else "none"
        if "done" in fields:
            self._set_done(task, bool(fields["done"]))
        # Κάθε χειροκίνητη αλλαγή ξαναενεργοποιεί την υπενθύμιση.
        if any(k in fields for k in ("date", "time")):
            task.notified_at = ""
        self.save()
        return task

    def remove(self, task_id: str) -> bool:
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.id != task_id]
        changed = len(self.tasks) != before
        if changed:
            self.save()
        return changed

    def toggle(self, task_id: str, done: bool | None = None) -> Task:
        task = self.get(task_id)
        if task is None:
            raise AgendaError("Δεν βρήκα αυτή την εργασία.")
        self._set_done(task, (not task.done) if done is None else bool(done))
        self.save()
        return task

    def clear_done(self) -> int:
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if not t.done]
        removed = before - len(self.tasks)
        if removed:
            self.save()
        return removed

    @staticmethod
    def _set_done(task: Task, done: bool) -> None:
        task.done = done
        task.done_at = now().strftime("%Y-%m-%dT%H:%M:%S") if done else ""

    def _new_id(self) -> str:
        stamp = now().strftime("%Y%m%d%H%M%S")
        used = {t.id for t in self.tasks}
        counter = 1
        while True:
            candidate = f"{stamp}-{counter}"
            if candidate not in used:
                return candidate
            counter += 1

    # --- ερωτήσεις ------------------------------------------------------

    def for_date(self, day: str | date) -> list[Task]:
        key = parse_date(day).strftime(DATE_FMT)
        return sorted((t for t in self.tasks if t.date == key), key=lambda t: t.sort_key())

    def counts(self, year: int, month: int) -> dict[str, int]:
        """Πόσες ανοιχτές εργασίες έχει κάθε μέρα του μήνα (κλειδί: ΕΤΟΣ-ΜΗΝΑΣ-ΜΕΡΑ)."""
        result: dict[str, int] = {}
        for task in self.tasks:
            if task.done:
                continue
            day = task.day
            if day.year == year and day.month == month:
                result[task.date] = result.get(task.date, 0) + 1
        return result

    def open_tasks(self) -> list[Task]:
        return sorted((t for t in self.tasks if not t.done), key=lambda t: t.sort_key())

    def upcoming(self, days: int = 7, moment: datetime | None = None) -> list[Task]:
        """Ανοιχτές εργασίες από σήμερα και για τις επόμενες `days` μέρες."""
        moment = moment or now()
        first = moment.date()
        last = first + timedelta(days=max(days, 0))
        return [t for t in self.open_tasks() if first <= t.day <= last]

    def late(self, moment: datetime | None = None) -> list[Task]:
        moment = moment or now()
        return [t for t in self.open_tasks() if t.when() < moment]

    def due(self, moment: datetime | None = None) -> list[Task]:
        """Ανοιχτές εργασίες που ήρθε η ώρα τους και δεν έχουν ειδοποιήσει ακόμα."""
        moment = moment or now()
        result = []
        for task in self.open_tasks():
            if task.when() > moment:
                continue
            if task.notified_at and task.notified_at >= task.when().strftime("%Y-%m-%dT%H:%M"):
                continue
            result.append(task)
        return result

    def take_due(self, moment: datetime | None = None) -> list[tuple[Task, datetime]]:
        """Όπως το due(), αλλά σημαδεύει τις εργασίες ως «ειδοποιήθηκε» και αποθηκεύει."""
        moment = moment or now()
        fired: list[tuple[Task, datetime]] = []
        for task in self.due(moment):
            fired.append((task, task.when()))
            self._mark_notified(task, moment)
        if fired:
            self.save()
        return fired

    def _mark_notified(self, task: Task, moment: datetime) -> None:
        task.notified_at = moment.strftime("%Y-%m-%dT%H:%M")
        if task.repeat in ("daily", "weekly", "monthly", "yearly"):
            task.date = self._next_occurrence(task, moment).strftime(DATE_FMT)
            task.notified_at = ""
            task.done = False
            task.done_at = ""

    @staticmethod
    def _next_occurrence(task: Task, moment: datetime) -> date:
        """Επόμενη ημερομηνία για επαναλαμβανόμενη εργασία (πάντα στο μέλλον)."""
        day = task.day
        for _ in range(500):  # ασφάλεια από ατέρμονο βρόχο
            if task.repeat == "daily":
                day = day + timedelta(days=1)
            elif task.repeat == "weekly":
                day = day + timedelta(days=7)
            elif task.repeat == "monthly":
                day = _add_month(day, 1)
            elif task.repeat == "yearly":
                day = _add_month(day, 12)
            else:
                break
            probe = Task(title=task.title, date=day.strftime(DATE_FMT), time=task.time)
            if probe.when() > moment:
                return day
        return day
