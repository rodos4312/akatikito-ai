"""Strict, unambiguous edits with byte-preserving atomic commits."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path


class EditError(ValueError):
    def __init__(self, message, *, changed_paths=(), rollback_errors=(), recovery_paths=()):
        super().__init__(message)
        self.changed_paths = tuple(changed_paths)
        self.rollback_errors = tuple(rollback_errors)
        self.recovery_paths = tuple(recovery_paths)


class EditResult(str):
    """Keep the text protocol while carrying exact mutation evidence and diff preview to the agent/UI."""
    def __new__(cls, text, changed_paths=(), recovery_required=False, recovery_paths=(), diff=""):
        result = super().__new__(cls, text)
        result.changed_paths = tuple(changed_paths)
        result.recovery_required = bool(recovery_required)
        result.recovery_paths = tuple(recovery_paths)
        result.diff = str(diff or "")
        return result


def _without_last_newline(text):
    return text[:-2] if text.endswith("\r\n") else text[:-1] if text.endswith(("\n","\r")) else text


def parse_edits(diff_text=None, edits=None):
    if edits is not None:
        if diff_text:
            raise EditError("Χρησιμοποίησε edits ή diff_text, όχι και τα δύο.")
        if not isinstance(edits,list) or not 1 <= len(edits) <= 32:
            raise EditError("Το edits πρέπει να περιέχει 1-32 αλλαγές search/replace.")
        result=[]
        for n,entry in enumerate(edits,1):
            if not isinstance(entry,dict) or set(entry)-{"search","replace","start_line"}:
                raise EditError(f"Αλλαγή {n}: επιτρέπονται μόνο search, replace, start_line.")
            if not isinstance(entry.get("search"),str) or not entry["search"].strip() or not isinstance(entry.get("replace"),str):
                raise EditError(f"Αλλαγή {n}: χρειάζονται μη κενό search και replace ως κείμενο.")
            line=entry.get("start_line")
            if line is not None and (type(line) is not int or line<1):
                raise EditError(f"Αλλαγή {n}: start_line είναι θετικός ακέραιος, από 1.")
            result.append(dict(entry))
        return result
    if not isinstance(diff_text,str) or not diff_text.strip():
        raise EditError("Δώσε edits=[{search, replace}] ή πλήρη SEARCH/REPLACE blocks.")
    lines=diff_text.splitlines(keepends=True)
    # A single complete Markdown fence is tolerated; arbitrary surrounding prose is not.
    if lines and lines[0].strip() in ("```","```diff","```text","```python") and lines[-1].strip()=="```":
        lines=lines[1:-1]
    state="outside";search=[];replace=[];result=[]
    for line in lines:
        marker=line.strip()
        if marker=="<<<<<<< SEARCH":
            if state!="outside":
                raise EditError("Εμφωλευμένο SEARCH block. Για κυριολεκτικά διαχωριστικά χρησιμοποίησε edits.")
            state="search";search=[];replace=[]
        elif marker=="=======":
            if state!="search":
                raise EditError("Πλεονάζον ή αμφίσημο =======. Καμία αλλαγή δεν γράφτηκε. Χρησιμοποίησε δομημένο edits για κυριολεκτικά διαχωριστικά.")
            state="replace"
        elif marker==">>>>>>> REPLACE":
            if state!="replace":
                raise EditError("REPLACE χωρίς πλήρες SEARCH/διαχωριστικό.")
            old=_without_last_newline("".join(search));new=_without_last_newline("".join(replace))
            if not old.strip():
                raise EditError("Το SEARCH είναι κενό.")
            result.append({"search":old,"replace":new});state="outside"
        elif state=="outside":
            if marker:
                raise EditError("Κείμενο έξω από πλήρες SEARCH/REPLACE block. Δεν επιτρέπεται μερική εφαρμογή.")
        elif state=="search":
            search.append(line)
        else:
            replace.append(line)
    if state!="outside" or not result:
        raise EditError("Ελλιπές SEARCH/REPLACE block. Καμία αλλαγή δεν γράφτηκε.")
    return result


def _normalized(text):
    out=[];positions=[];i=0
    while i<len(text):
        positions.append(i)
        if text[i]=="\r":
            out.append("\n");i+=2 if text[i:i+2]=="\r\n" else 1
        else:
            out.append(text[i]);i+=1
    positions.append(len(text))
    return "".join(out),positions


def _closest_match_hint(source_lines: list[str], needle: list[str]) -> str:
    if not source_lines or not needle:
        return ""
    import difflib
    first_nonempty = next((l.strip() for l in needle if l.strip()), "")
    if not first_nonempty:
        return ""
    import time
    deadline = time.monotonic() + 0.08
    best_ratio = 0.0
    best_idx = -1
    win = max(1, min(len(needle), len(source_lines)))
    needle_joined = "\n".join(l.strip() for l in needle)[:10000]
    for i in range(min(len(source_lines), 5000)):
        if time.monotonic() > deadline:
            break
        line_s = source_lines[i].strip()[:500]
        if not line_s:
            continue
        r = difflib.SequenceMatcher(None, first_nonempty[:500], line_s).ratio()
        if r >= 0.55:
            chunk = "\n".join(l.strip() for l in source_lines[i:min(len(source_lines), i + win)])[:10000]
            full_r = difflib.SequenceMatcher(None, needle_joined, chunk).ratio()
            score = max(r * 0.7, full_r)
            if score > best_ratio:
                best_ratio = score
                best_idx = i
    if best_idx < 0 or best_ratio < 0.52:
        return ""
    s_l = max(1, best_idx)
    e_l = min(len(source_lines), best_idx + win + 2)
    snippet = "\n".join(f"{ln:>5} | {source_lines[ln-1].rstrip()}" for ln in range(s_l, e_l + 1))
    return f"\nΠλησιέστερο σημείο στο αρχείο (γραμμές {s_l}-{e_l}):\n{snippet}"


def _shift_indentation(text: str, delta: int) -> str:
    if delta == 0:
        return text
    out = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip(" ")
        if not stripped or stripped in ("\n", "\r\n", "\r"):
            out.append(line)
        elif delta > 0:
            out.append((" " * delta) + line)
        else:
            lead = len(line) - len(stripped)
            remove = min(lead, -delta)
            out.append(line[remove:])
    return "".join(out)


def _uniform_indent_delta(source_lines: list[str], needle: list[str], start_idx: int) -> int | None:
    deltas = set()
    for j, n_line in enumerate(needle):
        if not n_line.strip():
            continue
        s_line = source_lines[start_idx + j]
        if s_line.startswith("\t") or n_line.startswith("\t"):
            return None
        deltas.add((len(s_line) - len(s_line.lstrip(" "))) - (len(n_line) - len(n_line.lstrip(" "))))
    return next(iter(deltas)) if len(deltas) == 1 else None


def _find_indent_matches(normalized: str, source_lines: list[str], needle: list[str], offsets: list[int], search: str):
    if not (len(source_lines) <= 10000 and len(needle) <= 200 and needle and any(len(ln.strip()) >= 6 for ln in needle)):
        return []
    matches = []
    for i in range(len(source_lines) - len(needle) + 1):
        if not all(source_lines[i + j].strip() == needle[j].strip() for j in range(len(needle))):
            continue
        delta = _uniform_indent_delta(source_lines, needle, i)
        if delta is not None:
            end = offsets[i + len(needle)]
            if not search.endswith("\n") and normalized[end - 1 : end] == "\n":
                end -= 1
            matches.append((offsets[i], end, delta))
    return matches


_UNICODE_PUNCT_TRANS = str.maketrans({
    "\u201c": '"', "\u201d": '"',
    "\u2018": "'", "\u2019": "'",
    "\u2014": "--", "\u2013": "-",
    "\u2026": "...", "\u00a0": " ",
    "\u2212": "-", "\u202f": " ",
    "\u2009": " ", "\u3000": " ",
})


def _find_unicode_matches(normalized: str, source_lines: list[str], needle: list[str], offsets: list[int], search: str):
    if not (needle and len(source_lines) <= 10000 and len(needle) <= 200):
        return []
    norm_needle = [ln.translate(_UNICODE_PUNCT_TRANS).rstrip() for ln in needle]
    matches = []
    for i in range(len(source_lines) - len(needle) + 1):
        if all(source_lines[i + j].translate(_UNICODE_PUNCT_TRANS).rstrip() == norm_needle[j] for j in range(len(needle))):
            end = offsets[i + len(needle)]
            if not search.endswith("\n") and normalized[end - 1 : end] == "\n":
                end -= 1
            matches.append((offsets[i], end, 0))
    return matches


_BACKSLASH_RUN_RE = re.compile(r"\\+")


def _find_escape_drift_matches(normalized: str, search: str, replacement: str):
    """Conservative recovery when an LLM doubles backslashes in search (e.g. Windows paths/regex).

    Only activates when every backslash run in search has even length and the halved form
    exists verbatim in the file. Refuses silently rewriting replacements that contain backslashes.
    """
    if "\\" not in search or search in normalized:
        return []
    runs = _BACKSLASH_RUN_RE.findall(search)
    if not runs or not all(len(r) % 2 == 0 for r in runs):
        return []
    halved = _BACKSLASH_RUN_RE.sub(lambda m: "\\" * (len(m.group(0)) // 2), search)
    starts = [m.start() for m in re.finditer("(?=" + re.escape(halved) + ")", normalized)]
    if not starts:
        return []
    if "\\" in replacement:
        raise EditError(
            "Εντοπίστηκε διπλό escaping backslashes (\\\\) στο search, αλλά και το replace περιέχει backslashes — "
            "διάβασε ξανά το αρχείο και στείλε το ακριβές search/replace."
        )
    return [(s, s + len(halved), 0) for s in starts]


def _find_edit_matches(normalized: str, source_lines: list[str], needle: list[str], offsets: list[int], search: str, replacement: str = ""):
    starts = [m.start() for m in re.finditer("(?=" + re.escape(search) + ")", normalized)]
    matches = [(s, s + len(search), 0) for s in starts]
    if not matches:
        for i in range(len(source_lines) - len(needle) + 1):
            if all(source_lines[i + j].rstrip() == needle[j].rstrip() for j in range(len(needle))):
                end = offsets[i + len(needle)]
                if not search.endswith("\n") and normalized[end - 1 : end] == "\n":
                    end -= 1
                matches.append((offsets[i], end, 0))
    if not matches:
        matches = _find_indent_matches(normalized, source_lines, needle, offsets, search)
    if not matches:
        matches = _find_unicode_matches(normalized, source_lines, needle, offsets, search)
    if not matches:
        matches = _find_escape_drift_matches(normalized, search, replacement)
    return matches


def apply_edits(original, edits):
    normalized, positions = _normalized(original)
    source_lines = normalized.splitlines(keepends=True)
    offsets = [0]
    for line in source_lines:
        offsets.append(offsets[-1] + len(line))
    spans = []
    for number, entry in enumerate(edits, 1):
        search, _ = _normalized(entry["search"])
        replacement_raw, _ = _normalized(entry["replace"])
        needle = search.splitlines()
        matches = _find_edit_matches(normalized, source_lines, needle, offsets, search, replacement_raw)
        hint = entry.get("start_line")
        if hint is not None:
            matches = [(s, e, d) for s, e, d in matches if normalized.count("\n", 0, s) + 1 == hint]
        if not matches:
            closest = _closest_match_hint(source_lines, needle) if hint is None else ""
            raise EditError(
                f"Block #{number}: το SEARCH δεν βρέθηκε"
                + (f" στη γραμμή {hint}" if hint else "")
                + ". Διάβασε τον τρέχοντα κώδικα."
                + closest
            )
        if len(matches) > 1:
            raise EditError(f"Block #{number}: το SEARCH βρέθηκε {len(matches)} φορές. Δώσε μοναδικό search ή start_line της αρχικής έκδοσης.")
        m_start, m_end, indent_delta = matches[0]
        start, end = positions[m_start], positions[m_end]
        nearby = re.search(r"\r\n|\r|\n", original[start:]) or re.search(r"\r\n|\r|\n", original)
        newline = nearby.group() if nearby else "\n"
        replacement, _ = _normalized(entry["replace"])
        if indent_delta != 0:
            replacement = _shift_indentation(replacement, indent_delta)
        spans.append((start, end, replacement.replace("\n", newline)))
    ordered = sorted(spans, key=lambda span: span[0])
    if any(current[0] < previous[1] for previous, current in zip(ordered, ordered[1:])):
        raise EditError("Επικαλυπτόμενες αλλαγές. Όλα τα search/start_line αναφέρονται στην αρχική έκδοση.")
    updated = original
    for start, end, replacement in reversed(ordered):
        updated = updated[:start] + replacement + updated[end:]
    return updated


_REFUSED = ". Απορρίφθηκε ΠΡΙΝ την εγγραφή· το αρχικό αρχείο έμεινε ανέπαφο."
_node_ts = {}


def _node_supports_ts(node):
    # Παλιότερο node δεν καταλαβαίνει τύπους TypeScript και θα απέρριπτε έγκυρο κώδικα.
    if node not in _node_ts:
        try:
            out=subprocess.run([node,"--version"],capture_output=True,text=True,timeout=5).stdout.strip().lstrip("v")
            major,minor=(int(n) for n in out.split(".")[:2])
            _node_ts[node]=(major,minor)>=(22,18)
        except (OSError,ValueError,subprocess.SubprocessError):
            _node_ts[node]=False
    return _node_ts[node]


def _check_node(target: Path, text: str):
    node=shutil.which("node")
    suffix=target.suffix.lower()
    if node is None or (suffix==".ts" and not _node_supports_ts(node)):
        return
    temporary=None
    try:
        # Το node ελέγχει αρχείο στο δίσκο· χρησιμοποιούμε προσωρινό ώστε το πραγματικό να μείνει ανέπαφο.
        with tempfile.NamedTemporaryFile(prefix="syntax-",suffix=suffix,delete=False) as stream:
            temporary=Path(stream.name);stream.write(text.encode("utf-8"))
        result=subprocess.run([node,"--check",str(temporary)],capture_output=True,text=True,timeout=5)
    except (OSError,subprocess.SubprocessError):
        return
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    # Μόνο πραγματικό SyntaxError μπλοκάρει· κάθε άλλη αποτυχία του node (crash, ρυθμίσεις) αγνοείται.
    if result.returncode!=0 and "SyntaxError" in result.stderr:
        detail=next((l.strip() for l in result.stderr.splitlines() if "SyntaxError" in l),"SyntaxError")
        raise EditError(f"Μη έγκυρο {suffix[1:].upper()}: {detail}"+_REFUSED)


def _check_html_scripts(target: Path, text: str) -> None:
    """Validate balanced <script> tags and inline JS syntax inside HTML files."""
    opens = list(re.finditer(r"<script\b([^>]*)>", text, re.I))
    closes = list(re.finditer(r"</script\s*>", text, re.I))
    if len(opens) != len(closes):
        raise EditError(
            f"Μη ισορροπημένα tags <script> στο {target.name} "
            f"({len(opens)} ανοίγματα / {len(closes)} κλεισίματα)" + _REFUSED
        )
    for idx, (op, cl) in enumerate(zip(opens, closes), 1):
        if cl.start() < op.end():
            raise EditError(f"Λάθος σειρά <script> / </script> στο {target.name} (block #{idx})" + _REFUSED)
        attrs = op.group(1) or ""
        if re.search(r"\bsrc\s*=", attrs, re.I):
            continue
        type_m = re.search(r"""\btype\s*=\s*['"]?([^'"\s>]+)""", attrs, re.I)
        script_type = (type_m.group(1) if type_m else "").lower()
        if script_type and script_type not in ("text/javascript", "application/javascript", "module"):
            continue
        body = text[op.end():cl.start()]
        if body.strip():
            _check_node(Path(f"{target.stem}_script{idx}.js"), body)


def _check_py(target: Path, text) -> None:
    try:
        compile(text if isinstance(text, bytes) else text.removeprefix("\ufeff"), str(target), "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as exc:
        detail = f"{getattr(exc, 'msg', str(exc))}, γραμμή {getattr(exc, 'lineno', '?')}"
        raise EditError("Μη έγκυρη Python: " + detail + ". Απορρίφθηκε ΠΡΙΝ την εγγραφή· το αρχικό αρχείο έμεινε ανέπαφο.") from exc


def _check_json(_target: Path, text: str) -> None:
    try:
        json.loads(text.removeprefix("\ufeff"))
    except ValueError as exc:
        raise EditError(f"Μη έγκυρο JSON: {exc}" + _REFUSED) from exc


def _check_toml(_target: Path, text: str) -> None:
    try:
        tomllib.loads(text.removeprefix("\ufeff"))
    except tomllib.TOMLDecodeError as exc:
        raise EditError(f"Μη έγκυρο TOML: {exc}" + _REFUSED) from exc


def _check_yaml(_target: Path, text: str) -> None:
    try:
        import yaml
    except ImportError:
        return
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EditError(f"Μη έγκυρο YAML: {exc}" + _REFUSED) from exc


_TEXT_SYNTAX_CHECKERS = {
    ".json": _check_json,
    ".toml": _check_toml,
    ".yaml": _check_yaml,
    ".yml": _check_yaml,
    ".js": _check_node,
    ".mjs": _check_node,
    ".cjs": _check_node,
    ".ts": _check_node,
    ".html": _check_html_scripts,
    ".htm": _check_html_scripts,
}


def check_syntax(target: Path, text: str):
    suffix = target.suffix.lower()
    if suffix in (".py", ".pyw"):
        _check_py(target, text)
        return
    checker = _TEXT_SYNTAX_CHECKERS.get(suffix)
    if checker is None:
        return
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise EditError(f"{target.name}: απαιτείται έγκυρο UTF-8." + _REFUSED) from exc
    checker(target, text)


def commit(ws, target: Path, original: bytes | None, updated: bytes):
    relative = target.relative_to(ws.root)
    if '.git' in relative.parts or relative.parts[:2] in (('.agent','checkpoints'),('.agent','context')):
        raise EditError('Τα εσωτερικά δεδομένα Git/checkpoint/context δεν αλλάζουν με εργαλεία αρχείων.')
    review = getattr(ws,'review_write',None)
    if review is not None and not review(target,original,updated):
        raise EditError('[OWNER_DENIED] Ο χρήστης δεν ενέκρινε την αλλαγή αρχείου.')
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent,prefix="."+target.name+"-",suffix=".tmp",delete=False) as stream:
            temporary=Path(stream.name);stream.write(updated);stream.flush();os.fsync(stream.fileno())
        if original is None:
            if os.name=="nt":os.rename(temporary,target)  # Windows rename refuses an existing destination.
            else:os.link(temporary,target)
        else:
            if target.read_bytes()!=original:
                raise EditError("Το αρχείο άλλαξε μετά την ανάγνωση. Η αλλαγή ακυρώθηκε χωρίς αντικατάσταση.")
            ws.backup(target)
            os.replace(temporary,target)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def _decode_guard_text(target: Path, raw: bytes | None) -> str:
    import io
    import tokenize

    raw = raw or b""
    if target.suffix.lower() in (".py", ".pyw"):
        encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0]
        return raw.decode(encoding)
    return raw.decode("utf-8-sig", errors="replace")


def _validate_batch_changes(ws, changes, checked) -> None:
    seen = set()
    for target, original, updated in changes:
        try:
            checked(target)
            relative = target.relative_to(ws.root)
        except (ValueError, OSError) as exc:
            raise EditError(f"Μη επιτρεπτή διαδρομή: {target}. Δεν εφαρμόστηκε τίποτα.") from exc
        if target == ws.root or target in seen:
            raise EditError(f"{relative}: διπλή ή μη έγκυρη διαδρομή. Δεν εφαρμόστηκε τίποτα.")
        seen.add(target)
        parts = tuple(p.casefold() for p in relative.parts)
        if ".git" in parts or parts[:2] in ((".agent", "checkpoints"), (".agent", "context")):
            raise EditError(f"{relative}: προστατευμένα εσωτερικά δεδομένα. Δεν εφαρμόστηκε τίποτα.")
        if not isinstance(updated, bytes) or (original is not None and not isinstance(original, bytes)):
            raise EditError(f"{relative}: απαιτούνται ακριβή bytes. Δεν εφαρμόστηκε τίποτα.")
        try:
            check_syntax(target, updated)
            guard = getattr(ws, "write_guard", None)
            if guard:
                guard(target, _decode_guard_text(target, original), _decode_guard_text(target, updated))
        except (ValueError, UnicodeError, SyntaxError) as exc:
            raise EditError(f"{relative}: {exc} Δεν εφαρμόστηκε τίποτα.") from exc
    review = getattr(ws, "review_write", None)
    for target, original, updated in changes:
        if review is not None and not review(target, original, updated):
            raise EditError(f"[OWNER_DENIED] {ws.relative(target)}: η αλλαγή δεν εγκρίθηκε. Δεν εφαρμόστηκε τίποτα.")


def _stage_temp_bytes(target: Path, raw: bytes, kind: str) -> Path:
    path = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix="." + target.name + "-" + kind + "-",
                                         suffix=".tmp", delete=False) as stream:
            path = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return path
    except BaseException:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                return path
        raise


def _rollback_batch(ws, attempted, recovery, backups, failed_label, failure, current_fn) -> None:
    rollback_errors = []
    for target, original, updated in reversed(attempted):
        try:
            value = current_fn(target)
            if value == original:
                continue
            if value != updated:
                raise OSError("άλλαξε από άλλη διεργασία· δεν αντικαταστάθηκε")
            if original is None:
                target.unlink()
            else:
                os.replace(recovery[target], target)
            if current_fn(target) != original:
                raise OSError("η επαναφορά δεν επαληθεύτηκε")
        except (OSError, ValueError) as exc:
            rollback_errors.append(f"{ws.relative(target)}: {exc}")
    remaining = []
    for target, original, _ in attempted:
        try:
            if current_fn(target) != original:
                remaining.append(ws.relative(target))
        except (OSError, ValueError):
            remaining.append(ws.relative(target))
    if remaining:
        details = "; ".join(rollback_errors)
        copies = tuple(f"{name}: {ws.root / '.agent_backups' / backup}" for name, backup in backups.items() if backup)
        message = (
            "ΑΤΕΛΗΣ ΕΠΑΝΑΦΟΡΑ: " + failed_label + ": " + str(failure)
            + ". Αλλαγμένα ή μη επαληθευμένα αρχεία: " + ", ".join(remaining)
            + ". Αιτίες: " + details + ". Αντίγραφα ασφαλείας: " + "; ".join(copies)
        )
        raise EditError(message, changed_paths=remaining, rollback_errors=rollback_errors or remaining,
                        recovery_paths=copies) from failure
    raise EditError(f"{failed_label}: {failure}. Δεν εφαρμόστηκε τίποτα· η επαναφορά επαληθεύτηκε.") from failure


def _cleanup_batch_artifacts(staged: dict, recovery: dict, created_dirs: list[Path]) -> None:
    for path in (*staged.values(), *recovery.values()):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
    for directory in reversed(created_dirs):
        try:
            directory.rmdir()
        except OSError:
            continue


def commit_batch(ws, changes):
    """Validate first, retain recovery bytes, and report any incomplete rollback."""
    from .local_store import checked

    changes = [(Path(target), old, new) for target, old, new in changes if old != new]
    if not changes:
        return ()
    _validate_batch_changes(ws, changes, checked)
    staged, recovery, attempted, created_dirs, backups = {}, {}, [], [], {}

    def current(target):
        checked(target)
        return target.read_bytes() if target.exists() else None

    active_target = changes[0][0]
    try:
        for target, original, updated in changes:
            active_target = target
            missing = [d for d in reversed(target.parents) if d != ws.root and ws.root in d.parents and not d.exists()]
            checked(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            created_dirs.extend(d for d in missing if d not in created_dirs)
            staged[target] = _stage_temp_bytes(target, updated, "edit")
            if original is not None:
                recovery[target] = _stage_temp_bytes(target, original, "restore")
            if current(target) != original:
                raise EditError(f"{ws.relative(target)}: άλλαξε μετά την ανάγνωση.")
        # All backups must succeed before the first source file is replaced.
        for target, original, _ in changes:
            active_target = target
            if current(target) != original:
                raise EditError(f"{ws.relative(target)}: άλλαξε κατά την προετοιμασία.")
            if original is not None:
                backups[ws.relative(target)] = ws.backup(target)
        for target, original, updated in changes:
            active_target = target
            if current(target) != original:
                raise EditError(f"{ws.relative(target)}: άλλαξε πριν την εγγραφή.")
            attempted.append((target, original, updated))
            if original is None:
                os.rename(staged[target], target) if os.name == "nt" else os.link(staged[target], target)
            else:
                os.replace(staged[target], target)
        return tuple(ws.relative(target) for target, _, _ in changes)
    except Exception as failure:  # quality: allow BLE001 — atomic rollback must run on any write/OS/guard failure
        _rollback_batch(ws, attempted, recovery, backups, ws.relative(active_target), failure, current)
    finally:
        _cleanup_batch_artifacts(staged, recovery, created_dirs)


