# Akatikito — Αρχιτεκτονική & Οδηγός Έκδοσης (GitHub Pro)

Το **Akatikito** είναι ένας τοπικός, ασφαλής AI agent για Windows με desktop UI, εργαλεία γραφείου & κώδικα, οργάνωση αρχείων, απομονωμένο web browser, MCP hub, προγραμματισμένες εργασίες και κανάλια Telegram/Discord.

## 5 Πυλώνες Ασφάλειας & Ποιότητας

1. **Windows DPAPI Secret Vault (`akatikito/secret_store.py`)**
   - Όλα τα API keys (`settings.json`), τα MCP μυστικά (`env`/`headers` στο `mcp.json`) και τα bot tokens (`channels.json`) κρυπτογραφούνται με `CryptProtectData` των Windows ανά χρήστη (`dpapi:v1:`), με αυτόματη συμβατότητα ανάγνωσης παλαιών `v1:` καταχωρήσεων.
2. **Ασφαλής Μηχανή Αρχείων & Κώδικα**
   - Συναλλακτικές αλλαγές (`editing.py`), bare Git checkpoints (`checkpoints.py`), NTFS οργάνωση με προεπισκόπηση, backups, journal & Undo (`file_organization.py`, `fs_snapshot.py`, `organization_tools.py`).
   - Υβριδική πλοήγηση κώδικα: pinned native Tree-sitter grammars για 12 γλώσσες (`syntax_index.py`) + offline parsers για Vue/Svelte/Angular/CSS (`template_parsers/parse.mjs`) με αυτόματο fallback στους ενσωματωμένους scanners.
3. **Απομονωμένο Web, Έγγραφα & Εργαλεία**
   - `browser_web.py`: Headless Chromium μέσω CDP με DNS-pinned `PublicProxy` (αποκλεισμός loopback/ιδιωτικών IP σε sub-requests, χωρίς downloads, χωρίς API keys στο environment).
   - Πραγματικά Word/Excel/PDF (Excel με literal string cells κατά του Formula Injection, PDF με CDP primary + pure-Python TrueType vector Greek fallback), 50-ψηφίων `Decimal` υπολογιστής, αυστηρός CSV/JSON/Markdown μετατροπέας, σκληραγωγημένο `git_status_diff`.
4. **Προγραμματισμός, MCP & Υποστήριξη**
   - `scheduler.py`: hash-authorized αυτόματες εργασίες, απομονωμένες συνεδρίες και `uncertain` crash recovery.
   - `mcp_client.py`: 1-click presets, Claude/Cursor JSON import, masked editing, stderr capture με redaction, hash-bound `autoConnect`.
   - `diagnostics.py`: 1-click Έλεγχος Υγείας & εξαγωγή ανωνυμοποιημένου `.zip` διαγνωστικών (μηδέν διαρροή secrets).
5. **Open-Source GitHub Release & CI/CD (χωρίς ανάγκη πληρωμένης υπογραφής Windows)**
   - `Install.cmd`: 1-click εγκατάσταση μετά από `git clone` ή λήψη ZIP.
   - `packaging/build_portable.py`: φτιάχνει καθαρό `Akatikito-vX.Y.Z-portable.zip` + `MANIFEST.json` + `SHA256SUMS.txt` με σκληρό έλεγχο κατά της διαρροής προσωπικών αρχείων.
   - `.github/workflows/ci.yml` & `release.yml`: αυτόματα tests + 10-scenario eval harness (`scripts/run_evals.py`) σε Windows runner και αυτόματη δημοσίευση GitHub Release σε κάθε tag `v*`.

## Γρήγορη Εκκίνηση (Για Χρήστες & Testers από GitHub)

```cmd
git clone <repo-url>
cd Akatikito
Install.cmd
AkatikitoApp.cmd
```

## Τοπική Επαλήθευση & Benchmarks

```cmd
.venv\Scripts\python.exe scripts\run_evals.py
.venv\Scripts\python.exe scripts\run_evals.py --live
.venv\Scripts\python.exe packaging\build_portable.py
```
