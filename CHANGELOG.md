# Ιστορικό Αλλαγών (Changelog) — Akatikito AI

Όλες οι σημαντικές αλλαγές σε απλά ελληνικά.

## [v2.5.0] — 2026-11-25

### Προστέθηκε
- **Κεντρική Πολύγλωσση Μνήμη (Μ0–Μ8, SQLite + FTS5)**: `memory_store.py`, `memory_text.py` (`snowballstemmer`, ελληνικά⇄greeklish, CJK bigrams, recall@8 `0,993`), `memory_recall.py`, `memory_import.py`, `memory_central.py` (cache-safe ανάκληση μέσω `context.observe`), νέα οθόνη Μνήμης (`ui/memory.js`) με «🧠 χρησιμοποίησα N μνήμες» & 👍/👎 feedback, προτάσεις μάθησης με έγκριση (`memory_learn.py`), επιμελητής τακτοποίησης (`memory_tidy.py`) και FTS5 ευρετήριο παλιών συζητήσεων (`memory_sessions.py`).
- **Studio UI & Μενού (`ui/studio.css`, `ui/studio.js`)**: SVG vector icons στο sidebar, αναδιπλούμενη πλαϊνή μπάρα (`Ctrl+\` με αποθήκευση), ξεχωριστή καρτέλα **Κινητό** (σε 6 γλώσσες), Master–Detail διάταξη στις **Ρυθμίσεις** (Εμφάνιση & 12–20px γραμματοσειρά, Routing & Fallback, Ασφάλεια & Advanced, Φωνή, Κόστος & Κλειδιά), file-type badges και καθαρή αρχική οθόνη chat.
- **Οδηγίες εν κινήσει (`steer`, Β7)**: αποστολή διόρθωσης κατά την εκτέλεση (`/api/steer`, κουμπί «🧭 Οδηγία») χωρίς διακοπή και χωρίς σπάσιμο της KV cache.
- **Έξυπνο Retry, `Retry-After` & Ρητός Εφεδρικός Πάροχος (Β3)**: `model_retry.py` σε `chat` & `chat_stream` με αυστηρή απομόνωση API keys ανά host.
- **Ασφάλεια & Απόκρυψη Μυστικών (Β1, Δ0+Δ6)**: κεντρικό `redact.py` σε sessions, audit log, διαγνωστικά & exports· 3-door **Advanced feature gate** (`advanced.py`: `self_update`, `subtasks`, `git` κλειστά by default σε UI, API 403 και tools μοντέλου).
- **Παραγωγικότητα & Κόστος (Β5, Β6, Β8)**: καρφίτσωμα (`📌`) & αναζήτηση πλήρους κειμένου (χωρίς τόνους) σε παλιές συζητήσεις, μηνιαία επισκόπηση κόστους με sparkline/cache savings/πρόβλεψη (`/api/usage/insights`), αποθήκευση μεγάλων tool outputs στο `.agent/outputs/` (`tool_spill.py`), **Visual Diff** (`/api/files/diff`), **Offline Hybrid Doc Search** (`doc_index.py`), **Drag & Drop + Ctrl+V** screenshot paste, native **System Tray** & **Global Hotkey** (`Alt+Space`), και **Slim `ask` mode** (-66,6% payload).
- **🩺💉 Γιατρός v2.5 (`doctor.py` + `doctor_checks.py` + `Doctor.cmd`)**: 19 read-only διαγνωστικοί έλεγχοι (συμπεριλαμβανομένων `memory_db`, `doc_index`, `source_syntax` AST, `staged_attachments`, `desktop_hotkey_tray`, busy lock, `snapshot_configs`) και ασφαλής 1-click επισκευή με backup & Audit Log.
- **Αυστηρά Quality Gates (`check.cmd` 6 σταδίων + CI)**: `ruff.toml`, 100% αρχεία πηγαίου κώδικα ≤ 800 γραμμές (0 εξαιρέσεις), downward-only complexity & long-line ratchet (`scripts/health_check.py`), 9 Node UI tests και 870+ pytest tests.

### Διορθώθηκε & Βελτιώθηκε
- **Windows 64-bit σταθερότητα**: ρητά `argtypes`/`restype` με απομονωμένα `ctypes.WinDLL` σε `tray.py` & `hotkey.py`.
- **Ασφάλεια & αξιοπιστία**: αυτόματο `ws.backup()` πριν από overwrite (mobile upload & tool plugins), session-sticky Smart Routing, ρητή προειδοποίηση HTTP LAN στο Mobile Bridge, κρυπτογραφημένο `flutter_secure_storage` & αυστηρό `key.properties` στο Android build.

---

## [v2.4.0] — 2026-11-23

### Προστέθηκε
- Νέο Master-Detail UI με Tabs (Συνομιλίες, Ατζέντα, Πάροχοι, Plugins, Skills, Μνήμη, Ρυθμίσεις).
- Cross-Provider Smart Model Routing (διαφορετικός πάροχος/μοντέλο για απλές vs δύσκολες εργασίες).
- 3 Native Tool Plugins (`pdf_toolkit`, `image_tools`, `zip_manager`) + 5 ενσωματωμένα Skills & εισαγωγή `SKILL.md`.
- Email (IMAP/SMTP) & Slack integrations.
- Mobile Bridge v2 (Tailscale + LAN, μεταφορά αρχείων/φακέλων, Android APK v1.7.0 με release υπογραφή).
- Επίσημο λογότυπο & ανανεωμένη σελίδα GitHub Pages.
