"""Native Python Tool Plugins: 1-click extensible tools for Akatikito without requiring Node.js or external MCP servers."""
from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import stat
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path

from .local_store import LOCK, load_json, save_json

PLUGIN_ID = re.compile(r"[a-z][a-z0-9-]{1,48}")

_DEC_CONSTANTS = {
    "pi": Decimal("3.14159265358979323846264338327950288419716939937510"),
    "e": Decimal("2.71828182845904523536028747135266249775724709369995"),
}


def _safe_eval_decimal(expr: str) -> Decimal:
    """Evaluate mathematical expression strictly via AST whitelist using 50-digit Decimal arithmetic."""
    src = expr.strip()
    if not src or len(src) > 400:
        raise ValueError("Δώσε μαθηματική έκφραση έως 400 χαρακτήρες.")
    tree = ast.parse(src, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 200:
        raise ValueError("Πολύ σύνθετη μαθηματική έκφραση.")

    def _check_num(v: Decimal) -> Decimal:
        if not v.is_finite() or (v and abs(v.adjusted()) > 1000):
            raise ValueError("Ο αριθμός ξεπερνά το όριο.")
        return v

    def _eval(node, depth: int = 0) -> Decimal:
        if depth > 25:
            raise ValueError("Πολύ βαθιά μαθηματική έκφραση.")
        if isinstance(node, ast.Expression):
            return _eval(node.body, depth)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            seg = ast.get_source_segment(src, node)
            raw = seg.replace("_", "") if seg else str(node.value)
            return _check_num(Decimal(raw))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            val = _eval(node.operand, depth + 1)
            return val if isinstance(node.op, ast.UAdd) else -val
        if isinstance(node, ast.BinOp):
            left, right = _eval(node.left, depth + 1), _eval(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                return _check_num(left + right)
            if isinstance(node.op, ast.Sub):
                return _check_num(left - right)
            if isinstance(node.op, ast.Mult):
                return _check_num(left * right)
            if isinstance(node.op, ast.Div):
                return _check_num(left / right)
            if isinstance(node.op, ast.FloorDiv):
                return _check_num(left // right)
            if isinstance(node.op, ast.Mod):
                return _check_num(left % right)
            if isinstance(node.op, ast.Pow):
                if abs(right) > 1000:
                    raise ValueError("Πολύ μεγάλος εκθέτης.")
                if right == right.to_integral_value():
                    return _check_num(left ** int(right))
                out = float(left) ** float(right)
                if not math.isfinite(out):
                    raise ValueError("Μη έγκυρο αριθμητικό αποτέλεσμα.")
                return _check_num(Decimal(str(out)))
        if isinstance(node, ast.Name) and node.id in _DEC_CONSTANTS:
            return _DEC_CONSTANTS[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.keywords:
                raise ValueError("Δεν επιτρέπονται ονομαστικά ορίσματα στη μαθηματική έκφραση.")
            fname = node.func.id
            if fname in ("sum", "min", "max") and len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple)):
                seq = [_eval(elt, depth + 1) for elt in node.args[0].elts]
                if not seq or len(seq) > 500:
                    raise ValueError("Δώσε 1–500 αριθμούς.")
                if fname == "sum":
                    return _check_num(sum(seq, Decimal(0)))
                return min(seq) if fname == "min" else max(seq)
            args = [_eval(a, depth + 1) for a in node.args]
            if fname == "abs" and len(args) == 1:
                return abs(args[0])
            if fname == "sqrt" and len(args) == 1:
                return _check_num(args[0].sqrt())
            if fname in ("min", "max") and 1 <= len(args) <= 500:
                return min(args) if fname == "min" else max(args)
            if fname == "sum" and 1 <= len(args) <= 500:
                return _check_num(sum(args, Decimal(0)))
            if fname == "round" and len(args) in (1, 2):
                places = int(args[1]) if len(args) == 2 else 0
                if abs(places) > 30:
                    raise ValueError("Πολλά δεκαδικά ψηφία.")
                return _check_num(args[0].quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
            if fname == "pow" and len(args) == 2:
                if abs(args[1]) > 1000:
                    raise ValueError("Πολύ μεγάλος εκθέτης.")
                if args[1] == args[1].to_integral_value():
                    return _check_num(args[0] ** int(args[1]))
                out = float(args[0]) ** float(args[1])
                if not math.isfinite(out):
                    raise ValueError("Μη έγκυρο αριθμητικό αποτέλεσμα.")
                return _check_num(Decimal(str(out)))
            if fname in ("ceil", "floor") and len(args) == 1:
                return Decimal(math.ceil(args[0]) if fname == "ceil" else math.floor(args[0]))
            if fname in ("log", "log10", "sin", "cos", "tan") and len(args) == 1:
                out = getattr(math, fname)(float(args[0]))
                if not math.isfinite(out):
                    raise ValueError("Μη έγκυρο αριθμητικό αποτέλεσμα.")
                return _check_num(Decimal(str(out)))
        raise ValueError("Μη επιτρεπτή μαθηματική έκφραση.")

    try:
        with localcontext() as ctx:
            ctx.prec = 50
            return _check_num(_eval(tree))
    except (ArithmeticError, InvalidOperation) as exc:
        raise ValueError("Μη έγκυρος ή υπερβολικά μεγάλος υπολογισμός.") from exc


def _safe_eval_math(expr: str) -> float:
    val = float(_safe_eval_decimal(expr))
    if not math.isfinite(val):
        raise ValueError("Μη έγκυρο αριθμητικό αποτέλεσμα.")
    return val


# --------------------------------------------------------------------------- Built-in Python Tool Plugin Handlers


def tool_system_diagnostics(ws, check: str = "overview") -> str:
    check = str(check or "overview").strip().lower()
    usage = shutil.disk_usage(ws.root)
    info = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "cpu_cores": os.cpu_count() or 1,
        "project_root": str(ws.root),
        "disk_total_gb": round(usage.total / (1024 ** 3), 2),
        "disk_used_gb": round(usage.used / (1024 ** 3), 2),
        "disk_free_gb": round(usage.free / (1024 ** 3), 2),
        "disk_free_percent": round((usage.free / usage.total) * 100, 1) if usage.total else 0.0,
    }
    if check == "files":
        files_count = 0
        total_bytes = 0
        skip_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        for directory, dirs, names in os.walk(ws.root, followlinks=False):
            kept = []
            for d in dirs:
                if d in skip_dirs:
                    continue
                dp = Path(directory, d)
                try:
                    if dp.is_symlink() or (reparse and getattr(dp.lstat(), "st_file_attributes", 0) & reparse):
                        continue
                except OSError:
                    continue
                kept.append(d)
            dirs[:] = kept
            for name in names:
                p = Path(directory, name)
                try:
                    lst = p.lstat()
                    if p.is_symlink() or (reparse and getattr(lst, "st_file_attributes", 0) & reparse):
                        continue
                    if not stat.S_ISREG(lst.st_mode):
                        continue
                    files_count += 1
                    total_bytes += lst.st_size
                except OSError:
                    continue
                if files_count >= 5000:
                    break
            if files_count >= 5000:
                break
        info["project_files_counted"] = files_count
        info["project_size_mb"] = round(total_bytes / (1024 ** 2), 2)
    return json.dumps(info, ensure_ascii=False, indent=2)


def tool_calculate_expression(ws, expression: str, vat_rate: float | None = None) -> str:
    expr = str(expression or "").strip()
    if not expr or len(expr) > 400:
        raise ValueError("Δώσε μαθηματική έκφραση έως 400 χαρακτήρες.")
    with localcontext() as ctx:
        ctx.prec = 50
        dval = _safe_eval_decimal(expr)
        num = float(dval.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))
        exact_str = format(dval.normalize(), "f") if dval == dval.to_integral_value() else str(dval)
        result = {
            "expression": expr,
            "result": num,
            "result_exact": exact_str,
            "precision_digits": 50,
            "formatted": f"{float(dval):,.2f}",
        }
        if vat_rate is not None:
            try:
                rate = Decimal(str(vat_rate))
            except InvalidOperation as exc:
                raise ValueError("Μη έγκυρος συντελεστής ΦΠΑ.") from exc
            if not rate.is_finite() or not (Decimal(0) <= rate <= Decimal(100)):
                raise ValueError("Ο ΦΠΑ πρέπει να είναι μεταξύ 0 και 100%.")
            factor = Decimal(1) + rate / Decimal(100)
            vat_amount = dval * (rate / Decimal(100))
            gross = dval * factor
            net_from_gross = dval / factor
            q2 = Decimal("0.01")
            result["vat_analysis"] = {
                "vat_rate_percent": float(rate),
                "if_net_then_vat": float(vat_amount.quantize(q2, rounding=ROUND_HALF_UP)),
                "if_net_then_gross": float(gross.quantize(q2, rounding=ROUND_HALF_UP)),
                "if_gross_then_net": float(net_from_gross.quantize(q2, rounding=ROUND_HALF_UP)),
                "if_gross_then_vat": float((dval - net_from_gross).quantize(q2, rounding=ROUND_HALF_UP)),
                "exact_net_to_gross": str(gross),
                "exact_vat_on_net": str(vat_amount),
                "exact_gross_to_net": str(net_from_gross),
            }
    return json.dumps(result, ensure_ascii=False, indent=2)


def tool_convert_data_file(
    ws,
    source_path: str,
    target_format: str = "markdown",
    output_path: str = "",
    encoding: str = "auto",
    expected_sha256: str = "",
) -> str:
    from .tools import _protect_live_source
    from .local_store import checked

    src = checked(ws.resolve(source_path))
    if not src.is_file() or src.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Χρειάζεται υπάρχον αρχείο CSV ή JSON έως 8 MiB.")
    suffix = src.suffix.lower()
    raw_bytes = src.read_bytes()
    enc = str(encoding or "auto").strip().lower()
    if enc == "auto":
        enc = (
            "utf-32" if raw_bytes.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"))
            else "utf-16" if raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff"))
            else "utf-8-sig"
        )
    if enc not in ("utf-8", "utf-8-sig", "utf-16", "utf-32", "cp1253", "cp1252"):
        raise ValueError("Μη επιτρεπτή κωδικοποίηση. Υποστηρίζονται: auto, utf-8, utf-8-sig, utf-16, utf-32, cp1253, cp1252.")
    try:
        raw_text = raw_bytes.decode(enc)
    except UnicodeError as exc:
        raise ValueError("Η κωδικοποίηση δεν ταιριάζει. Επίλεξε ρητά encoding· δεν αντικαταστάθηκαν χαρακτήρες.") from exc

    rows: list[dict] = []
    headers: list[str] = []

    if suffix == ".csv":
        reader = csv.reader(io.StringIO(raw_text))
        raw_headers = next(reader, [])
        if not raw_headers:
            return "Το αρχείο CSV είναι κενό."
        headers = [str(h).strip() for h in raw_headers]
        if any(not h for h in headers) or len(set(headers)) != len(headers) or len(headers) > 100:
            raise ValueError("Χρειάζονται μη κενές και μοναδικές επικεφαλίδες CSV (έως 100 στήλες).")
        for r in reader:
            if not r:
                continue
            if len(r) != len(headers):
                raise ValueError("Ασυνεπές πλήθος στηλών CSV· δεν χάθηκαν κελιά.")
            rows.append(dict(zip(headers, r)))
            if len(rows) > 10000:
                raise ValueError("Όριο 10.000 εγγραφών CSV.")
    elif suffix == ".json":
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list) or len(parsed) > 10000 or not all(isinstance(x, dict) for x in parsed):
            raise ValueError("Το JSON πρέπει να περιέχει λίστα αντικειμένων έως 10.000 εγγραφές.")
        headers = list(dict.fromkeys(str(k) for item in parsed for k in item.keys()))
        if len(headers) > 100:
            raise ValueError("Όριο 100 στηλών JSON.")
        rows = [{str(k): v for k, v in item.items()} for item in parsed]
    else:
        raise ValueError("Υποστηρίζονται αρχεία εισόδου .csv και .json.")

    fmt = str(target_format or "markdown").strip().lower()
    if fmt == "json":
        rendered = json.dumps(rows, ensure_ascii=False, indent=2)
    elif fmt in ("csv", "markdown"):
        if any(isinstance(v, (dict, list)) for r in rows for v in r.values()):
            raise ValueError("Τα σύνθετα JSON δεδομένα (λίστες/αντικείμενα) διατηρούνται μόνο σε μορφή JSON.")
        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=headers, lineterminator="\n")
            writer.writeheader()
            for r in rows:
                writer.writerow({h: ("" if r.get(h) is None else r.get(h)) for h in headers})
            rendered = buf.getvalue()
        else:
            if not headers:
                rendered = "(Χωρίς δεδομένα)"
            else:
                esc = lambda v: str("" if v is None else v).replace("|", "\\|").replace("\n", "<br>")
                head_line = "| " + " | ".join(esc(h) for h in headers) + " |"
                sep_line = "| " + " | ".join("---" for _ in headers) + " |"
                body_lines = [
                    "| " + " | ".join(esc(r.get(h, "")) for h in headers) + " |"
                    for r in rows
                ]
                rendered = "\n".join([head_line, sep_line, *body_lines])
    else:
        raise ValueError("Το target_format πρέπει να είναι 'json', 'csv' ή 'markdown'.")

    if output_path and str(output_path).strip():
        from .editing import check_syntax, commit
        from .tools import EditError

        out_target = checked(ws.resolve(str(output_path).strip()))
        _protect_live_source(out_target)
        expected_ext = {"json": ".json", "csv": ".csv", "markdown": ".md"}[fmt]
        if out_target.suffix.lower() != expected_ext:
            raise ValueError(f"Η κατάληξη του output_path πρέπει να είναι {expected_ext} για μορφή {fmt}.")
        raw_prev = out_target.read_bytes() if out_target.is_file() else None
        if expected_sha256:
            if raw_prev is None:
                raise ValueError("Το αρχικό αρχείο εξόδου δεν υπάρχει πια. Ανανέωσε την ανάγνωση.")
            if hashlib.sha256(raw_prev).hexdigest() != expected_sha256:
                raise ValueError("Το αρχείο εξόδου άλλαξε από το αναμενόμενο SHA256. Διάβασέ το ξανά.")
        orig_text = raw_prev.decode("utf-8", errors="replace") if raw_prev is not None else ""
        try:
            check_syntax(out_target, rendered)
            guard = getattr(ws, "write_guard", None)
            if guard:
                guard(out_target, orig_text, rendered)
            commit(ws, out_target, raw_prev, rendered.encode("utf-8"))
        except (EditError, FileExistsError) as exc:
            return "ΣΦΑΛΜΑ: " + str(exc)
        return f"ΕΓΙΝΕ: μετατράπηκαν {len(rows)} εγγραφές στο {ws.relative(out_target)}\n\n" + rendered[:4000]
    from .tools import _clip_with_archive
    return _clip_with_archive(ws, rendered)


def _guard_output_write(ws, target: Path, overwrite: bool) -> None:
    """Prevent accidental overwrite of existing files or live source files unless explicitly approved."""
    from .tools import _protect_live_source

    _protect_live_source(target)
    guard = getattr(ws, "write_guard", None)
    if callable(guard):
        guard(target, "", "")
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"Το αρχείο '{ws.relative(target)}' υπάρχει ήδη. Διάλεξε νέο όνομα ή όρισε overwrite=true."
        )


def tool_pdf_toolkit(
    ws,
    action: str = "info",
    paths: list[str] | None = None,
    output_path: str = "",
    pages: str = "",
    overwrite: bool = False,
) -> str:
    """Merge, split, or inspect PDF files safely inside the project workspace."""
    from pypdf import PdfReader, PdfWriter

    act = str(action or "info").strip().lower()
    src_list = [str(p).strip() for p in (paths or []) if str(p).strip()]
    if not src_list:
        raise ValueError("Δώσε τουλάχιστον μία διαδρομή PDF στο paths.")

    if act == "info":
        target = ws.resolve(src_list[0])
        if not target.is_file():
            raise ValueError(f"Δεν βρέθηκε το PDF: {src_list[0]}")
        reader = PdfReader(str(target))
        meta = reader.metadata or {}
        title = str(meta.get("/Title") or "—")
        return f"PDF {ws.relative(target)}: {len(reader.pages)} σελίδες · Τίτλος: {title} · Μέγεθος: {target.stat().st_size} bytes"

    if act == "merge":
        if len(src_list) < 2:
            raise ValueError("Για merge χρειάζονται τουλάχιστον 2 PDF αρχεία.")
        if not output_path or not output_path.strip():
            raise ValueError("Δώσε output_path για το ενωμένο PDF.")
        out = ws.resolve(output_path)
        _guard_output_write(ws, out, overwrite)
        writer = PdfWriter()
        total_pages = 0
        for item in src_list[:50]:
            p = ws.resolve(item)
            if not p.is_file():
                raise ValueError(f"Δεν βρέθηκε το PDF: {item}")
            reader = PdfReader(str(p))
            for pg in reader.pages:
                writer.add_page(pg)
                total_pages += 1
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as fh:
            writer.write(fh)
        return f"ΕΓΙΝΕ: ενώθηκαν {len(src_list)} PDF ({total_pages} σελίδες) στο {ws.relative(out)}"

    if act == "split":
        if not output_path or not output_path.strip():
            raise ValueError("Δώσε output_path για το νέο PDF.")
        target = ws.resolve(src_list[0])
        if not target.is_file():
            raise ValueError(f"Δεν βρέθηκε το PDF: {src_list[0]}")
        reader = PdfReader(str(target))
        n = len(reader.pages)
        selected: list[int] = []
        raw_pages = str(pages or f"1-{n}").strip()
        for part in raw_pages.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                if "-" in part:
                    a, b = part.split("-", 1)
                    if not a.strip() or not b.strip():
                        raise ValueError("missing bound")
                    start_i = int(a.strip())
                    end_i = int(b.strip())
                    if start_i < 1 or end_i < start_i:
                        raise ValueError("invalid bounds")
                    selected.extend(range(max(1, start_i), min(n, end_i) + 1))
                else:
                    idx = int(part)
                    if 1 <= idx <= n:
                        selected.append(idx)
            except ValueError as exc:
                raise ValueError(f"Μη έγκυρο εύρος σελίδων '{part}' (παράδειγμα: '1-3,5').") from exc
        if not selected:
            raise ValueError(f"Μη έγκυρο εύρος σελίδων (1–{n}).")
        writer = PdfWriter()
        for idx in selected:
            writer.add_page(reader.pages[idx - 1])
        out = ws.resolve(output_path)
        _guard_output_write(ws, out, overwrite)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as fh:
            writer.write(fh)
        return f"ΕΓΙΝΕ: εξήχθησαν {len(selected)} σελίδες από {ws.relative(target)} στο {ws.relative(out)}"

    raise ValueError("Μη έγκυρη ενέργεια pdf_toolkit (χρησιμοποίησε info, merge ή split).")


def tool_image_tools(
    ws,
    source_path: str,
    output_path: str = "",
    max_width: int = 0,
    max_height: int = 0,
    quality: int = 85,
    overwrite: bool = False,
) -> str:
    """Convert and/or resize project images (PNG/JPG/WebP) safely using Pillow."""
    from PIL import Image

    src = ws.resolve(source_path)
    if not src.is_file():
        raise ValueError(f"Δεν βρέθηκε η εικόνα: {source_path}")
    out = ws.resolve(output_path) if (output_path and output_path.strip()) else src.with_name(f"{src.stem}_edited{src.suffix}")
    _guard_output_write(ws, out, overwrite)
    q = max(10, min(100, int(quality or 85)))
    with Image.open(src) as img:
        orig_size = img.size
        if max_width > 0 or max_height > 0:
            mw = max_width if max_width > 0 else img.width
            mh = max_height if max_height > 0 else img.height
            img.thumbnail((mw, mh))
        suffix = out.suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
            raise ValueError(f"Μη υποστηριζόμενος τύπος εικόνας '{suffix}'. Χρησιμοποίησε .png, .jpg ή .webp.")
        out.parent.mkdir(parents=True, exist_ok=True)
        if suffix in (".jpg", ".jpeg"):
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")
            img.save(out, format="JPEG", quality=q, optimize=True)
        elif suffix == ".webp":
            img.save(out, format="WEBP", quality=q)
        else:
            img.save(out, format="PNG", optimize=True)
        new_size = img.size
    return f"ΕΓΙΝΕ: {ws.relative(src)} ({orig_size[0]}x{orig_size[1]}) → {ws.relative(out)} ({new_size[0]}x{new_size[1]}, {out.stat().st_size} bytes)"


def tool_zip_manager(
    ws,
    action: str = "list",
    archive_path: str = "",
    paths: list[str] | None = None,
    destination: str = "",
    overwrite: bool = False,
) -> str:
    """Safely list, create, or extract ZIP archives inside the project (with Zip-Slip guard)."""
    import zipfile

    act = str(action or "list").strip().lower()
    if not archive_path or not archive_path.strip():
        raise ValueError("Δώσε διαδρομή αρχείου .zip στο archive_path.")
    arc = ws.resolve(archive_path)

    if act == "list":
        if not arc.is_file():
            raise ValueError(f"Δεν βρέθηκε το αρχείο ZIP: {archive_path}")
        with zipfile.ZipFile(arc, "r") as zf:
            infos = zf.infolist()
            lines = [f"ZIP {ws.relative(arc)} ({len(infos)} στοιχεία):"]
            for info in infos[:100]:
                lines.append(f"- {info.filename} ({info.file_size} bytes)")
            if len(infos) > 100:
                lines.append(f"... (+{len(infos) - 100} ακόμα)")
        return "\n".join(lines)

    if act == "create":
        items = [str(p).strip() for p in (paths or []) if str(p).strip()]
        if not items:
            raise ValueError("Δώσε paths για συμπίεση στο .zip.")
        _guard_output_write(ws, arc, overwrite)
        arc.parent.mkdir(parents=True, exist_ok=True)
        arc_resolved = arc.resolve()
        skip_parts = {".git", ".agent_backups", ".venv", "venv", "__pycache__", "node_modules"}
        count = 0
        with zipfile.ZipFile(arc, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in items[:500]:
                if count >= 2000:
                    break
                p = ws.resolve(item)
                if p.is_file():
                    if p.resolve() == arc_resolved or p.name == ".env" or any(sp in p.parts for sp in skip_parts):
                        continue
                    zf.write(p, arcname=ws.relative(p).replace("\\", "/"))
                    count += 1
                elif p.is_dir():
                    for child in p.rglob("*"):
                        if count >= 2000:
                            break
                        if not child.is_file():
                            continue
                        if child.resolve() == arc_resolved or child.name == ".env" or any(sp in child.parts for sp in skip_parts):
                            continue
                        zf.write(child, arcname=ws.relative(child).replace("\\", "/"))
                        count += 1
        return f"ΕΓΙΝΕ: δημιουργήθηκε το {ws.relative(arc)} ({count} αρχεία, {arc.stat().st_size} bytes)"

    if act == "extract":
        if not arc.is_file():
            raise ValueError(f"Δεν βρέθηκε το αρχείο ZIP: {archive_path}")
        dest_root = ws.resolve(destination or ".")
        dest_root.mkdir(parents=True, exist_ok=True)
        total_bytes = 0
        extracted = 0
        with zipfile.ZipFile(arc, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.file_size > 32 * 1024 * 1024 or total_bytes + info.file_size > 64 * 1024 * 1024:
                    raise ValueError("Το ZIP ξεπερνά το όριο ασφαλείας αποσυμπίεσης (64 MiB).")
                # Strict Zip-Slip guard using Path.relative_to (prevents out -> out2 prefix bypass!)
                target = ws.resolve(str((dest_root / info.filename).resolve()))
                try:
                    target.relative_to(dest_root.resolve())
                except ValueError as exc:
                    raise PermissionError(f"Μη ασφαλής διαδρομή μέσα στο ZIP: {info.filename}") from exc
                _guard_output_write(ws, target, overwrite)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src_f:
                    data = src_f.read(32 * 1024 * 1024 + 1)
                    if len(data) > 32 * 1024 * 1024 or total_bytes + len(data) > 64 * 1024 * 1024:
                        raise ValueError("Το ZIP ξεπερνά το όριο ασφαλείας αποσυμπίεσης (64 MiB).")
                    target.write_bytes(data)
                    total_bytes += len(data)
                    extracted += 1
        return f"ΕΓΙΝΕ: αποσυμπιέστηκαν {extracted} αρχεία ({total_bytes} bytes) στο {ws.relative(dest_root)}"

    raise ValueError("Μη έγκυρη ενέργεια zip_manager (list, create ή extract).")


BUILTIN_TOOL_PLUGINS: dict[str, dict] = {
    "system-diagnostics": {
        "id": "system-diagnostics",
        "name": "💻 Διαγνωστικά Συστήματος & Δίσκου",
        "version": "1.0",
        "kind": "tool",
        "readonly": True,
        "default_enabled": True,
        "description": "Ελέγχει ελεύθερο χώρο δίσκου, έκδοση Windows/Python, πυρήνες CPU και μέγεθος project χωρίς εκτέλεση shell.",
        "tool_name": "system_diagnostics",
        "spec": {
            "type": "function",
            "function": {
                "name": "system_diagnostics",
                "description": "Inspect local system & disk diagnostics (OS, Python, CPU cores, free disk space in GB, and project file statistics) safely without running shell commands.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "check": {
                            "type": "string",
                            "enum": ["overview", "files"],
                            "description": "'overview' for OS/CPU/disk space, or 'files' to also count project files and total MB.",
                        }
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        "handler": tool_system_diagnostics,
    },
    "smart-calculator": {
        "id": "smart-calculator",
        "name": "🧮 Ακριβής Υπολογιστής & Ανάλυση ΦΠΑ",
        "version": "1.0",
        "kind": "tool",
        "readonly": True,
        "default_enabled": True,
        "description": "Εκτελεί μαθηματικούς και οικονομικούς υπολογισμούς ακριβείας, αποφορολόγηση και υπολογισμό ΦΠΑ (π.χ. 24%).",
        "tool_name": "calculate_expression",
        "spec": {
            "type": "function",
            "function": {
                "name": "calculate_expression",
                "description": "Safely evaluate mathematical/financial expressions with exact precision and optional VAT (ΦΠΑ) net/gross breakdown.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string", "description": "Math expression, e.g. '(1250.50 * 12) - 3400' or 'sqrt(144) * 25'"},
                        "vat_rate": {"type": "number", "description": "Optional VAT percentage (e.g. 24 for 24% ΦΠΑ) to calculate net/gross breakdown."},
                    },
                    "required": ["expression"],
                    "additionalProperties": False,
                },
            },
        },
        "handler": tool_calculate_expression,
    },
    "data-converter": {
        "id": "data-converter",
        "name": "🔄 Μετατροπέας Δεδομένων (CSV ↔ JSON ↔ Markdown)",
        "version": "1.0",
        "kind": "tool",
        "readonly": False,
        "default_enabled": True,
        "description": "Μετατρέπει αυτόματα αρχεία CSV και JSON σε πίνακες Markdown, JSON ή CSV.",
        "tool_name": "convert_data_file",
        "spec": {
            "type": "function",
            "function": {
                "name": "convert_data_file",
                "description": "Convert a project CSV or JSON data file into a Markdown table, JSON, or CSV, optionally saving to output_path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_path": {"type": "string"},
                        "target_format": {"type": "string", "enum": ["markdown", "json", "csv"]},
                        "output_path": {"type": "string"},
                        "encoding": {"type": "string", "enum": ["auto", "utf-8", "utf-8-sig", "utf-16", "utf-32", "cp1253", "cp1252"]},
                        "expected_sha256": {"type": "string"},
                    },
                    "required": ["source_path"],
                    "additionalProperties": False,
                },
            },
        },
        "handler": tool_convert_data_file,
    },
    "pdf-toolkit": {
        "id": "pdf-toolkit",
        "name": "📄 Εργαλεία PDF (Ένωση & Διαχωρισμός)",
        "version": "1.0",
        "kind": "tool",
        "readonly": False,
        "default_enabled": True,
        "description": "Ενώνει πολλά PDF σε ένα (merge), εξάγει σελίδες (split) και ελέγχει στοιχεία PDF τοπικά.",
        "tool_name": "pdf_toolkit",
        "spec": {
            "type": "function",
            "function": {
                "name": "pdf_toolkit",
                "description": "Merge multiple PDFs, split/extract page ranges (e.g. '1-3,5'), or inspect PDF metadata locally inside the project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["info", "merge", "split"]},
                        "paths": {"type": "array", "items": {"type": "string"}},
                        "output_path": {"type": "string"},
                        "pages": {"type": "string", "description": "Page range for split, e.g. '1-3,5'"},
                        "overwrite": {"type": "boolean"},
                    },
                    "required": ["action", "paths"],
                    "additionalProperties": False,
                },
            },
        },
        "handler": tool_pdf_toolkit,
    },
    "image-tools": {
        "id": "image-tools",
        "name": "🖼️ Επεξεργασία & Μετατροπή Εικόνων",
        "version": "1.0",
        "kind": "tool",
        "readonly": False,
        "default_enabled": True,
        "description": "Αλλάζει μέγεθος, συμπιέζει και μετατρέπει εικόνες PNG ↔ JPG ↔ WebP τοπικά.",
        "tool_name": "image_tools",
        "spec": {
            "type": "function",
            "function": {
                "name": "image_tools",
                "description": "Resize, compress, or convert project images between PNG, JPEG, and WebP locally.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_path": {"type": "string"},
                        "output_path": {"type": "string"},
                        "max_width": {"type": "integer"},
                        "max_height": {"type": "integer"},
                        "quality": {"type": "integer"},
                        "overwrite": {"type": "boolean"},
                    },
                    "required": ["source_path"],
                    "additionalProperties": False,
                },
            },
        },
        "handler": tool_image_tools,
    },
    "zip-manager": {
        "id": "zip-manager",
        "name": "📦 Διαχειριστής ZIP (Συμπίεση & Αποσυμπίεση)",
        "version": "1.0",
        "kind": "tool",
        "readonly": False,
        "default_enabled": True,
        "description": "Προβάλλει, δημιουργεί και αποσυμπιέζει αρχεία .zip με προστασία Zip-Slip.",
        "tool_name": "zip_manager",
        "spec": {
            "type": "function",
            "function": {
                "name": "zip_manager",
                "description": "Safely list, create, or extract .zip archives inside the workspace with Zip-Slip and size limits.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "create", "extract"]},
                        "archive_path": {"type": "string"},
                        "paths": {"type": "array", "items": {"type": "string"}},
                        "destination": {"type": "string"},
                        "overwrite": {"type": "boolean"},
                    },
                    "required": ["action", "archive_path"],
                    "additionalProperties": False,
                },
            },
        },
        "handler": tool_zip_manager,
    },
}


class ToolPlugins:
    """Manages enabled state of native Python Tool Plugins."""

    def __init__(self, path: Path | None = None):
        from . import memory
        self.path = Path(path) if path is not None else (Path(memory.DIR) / "tool_plugins.json")

    def _load_state(self) -> dict[str, bool]:
        try:
            data = load_json(self.path, dict)
            if isinstance(data, dict):
                return {str(k): bool(v) for k, v in data.items()}
        except Exception:
            pass
        return {}

    def is_enabled(self, plugin_id: str) -> bool:
        plugin = BUILTIN_TOOL_PLUGINS.get(plugin_id)
        if not plugin:
            return False
        state = self._load_state()
        return state.get(plugin_id, bool(plugin.get("default_enabled", True)))

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict:
        if plugin_id not in BUILTIN_TOOL_PLUGINS:
            raise ValueError(f"Άγνωστο πρόσθετο εργαλείου '{plugin_id}'.")
        with LOCK:
            state = self._load_state()
            state[plugin_id] = bool(enabled)
            save_json(self.path, state)
        return {"ok": True, "id": plugin_id, "enabled": bool(enabled), "catalog": self.catalog()}

    def catalog(self) -> list[dict]:
        state = self._load_state()
        rows = []
        for pid, p in BUILTIN_TOOL_PLUGINS.items():
            enabled = state.get(pid, bool(p.get("default_enabled", True)))
            rows.append({
                "id": pid,
                "name": p["name"],
                "version": p["version"],
                "kind": "tool",
                "tool_name": p["tool_name"],
                "description": p["description"],
                "readonly": bool(p["readonly"]),
                "enabled": bool(enabled),
            })
        return rows

    def active_tool_names(self, mode: str) -> list[str]:
        state = self._load_state()
        names = []
        for pid, p in BUILTIN_TOOL_PLUGINS.items():
            if not state.get(pid, bool(p.get("default_enabled", True))):
                continue
            if mode == "ask" and not p.get("readonly"):
                continue
            if mode == "architect" and not p.get("readonly"):
                continue
            names.append(p["tool_name"])
        return names
