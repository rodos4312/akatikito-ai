# Ιστορικό Αλλαγών (Changelog) — Akatikito AI

Όλες οι σημαντικές αλλαγές σε απλά ελληνικά.

## [Υπό ανάπτυξη — μετά την v2.4.0]

### Προστέθηκε
- **Παράλληλοι βοηθοί (`run_subtasks`)**: έως 2 ταυτόχρονοι βοηθοί με δήλωση αρχείων (`FileClaims`), κοινό lock εντολών, προειδοποίηση στο 80% του ορίου tokens, διακοπή μετά από 3 βήματα χωρίς πρόοδο, `Stop` σε όλους και απομόνωση γύρου (`_turn_id`).
- **🩺💉 Γιατρός (`akatikito/doctor.py` + `Doctor.cmd`)**: 14 read-only διαγνωστικοί έλεγχοι και 1-click ασφαλής επισκευή με preview, backup και καταγραφή στο Audit Log (προστατεύει Κάδο, αρχεία χρήστη και Audit chain).
- **Συγχρονισμός core PC ↔ Κινητό**: `scripts/sync_core.py` (και `--check` για ανίχνευση αποκλίσεων πριν το APK build).
- **Αυτόματοι έλεγχοι ποιότητας**: `check.cmd`, git hooks (`pre-commit` / `pre-push`), GitHub CI (`windows-latest`), `pip-audit`, `scripts/size_check.py` (όριο 800 γραμμών) και απομόνωση tests από το πραγματικό `data/`.

### Διορθώθηκε & Βελτιώθηκε
- **Ασφάλεια & αξιοπιστία**: αυτόματο `ws.backup()` πριν από overwrite (mobile upload & tool plugins), session-sticky Smart Routing (χωρίς σπάσιμο cache), ρητή προειδοποίηση HTTP LAN στο Mobile Bridge, κρυπτογραφημένο `flutter_secure_storage` & αυστηρό `key.properties` στο Android build.
- **Δομή κώδικα**: σπάσιμο μεγάλων αρχείων (`agent.py`, `app_api.py`, `ui.html`, `main.dart`) σε μικρά, καθαρά modules (<1.000 γραμμές).

---

## [v2.4.0] — 2026-11-23

### Προστέθηκε
- Νέο Master-Detail UI με Tabs (Συνομιλίες, Ατζέντα, Πάροχοι, Plugins, Skills, Μνήμη, Ρυθμίσεις).
- Cross-Provider Smart Model Routing (διαφορετικός πάροχος/μοντέλο για απλές vs δύσκολες εργασίες).
- 3 Native Tool Plugins (`pdf_toolkit`, `image_tools`, `zip_manager`) + 5 ενσωματωμένα Skills & εισαγωγή `SKILL.md`.
- Email (IMAP/SMTP) & Slack integrations.
- Mobile Bridge v2 (Tailscale + LAN, μεταφορά αρχείων/φακέλων, Android APK v1.7.0 με release υπογραφή).
- Επίσημο λογότυπο & ανανεωμένη σελίδα GitHub Pages.
