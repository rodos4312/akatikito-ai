"""Έλεγχοι για το σημειωματάριο: αποθήκευση, ταξινόμηση, «ήρθε η ώρα», επαναλήψεις."""

from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from akatikito.agenda import Agenda, AgendaError, Task, parse_time  # noqa: E402

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ✔ {name}")
    else:
        print(f"  ✘ {name} {detail}")
        raise SystemExit(1)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="akatikito-agenda-"))
    path = root / "data" / "agenda.json"

    # 1. Προσθήκη και επιμονή στο αρχείο
    agenda = Agenda(path)
    agenda.add("Πληρωμή ΔΕΗ", "2026-02-14", "18:30", note="με κάρτα")
    agenda.add("Γιατρός", "2026-02-14", "")
    agenda.add("Ραντεβού", "2026-02-20", "09:00")
    check("γράφτηκε το αρχείο", path.exists())
    again = Agenda(path)
    check("ξαναδιαβάστηκε", len(again.tasks) == 3, f"{len(again.tasks)} εργασίες")

    # 2. Ταξινόμηση μέρας: όλη μέρα πρώτα, μετά κατά ώρα
    day = again.for_date("2026-02-14")
    check("σειρά μέρας", [t.title for t in day] == ["Γιατρός", "Πληρωμή ΔΕΗ"], str([t.title for t in day]))

    # 3. «Ήρθε η ώρα» — πριν, πάνω στην ώρα, μετά
    moment = datetime(2026, 2, 14, 8, 59)
    check("πριν την ώρα: τίποτα", again.due(moment) == [])
    moment = datetime(2026, 2, 14, 18, 29)
    check("09:00 χτύπησε, 18:30 όχι ακόμα", [t.title for t in again.due(moment)] == ["Γιατρός"])
    moment = datetime(2026, 2, 14, 18, 30)
    due = again.due(moment)
    check(
        "πάνω στην ώρα: δύο",
        sorted(t.title for t in due) == ["Γιατρός", "Πληρωμή ΔΕΗ"],
        str([t.title for t in due]),
    )

    # 4. Το take_due σημαδεύει ώστε να μη χτυπήσει δεύτερη φορά
    fired = again.take_due(moment)
    check("χτύπησε δύο φορές", len(fired) == 2, str(len(fired)))
    check("δεν ξαναχτυπά", again.due(moment) == [])

    # 5. Χωρίς ώρα → χτυπάει στις 09:00
    all_day = [t for t in again.tasks if t.title == "Γιατρός"][0]
    check("όλη μέρα = 09:00", all_day.all_day and all_day.when().hour == 9, str(all_day.when()))
    fresh = Agenda(root / "third.json")
    fresh.add("Γιατρός", "2026-02-14", "")
    check("όλη μέρα δεν ήρθε 08:59", fresh.due(datetime(2026, 2, 14, 8, 59)) == [])
    check("όλη μέρα ήρθε 09:00", len(fresh.due(datetime(2026, 2, 14, 9, 0))) == 1)

    # 6. Ολοκλήρωση
    again.toggle(all_day.id, True)
    check("ολοκληρώθηκε", Agenda(path).get(all_day.id).done is True)
    check("ολοκληρωμένο δεν χτυπά", all_day not in again.due(datetime(2026, 2, 20, 12, 0)))

    # 7. Επαναλαμβανόμενη: μετά το χτύπημα πάει στην επόμενη μέρα
    agenda2 = Agenda(root / "second.json")
    daily = agenda2.add("Νερό φυτών", "2026-03-01", "08:00", repeat="daily")
    agenda2.take_due(datetime(2026, 3, 1, 8, 0))
    moved = Agenda(root / "second.json").get(daily.id)
    check("καθημερινό πάει αύριο", moved.date == "2026-03-02", moved.date)
    check("καθημερινό ξαναείναι ανοιχτό", moved.done is False and moved.notified_at == "")

    weekly = agenda2.add("Σκουπίδια", "2026-03-02", "20:00", repeat="weekly")
    agenda2.take_due(datetime(2026, 3, 9, 21, 0))  # δύο εβδομάδες μαζεμένες
    check(
        "εβδομαδιαίο πάει μπροστά",
        Agenda(root / "second.json").get(weekly.id).day > date(2026, 3, 9),
        Agenda(root / "second.json").get(weekly.id).date,
    )

    # 8. Αλλαγή ημερομηνίας → ξαναενεργοποιεί την υπενθύμιση
    t = agenda.add("Τηλέφωνο", "2026-04-01", "10:00")
    agenda.take_due(datetime(2026, 4, 1, 10, 0))
    agenda.update(t.id, date="2026-04-05")
    check("νέα ημερομηνία = νέα υπενθύμιση", agenda.get(t.id).notified_at == "")
    check("νέα μέρα κρατήθηκε", agenda.get(t.id).date == "2026-04-05")

    # 9. Διαγραφή και καθάρισμα
    check("διαγραφή", agenda.remove(t.id) and agenda.get(t.id) is None)
    agenda.toggle(all_day.id, True)
    removed = agenda.clear_done()
    check("καθάρισμα ολοκληρωμένων", removed >= 1 and not any(x.done for x in agenda.tasks))

    # 10. Λάθος δεδομένα
    for bad, why in ((("", "2026-02-14", ""), "άδειος τίτλος"), (("x", "χθες", ""), "λάθος μέρα")):
        try:
            agenda.add(*bad)
            check(f"απόρριψη: {why}", False, "πέρασε χωρίς λάθος")
        except AgendaError:
            check(f"απόρριψη: {why}", True)
    try:
        parse_time("25:99")
        check("απόρριψη ώρας", False)
    except AgendaError:
        check("απόρριψη ώρας", True)
    check("κενή ώρα = όλη μέρα", parse_time("") == "")

    # 11. Χαλασμένο αρχείο δεν σκάει
    broken = root / "broken.json"
    broken.write_text("{ όχι json", encoding="utf-8")
    safe = Agenda(broken)
    check("χαλασμένο αρχείο = προειδοποίηση", safe.warning != "" and safe.tasks == [])

    # 12. Πεδία που δεν ξέρουμε αγνοούνται (συμβατότητα παλιών αρχείων)
    legacy = Task.from_dict({"title": "Παλιό", "date": "2026-05-05", "άγνωστο": 1})
    check("άγνωστα πεδία αγνοούνται", legacy.title == "Παλιό")

    print(f"\n{len(PASSED)} έλεγχοι πέρασαν.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
