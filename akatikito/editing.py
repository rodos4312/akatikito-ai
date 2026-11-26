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


def apply_edits(original, edits):
    normalized,positions=_normalized(original)
    spans=[]
    for number,entry in enumerate(edits,1):
        search,_=_normalized(entry["search"])
        starts=[m.start() for m in re.finditer("(?="+re.escape(search)+")",normalized)]
        matches=[(s,s+len(search),0) for s in starts]
        source_lines=normalized.splitlines(keepends=True);needle=search.splitlines();offsets=[0]
        for line in source_lines:offsets.append(offsets[-1]+len(line))
        if not matches:
            for i in range(len(source_lines)-len(needle)+1):
                if all(source_lines[i+j].rstrip()==needle[j].rstrip() for j in range(len(needle))):
                    end=offsets[i+len(needle)]
                    if not search.endswith("\n") and normalized[end-1:end]=="\n":end-=1
                    matches.append((offsets[i],end,0))
        # Step 3: Indentation-normalized fallback when relative indentation is uniform
        if not matches and len(source_lines) <= 10000 and len(needle) <= 200 and needle and any(len(l.strip()) >= 6 for l in needle):
            for i in range(len(source_lines)-len(needle)+1):
                if not all(source_lines[i+j].strip()==needle[j].strip() for j in range(len(needle))):
                    continue
                deltas = set()
                valid_indent = True
                for j in range(len(needle)):
                    s_line = source_lines[i+j]
                    n_line = needle[j]
                    if not n_line.strip():
                        continue
                    if s_line.startswith("\t") or n_line.startswith("\t"):
                        valid_indent = False
                        break
                    s_lead = len(s_line) - len(s_line.lstrip(" "))
                    n_lead = len(n_line) - len(n_line.lstrip(" "))
                    deltas.add(s_lead - n_lead)
                if valid_indent and len(deltas) == 1:
                    delta = next(iter(deltas))
                    end = offsets[i+len(needle)]
                    if not search.endswith("\n") and normalized[end-1:end]=="\n":end-=1
                    matches.append((offsets[i],end,delta))
        hint=entry.get("start_line")
        if hint is not None:
            matches=[(s,e,d) for s,e,d in matches if normalized.count("\n",0,s)+1==hint]
        if not matches:
            closest = _closest_match_hint(source_lines, needle) if hint is None else ""
            raise EditError(f"Block #{number}: το SEARCH δεν βρέθηκε"+(f" στη γραμμή {hint}" if hint else "")+". Διάβασε τον τρέχοντα κώδικα."+closest)
        if len(matches)>1:
            raise EditError(f"Block #{number}: το SEARCH βρέθηκε {len(matches)} φορές. Δώσε μοναδικό search ή start_line της αρχικής έκδοσης.")
        m_start, m_end, indent_delta = matches[0]
        start,end=(positions[m_start], positions[m_end])
        nearby=re.search(r"\r\n|\r|\n",original[start:]) or re.search(r"\r\n|\r|\n",original)
        newline=nearby.group() if nearby else "\n"
        replacement,_=_normalized(entry["replace"])
        if indent_delta != 0:
            replacement = _shift_indentation(replacement, indent_delta)
        spans.append((start,end,replacement.replace("\n",newline)))
    ordered=sorted(spans,key=lambda span:span[0])
    if any(current[0]<previous[1] for previous,current in zip(ordered,ordered[1:])):
        raise EditError("Επικαλυπτόμενες αλλαγές. Όλα τα search/start_line αναφέρονται στην αρχική έκδοση.")
    updated=original
    for start,end,replacement in reversed(ordered):
        updated=updated[:start]+replacement+updated[end:]
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


def check_syntax(target: Path, text: str):
    suffix=target.suffix.lower()
    if isinstance(text,bytes) and suffix not in (".py",".pyw"):
        if suffix not in (".json",".toml",".yaml",".yml",".js",".mjs",".cjs",".ts"):
            return
        try:text=text.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise EditError(f"{target.name}: απαιτείται έγκυρο UTF-8."+_REFUSED) from exc
    if suffix in (".py",".pyw"):
        try:
            compile(text if isinstance(text,bytes) else text.removeprefix("\ufeff"),str(target),"exec",dont_inherit=True)
        except (SyntaxError,ValueError) as exc:
            detail=f"{getattr(exc,'msg',str(exc))}, γραμμή {getattr(exc,'lineno','?')}"
            raise EditError("Μη έγκυρη Python: "+detail+". Απορρίφθηκε ΠΡΙΝ την εγγραφή· το αρχικό αρχείο έμεινε ανέπαφο.") from exc
    elif suffix==".json":
        try:
            json.loads(text.removeprefix("\ufeff"))
        except ValueError as exc:
            raise EditError(f"Μη έγκυρο JSON: {exc}"+_REFUSED) from exc
    elif suffix==".toml":
        try:
            tomllib.loads(text.removeprefix("\ufeff"))
        except tomllib.TOMLDecodeError as exc:
            raise EditError(f"Μη έγκυρο TOML: {exc}"+_REFUSED) from exc
    elif suffix in (".yaml",".yml"):
        try:
            import yaml
        except ImportError:
            return
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise EditError(f"Μη έγκυρο YAML: {exc}"+_REFUSED) from exc
    elif suffix in (".js",".mjs",".cjs",".ts"):
        _check_node(target,text)


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


def commit_batch(ws, changes):
    """Validate first, retain recovery bytes, and report any incomplete rollback."""
    from .local_store import checked
    import io
    import tokenize

    changes = [(Path(target), old, new) for target, old, new in changes if old != new]
    if not changes:
        return ()
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
        if not isinstance(updated, bytes) or original is not None and not isinstance(original, bytes):
            raise EditError(f"{relative}: απαιτούνται ακριβή bytes. Δεν εφαρμόστηκε τίποτα.")
        try:
            check_syntax(target, updated)
            guard = getattr(ws, "write_guard", None)
            if guard:
                def text(raw):
                    raw = raw or b""
                    if target.suffix.lower() in (".py", ".pyw"):
                        encoding = tokenize.detect_encoding(io.BytesIO(raw).readline)[0]
                        return raw.decode(encoding)
                    return raw.decode("utf-8-sig", errors="replace")
                guard(target, text(original), text(updated))
        except (ValueError, UnicodeError, SyntaxError) as exc:
            raise EditError(f"{relative}: {exc} Δεν εφαρμόστηκε τίποτα.") from exc
    review = getattr(ws, "review_write", None)
    for target, original, updated in changes:
        if review is not None and not review(target, original, updated):
            raise EditError(f"[OWNER_DENIED] {ws.relative(target)}: η αλλαγή δεν εγκρίθηκε. Δεν εφαρμόστηκε τίποτα.")

    staged, recovery, attempted, created_dirs, backups = {}, {}, [], [], {}
    def current(target):
        checked(target)
        return target.read_bytes() if target.exists() else None
    def stage(target, raw, kind):
        path = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix="."+target.name+"-"+kind+"-",
                                             suffix=".tmp", delete=False) as stream:
                path = Path(stream.name)
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            return path
        except BaseException:
            if path is not None:
                try:path.unlink(missing_ok=True)
                except OSError:pass
            raise
    try:
        for target, original, updated in changes:
            missing = [d for d in reversed(target.parents)
                       if d != ws.root and ws.root in d.parents and not d.exists()]
            checked(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            created_dirs.extend(d for d in missing if d not in created_dirs)
            staged[target] = stage(target, updated, "edit")
            if original is not None:
                recovery[target] = stage(target, original, "restore")
            if current(target) != original:
                raise EditError(f"{ws.relative(target)}: άλλαξε μετά την ανάγνωση.")
        # All backups must succeed before the first source file is replaced.
        for target, original, _ in changes:
            if current(target) != original:
                raise EditError(f"{ws.relative(target)}: άλλαξε κατά την προετοιμασία.")
            if original is not None:
                backups[ws.relative(target)] = ws.backup(target)
        for target, original, updated in changes:
            if current(target) != original:
                raise EditError(f"{ws.relative(target)}: άλλαξε πριν την εγγραφή.")
            attempted.append((target,original,updated))
            if original is None:
                if os.name == "nt":
                    os.rename(staged[target], target)
                else:
                    os.link(staged[target], target)
            else:
                os.replace(staged[target], target)
        return tuple(ws.relative(target) for target, _, _ in changes)
    except Exception as failure:
        failed_label=ws.relative(target)
        rollback_errors = []
        for target, original, updated in reversed(attempted):
            try:
                value = current(target)
                if value == original:
                    continue
                if value != updated:
                    raise OSError("άλλαξε από άλλη διεργασία· δεν αντικαταστάθηκε")
                if original is None:
                    target.unlink()
                else:
                    os.replace(recovery[target], target)
                if current(target) != original:
                    raise OSError("η επαναφορά δεν επαληθεύτηκε")
            except (OSError, ValueError) as exc:
                rollback_errors.append(f"{ws.relative(target)}: {exc}")
        remaining = []
        for target, original, _ in attempted:
            try:
                if current(target) != original:
                    remaining.append(ws.relative(target))
            except (OSError, ValueError):
                remaining.append(ws.relative(target))
        if remaining:
            details = "; ".join(rollback_errors)
            copies = tuple(f"{name}: {ws.root / '.agent_backups' / backup}" for name, backup in backups.items() if backup)
            message = ("ΑΤΕΛΗΣ ΕΠΑΝΑΦΟΡΑ: " + failed_label + ": " + str(failure) +
                       ". Αλλαγμένα ή μη επαληθευμένα αρχεία: " + ", ".join(remaining) +
                       ". Αιτίες: " + details + ". Αντίγραφα ασφαλείας: " + "; ".join(copies))
            raise EditError(message, changed_paths=remaining, rollback_errors=rollback_errors or remaining,
                            recovery_paths=copies) from failure
        raise EditError(f"{failed_label}: {failure}. Δεν εφαρμόστηκε τίποτα· η επαναφορά επαληθεύτηκε.") from failure
    finally:
        for path in (*staged.values(), *recovery.values()):
            try:path.unlink(missing_ok=True)
            except OSError:pass
        for directory in reversed(created_dirs):
            try:directory.rmdir()
            except OSError:pass


