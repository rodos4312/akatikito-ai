"""Δοκιμές για το edit_file: ατομικότητα, αμφισημία, ανοχή κενών, διατήρηση αρχείου."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from akatikito.tools import dispatch, edit_file  # noqa: E402
from akatikito.workspace import Workspace  # noqa: E402

PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  OK {name}")
    else:
        print(f"  ✘ {name} {detail}")
        raise SystemExit(1)


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="akatikito-edit-"))
    ws = Workspace(root)

    # 1. Απλή αλλαγή
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    msg = edit_file(ws, "a.py", "<<<<<<< SEARCH\n    return 1\n=======\n    return 10\n>>>>>>> REPLACE")
    check("απλή αλλαγή", msg.startswith("ΕΓΙΝΕ"), msg)
    check("γράφτηκε σωστά", "return 10" in (root / "a.py").read_text(encoding="utf-8"))

    # 2. Πολλαπλά blocks σε μία κλήση
    (root / "b.py").write_text("one = 1\ntwo = 2\nthree = 3\n", encoding="utf-8")
    msg = edit_file(
        ws,
        "b.py",
        "<<<<<<< SEARCH\none = 1\n=======\none = 100\n>>>>>>> REPLACE\n"
        "<<<<<<< SEARCH\nthree = 3\n=======\nthree = 300\n>>>>>>> REPLACE",
    )
    text = (root / "b.py").read_text(encoding="utf-8")
    check("δύο blocks", msg.startswith("ΕΓΙΝΕ") and "one = 100" in text and "three = 300" in text, msg)

    # 3. Αμφισημία -> άρνηση, αρχείο ανέπαφο
    (root / "c.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    before = (root / "c.py").read_text(encoding="utf-8")
    msg = edit_file(ws, "c.py", "<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE")
    check("αμφισημία απορρίπτεται", msg.startswith("ΣΦΑΛΜΑ") and "2 φορές" in msg, msg)
    check("αρχείο ανέπαφο", (root / "c.py").read_text(encoding="utf-8") == before)

    # 4. Δεν βρέθηκε -> άρνηση, αρχείο ανέπαφο
    msg = edit_file(ws, "c.py", "<<<<<<< SEARCH\nδεν υπάρχει\n=======\nκάτι\n>>>>>>> REPLACE")
    check("μη εύρεση απορρίπτεται", msg.startswith("ΣΦΑΛΜΑ"), msg)
    check("αρχείο ανέπαφο 2", (root / "c.py").read_text(encoding="utf-8") == before)

    # 5. Ανοχή σε κενά στο τέλος: αλλάζει ΜΟΝΟ το σημείο, όχι όλο το αρχείο
    original = "keep_me   \nvalue = 1   \nlast\n"
    (root / "d.py").write_text(original, encoding="utf-8")
    msg = edit_file(
        ws,
        "d.py",
        "<<<<<<< SEARCH\nvalue = 1\nlast\n=======\nvalue = 2\nlast\n>>>>>>> REPLACE",
    )
    after = (root / "d.py").read_text(encoding="utf-8")
    check("fallback κενών δουλεύει", msg.startswith("ΕΓΙΝΕ"), msg)
    check("άλλαξε μόνο το σημείο", after == "keep_me   \nvalue = 2\nlast\n", repr(after))
    check(
        "τα άλλα κενά έμειναν",
        after.splitlines()[0] == "keep_me   ",
        f"πρώτη γραμμή: {after.splitlines()[0]!r}",
    )

    # 6. CRLF διατηρείται στις γραμμές που δεν αλλάζουν
    crlf = "alpha\r\nbeta\r\ngamma\r\n"
    with open(root / "e.py", "w", encoding="utf-8", newline="") as fh:
        fh.write(crlf)
    edit_file(ws, "e.py", "<<<<<<< SEARCH\nbeta\n=======\nBETA\n>>>>>>> REPLACE")
    raw = (root / "e.py").read_bytes()
    check("CRLF διατηρήθηκε", raw.count(b"\r\n") == 3, repr(raw))

    # 7. Περιορισμός: έξω από το project απορρίπτεται
    outside = dispatch(ws, "read_file", {"path": "../secret.txt"})
    check("έξω από project = απόρριψη", outside.startswith("ΑΠΟΡΡΙΨΗ"), outside)

    # 8. write_file αρνείται υπάρχον
    msg = dispatch(ws, "write_file", {"path": "a.py", "content": "νέο"})
    check("write_file αρνείται υπάρχον", msg.startswith("ΣΦΑΛΜΑ"), msg)

    print(f"\n{len(PASSED)} έλεγχοι πέρασαν.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
