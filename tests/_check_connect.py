"""Προσωρινός έλεγχος της διόρθωσης σύνδεσης — διαγράφεται μετά."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from akatikito.app_api import AppState  # noqa: E402
from akatikito.settings import Settings  # noqa: E402

# 1. Το αποθηκευμένο κλειδί διαβάζεται σωστά;
saved = Settings.load()
provider = saved.provider
key = saved.key_for(provider)
print(f"πάροχος: {provider!r}")
print(f"αποθηκευμένο κλειδί: {'ΥΠΑΡΧΕΙ (' + str(len(key)) + ' χαρακτήρες)' if key else 'ΚΕΝΟ!'}")
assert key, "το αποθηκευμένο κλειδί δεν διαβάστηκε"

# 2. Σύνδεση με ΚΕΝΟ api_key και ΚΕΝΟ base_url (όπως τα στέλνει το UI) — ξαναχρησιμοποιεί το αποθηκευμένο;
state = AppState()
root = tempfile.mkdtemp(prefix="conn-test-")
result = state.connect(
    provider=provider,
    base_url="",          # το UI στέλνει κενό για έτοιμους παρόχους
    model="",             # κενό μοντέλο → default/αποθηκευμένο
    api_key="",           # ΚΕΝΟ κλειδί → πρέπει να πάρει το αποθηκευμένο
    project=root,
    max_steps=25,
    verify="",
)
print("αποτέλεσμα connect:", {k: v for k, v in result.items() if k in ("connected", "error", "model", "project")})
assert result.get("connected"), f"δεν συνδέθηκε: {result.get('error')}"
assert state.agent.config.api_key == key, "το κλειδί δεν ήρθε από τις ρυθμίσεις"
assert state.agent.config.base_url, "η διεύθυνση δεν βρέθηκε"
print(f"base_url που λύθηκε: {state.agent.config.base_url}")
print(f"μοντέλο: {state.agent.config.model}")

# 3. Το κλειδί ΔΕΝ σβήνεται όταν το πεδίο είναι κενό
saved2 = Settings.load()
assert saved2.key_for(provider), "το αποθηκευμένο κλειδί σβήστηκε!"
print("το αποθηκευμένο κλειδί έμεινε στη θέση του ✔")

# 4. Λάθος πάροχος χωρίς κλειδί → καθαρό μήνυμα λάθους, όχι κρασάρισμα
state2 = AppState()
root2 = tempfile.mkdtemp(prefix="conn-test2-")
result2 = state2.connect(
    provider="OpenAI", base_url="", model="gpt-4o-mini", api_key="",
    project=root2, max_steps=25, verify="",
)
print("χωρίς κλειδί:", {k: v for k, v in result2.items() if k in ("connected", "error")})
assert not result2.get("connected") and result2.get("error"), "έπρεπε να δώσει μήνυμα λάθους"

print("\nΟΛΑ ΟΚ — η σύνδεση δουλεύει με αποθηκευμένο κλειδί.")