# Ιστορικό Αλλαγών (Changelog) — Akatikito AI

Όλες οι σημαντικές αλλαγές σε απλά ελληνικά.

## [v2.6.0] — 2026-11-26

### Προστέθηκε
- **Εγκατάσταση χωρίς Python (`Akatikito-v2.6.0-portable.zip`, ~20,6 MB)**: ενσωματωμένη Python 3.11.9 με καρφωμένο SHA-256 και όλα τα πακέτα του `requirements.txt` — αποσυμπίεση → διπλό κλικ στο `AkatikitoApp.cmd` χωρίς PATH ή δικαιώματα διαχειριστή, και ασφαλές `Uninstall.cmd` που διατηρεί τα δεδομένα χρήστη (`data/`).
- **Απλός Οδηγός Πρώτης Εκκίνησης (6 γλώσσες)**: επιλογή «Με λογαριασμό AI» (με συνδέσμους κλειδιών) ή «Τοπικά στον υπολογιστή μου» (αυτόματος εντοπισμός Ollama / LM Studio χωρίς αυτόματο κατέβασμα) ή «Για προχωρημένους», 3 έτοιμα παραδείγματα με 1 κλικ και φιλικά μηνύματα λάθους στη γλώσσα του χρήστη.
- **Κουμπί «🐛 Αναφορά προβλήματος» (Ρυθμίσεις + banners σφάλματος)**: καθαρισμένη προεπισκόπηση (χωρίς API keys, usernames, email ή μνήμη), αποθήκευση ZIP και έτοιμο GitHub issue URL μόνο με έκδοση & OS (0 τηλεμετρία).
- **Προφίλ Οικογένειας (B10)**: απομονωμένα δεδομένα ανά μέλος (ρυθμίσεις, κλειδιά, `memory.db`, κόστος), in-place μετάβαση κυρίου προφίλ byte-προς-byte, φόρμα νέου προφίλ σε 6 γλώσσες με PIN κυρίου, παιδικοί περιορισμοί και κλιμακωτό lockout PIN (`30s → 60s → 120s → … → 1h`).
- **Βοηθός Email & Λειτουργία 24/7**: read-only `email_summary` με `injection_guard`, τοπικά πρόχειρα `email_draft_reply` (0 εργαλείο αποστολής στο μοντέλο· αποστολή μόνο με κλικ χρήστη στο UI) και διακόπτης 24/7 στις Ρυθμίσεις με υποχρεωτικό ημερήσιο όριο κόστους (`> 0 $`) και single-instance PID έλεγχο.
- **Σημειώσεις Obsidian (`notes_folder`), Ροή Περιεχομένου, Επιμελητής Skills & Advanced Εργαλεία Προγραμματιστή**: `search_notes`/`propose_note_edit` (κλειστό by default), skill `content-workflow`, κουμπί «🧹 Έλεγχος skills», φύλακας KV cache (`SessionFrame`/`CacheGuard`), Git commit/draft PR (`git`), Plugin SDK (`plugin_sdk`) και ACP editor server (`Akatikito.cmd acp`) αυστηρά πίσω από το Advanced.
- **Κοινός Πυρήνας Windows + Android (`sync-core.cmd`) & Οδηγοί 5 Βημάτων**: αυτόματος συγχρονισμός 145+ κοινών αρχείων προς το Android APK (`v1.9.0`), καθαρή δοκιμή εγκατάστασης/αναβάθμισης/επαναφοράς (`scripts/clean_install_check.ps1`) και οδηγοί `docs/GETTING-STARTED.{el,en}.md` με εικόνες SmartScreen.

### Διορθώθηκε & Βελτιώθηκε
- **Ποιότητα & Ταχύτητα Ελέγχων**: 146/146 αρχεία tests σε ~2,5 λεπτά (παράλληλος runner απομόνωσης, 0 flaky), max CC 137 → 40 (-70,8%), 0 `AK-SOURCE-READ-TEST`, υποστήριξη UTF-8 BOM στο `Settings.load()` και σύνδεση σε τοπικά Ollama/LM Studio χωρίς υποχρεωτικό API key.

---

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
