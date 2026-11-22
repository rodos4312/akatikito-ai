"""Έλεγχοι για τις συνεδρίες: αποθήκευση, τίτλος, φόρτωση, διαγραφή, χαλασμένο αρχείο."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from akatikito.sessions import DEFAULT_TITLE, SessionStore  # noqa: E402

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ✔ {name}")
    else:
        print(f"  ✘ {name} {detail}")
        raise SystemExit(1)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="akatikito-sessions-"))
    store = SessionStore(root)

    # 1. Δημιουργία και λίστα
    sid = store.create()
    check("δημιουργήθηκε αρχείο", store._path(sid).is_file())
    check("φαίνεται στη λίστα", any(s["id"] == sid for s in store.list()))

    # 2. Ο τίτλος βγαίνει από το πρώτο μήνυμα του χρήστη
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Φτιάξε hello.py"}]
    transcript = [{"role": "user", "text": "Φτιάξε hello.py"}, {"role": "assistant", "text": "Έτοιμο"}]
    store.save(sid, messages=messages, tokens={"prompt_tokens": 20, "completion_tokens": 10}, transcript=transcript)
    row = store.load(sid)
    check("τίτλος από το πρώτο μήνυμα", row["title"] == "Φτιάξε hello.py", row.get("title"))
    check("κράτησε messages", row["messages"] == messages)
    check("κράτησε tokens", row["tokens"]["prompt_tokens"] == 20)

    # 3. Φόρτωση μετά από νέο SessionStore (καινούργιο αντικείμενο, ίδιος φάκελος)
    again = SessionStore(root)
    loaded = again.load(sid)
    check("ξαναδιαβάζεται", loaded is not None and loaded["transcript"] == transcript)
    check("η λίστα δείχνει τον τίτλο", any(s["title"] == "Φτιάξε hello.py" for s in again.list()))

    # 4. Ο τίτλος δεν χάνεται σε επόμενο save
    again.save(sid, messages=messages, tokens={"prompt_tokens": 30, "completion_tokens": 5}, transcript=transcript)
    check("τίτλος μένει ίδιος", again.load(sid)["title"] == "Φτιάξε hello.py")

    # 5. Μακρύς τίτλος κόβεται
    long_text = "x" * 200
    sid2 = store.create()
    store.save(sid2, transcript=[{"role": "user", "text": long_text}])
    check("τίτλος κόβεται", len(store.load(sid2)["title"]) <= 60)

    # 6. Διαγραφή
    check("διαγραφή υπάρχουσας", store.delete(sid) is True and store.load(sid) is None)
    check("διαγραφή ανύπαρκτης = False", store.delete("δεν-υπάρχει") is False)
    check("δεν φαίνεται στη λίστα μετά", all(s["id"] != sid for s in store.list()))

    # 7. Χαλασμένο αρχείο δεν σκάει
    broken = store._path("broken")
    store.dir.mkdir(parents=True, exist_ok=True)
    broken.write_text("{ όχι json", encoding="utf-8")
    check("χαλασμένο = None", store.load("broken") is None)
    check("χαλασμένο δεν είναι στη λίστα", all(s["id"] != "broken" for s in store.list()))

    # 8. Κενή συνεδρία κρατά τον προεπιλεγμένο τίτλο
    sid3 = store.create()
    check("κενή = προεπιλεγμένος τίτλος", store.load(sid3)["title"] == DEFAULT_TITLE)

    print(f"\n{len(PASSED)} έλεγχοι πέρασαν.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
