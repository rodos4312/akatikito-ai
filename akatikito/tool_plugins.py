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


def _check_decimal(v: Decimal) -> Decimal:
    if not v.is_finite() or (v and abs(v.adjusted()) > 1000):
        raise ValueError("Ο αριθμός ξεπερνά το όριο.")
    return v


def _decimal_pow(base: Decimal, exp: Decimal) -> Decimal:
    if abs(exp) > 1000:
        raise ValueError("Πολύ μεγάλος εκθέτης.")
    if exp == exp.to_integral_value():
        return _check_decimal(base ** int(exp))
    out = float(base) ** float(exp)
    if not math.isfinite(out):
        raise ValueError("Μη έγκυρο αριθμητικό αποτέλεσμα.")
    return _check_decimal(Decimal(str(out)))


_BIN_OPS = {
    ast.Add: lambda a, b: _check_decimal(a + b),
    ast.Sub: lambda a, b: _check_decimal(a - b),
    ast.Mult: lambda a, b: _check_decimal(a * b),
    ast.Div: lambda a, b: _check_decimal(a / b),
    ast.FloorDiv: lambda a, b: _check_decimal(a // b),
    ast.Mod: lambda a, b: _check_decimal(a % b),
    ast.Pow: _decimal_pow,
}


def _eval_round(args: list[Decimal]) -> Decimal:
    places = int(args[1]) if len(args) == 2 else 0
    if abs(places) > 30:
        raise ValueError("Πολλά δεκαδικά ψηφία.")
    return _check_decimal(args[0].quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def _eval_trig_log(fname: str, val: Decimal) -> Decimal:
    out = getattr(math, fname)(float(val))
    if not math.isfinite(out):
        raise ValueError("Μη έγκυρο αριθμητικό αποτέλεσμα.")
    return _check_decimal(Decimal(str(out)))


def _eval_math_func(fname: str, args: list[Decimal]) -> Decimal:
    n = len(args)
    if n == 1:
        if fname == "abs":
            return abs(args[0])
        if fname == "sqrt":
            return _check_decimal(args[0].sqrt())
        if fname in ("ceil", "floor"):
            return Decimal(math.ceil(args[0]) if fname == "ceil" else math.floor(args[0]))
        if fname in ("log", "log10", "sin", "cos", "tan"):
            return _eval_trig_log(fname, args[0])
    if fname == "round" and n in (1, 2):
        return _eval_round(args)
    if fname == "pow" and n == 2:
        return _decimal_pow(args[0], args[1])
    if 1 <= n <= 500:
        if fname == "min":
            return min(args)
        if fname == "max":
            return max(args)
        if fname == "sum":
            return _check_decimal(sum(args, Decimal(0)))
    raise ValueError("Μη επιτρεπτή μαθηματική έκφραση.")


def _eval_call_node(node: ast.Call, eval_fn, depth: int) -> Decimal:
    if not isinstance(node.func, ast.Name):
        raise ValueError("Μη επιτρεπτή μαθηματική έκφραση.")
    if node.keywords:
        raise ValueError("Δεν επιτρέπονται ονομαστικά ορίσματα στη μαθηματική έκφραση.")
    fname = node.func.id
    if fname in ("sum", "min", "max") and len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple)):
        seq = [eval_fn(elt, depth + 1) for elt in node.args[0].elts]
        if not seq or len(seq) > 500:
            raise ValueError("Δώσε 1–500 αριθμούς.")
        return _eval_math_func(fname, seq)
    args = [eval_fn(a, depth + 1) for a in node.args]
    return _eval_math_func(fname, args)


def _safe_eval_decimal(expr: str) -> Decimal:
    """Evaluate mathematical expression strictly via AST whitelist using 50-digit Decimal arithmetic."""
    src = expr.strip()
    if not src or len(src) > 400:
        raise ValueError("Δώσε μαθηματική έκφραση έως 400 χαρακτήρες.")
    tree = ast.parse(src, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 200:
        raise ValueError("Πολύ σύνθετη μαθηματική έκφραση.")

    def _eval(node, depth: int = 0) -> Decimal:
        if depth > 25:
            raise ValueError("Πολύ βαθιά μαθηματική έκφραση.")
        if isinstance(node, ast.Expression):
            return _eval(node.body, depth)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            seg = ast.get_source_segment(src, node)
            raw = seg.replace("_", "") if seg else str(node.value)
            return _check_decimal(Decimal(raw))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            val = _eval(node.operand, depth + 1)
            return val if isinstance(node.op, ast.UAdd) else -val
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](_eval(node.left, depth + 1), _eval(node.right, depth + 1))
        if isinstance(node, ast.Name) and node.id in _DEC_CONSTANTS:
            return _DEC_CONSTANTS[node.id]
        if isinstance(node, ast.Call):
            return _eval_call_node(node, _eval, depth)
        raise ValueError("Μη επιτρεπτή μαθηματική έκφραση.")

    try:
        with localcontext() as ctx:
            ctx.prec = 50
            return _check_decimal(_eval(tree))
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


def _decode_data_bytes(raw_bytes: bytes, encoding: str) -> str:
    enc = str(encoding or "auto").strip().lower()
    if enc == "auto":
        if raw_bytes.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            enc = "utf-32"
        elif raw_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
            enc = "utf-16"
        else:
            enc = "utf-8-sig"
    if enc not in ("utf-8", "utf-8-sig", "utf-16", "utf-32", "cp1253", "cp1252"):
        raise ValueError("Μη επιτρεπτή κωδικοποίηση. Υποστηρίζονται: auto, utf-8, utf-8-sig, utf-16, utf-32, cp1253, cp1252.")
    try:
        return raw_bytes.decode(enc)
    except UnicodeError as exc:
        raise ValueError("Η κωδικοποίηση δεν ταιριάζει. Επίλεξε ρητά encoding· δεν αντικαταστάθηκαν χαρακτήρες.") from exc


def _parse_csv_rows(raw_text: str) -> tuple[list[str], list[dict]] | None:
    reader = csv.reader(io.StringIO(raw_text))
    raw_headers = next(reader, [])
    if not raw_headers:
        return None
    headers = [str(h).strip() for h in raw_headers]
    if any(not h for h in headers) or len(set(headers)) != len(headers) or len(headers) > 100:
        raise ValueError("Χρειάζονται μη κενές και μοναδικές επικεφαλίδες CSV (έως 100 στήλες).")
    rows: list[dict] = []
    for row in reader:
        if not row:
            continue
        if len(row) != len(headers):
            raise ValueError("Ασυνεπές πλήθος στηλών CSV· δεν χάθηκαν κελιά.")
        rows.append(dict(zip(headers, row)))
        if len(rows) > 10000:
            raise ValueError("Όριο 10.000 εγγραφών CSV.")
    return headers, rows


def _parse_json_rows(raw_text: str) -> tuple[list[str], list[dict]]:
    parsed = json.loads(raw_text)
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list) or len(parsed) > 10000 or not all(isinstance(x, dict) for x in parsed):
        raise ValueError("Το JSON πρέπει να περιέχει λίστα αντικειμένων έως 10.000 εγγραφές.")
    headers = list(dict.fromkeys(str(k) for item in parsed for k in item.keys()))
    if len(headers) > 100:
        raise ValueError("Όριο 100 στηλών JSON.")
    rows = [{str(k): v for k, v in item.items()} for item in parsed]
    return headers, rows


def _render_converted_rows(headers: list[str], rows: list[dict], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2)
    if fmt not in ("csv", "markdown"):
        raise ValueError("Το target_format πρέπει να είναι 'json', 'csv' ή 'markdown'.")
    if any(isinstance(v, (dict, list)) for r in rows for v in r.values()):
        raise ValueError("Τα σύνθετα JSON δεδομένα (λίστες/αντικείμενα) διατηρούνται μόνο σε μορφή JSON.")
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({h: ("" if r.get(h) is None else r.get(h)) for h in headers})
        return buf.getvalue()
    if not headers:
        return "(Χωρίς δεδομένα)"
    esc = lambda v: str("" if v is None else v).replace("|", "\\|").replace("\n", "<br>")
    head_line = "| " + " | ".join(esc(h) for h in headers) + " |"
    sep_line = "| " + " | ".join("---" for _ in headers) + " |"
    body_lines = ["| " + " | ".join(esc(r.get(h, "")) for h in headers) + " |" for r in rows]
    return "\n".join([head_line, sep_line, *body_lines])


def _write_converted_output(ws, output_path: str, fmt: str, rendered: str, expected_sha256: str, row_count: int) -> str:
    from .editing import check_syntax, commit
    from .local_store import checked
    from .tools import EditError, _protect_live_source

    out_target = checked(ws.resolve(output_path))
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
    return f"ΕΓΙΝΕ: μετατράπηκαν {row_count} εγγραφές στο {ws.relative(out_target)}\n\n" + rendered[:4000]


def tool_convert_data_file(
    ws,
    source_path: str,
    target_format: str = "markdown",
    output_path: str = "",
    encoding: str = "auto",
    expected_sha256: str = "",
) -> str:
    from .local_store import checked
    from .tools import _clip_with_archive

    src = checked(ws.resolve(source_path))
    if not src.is_file() or src.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Χρειάζεται υπάρχον αρχείο CSV ή JSON έως 8 MiB.")
    suffix = src.suffix.lower()
    raw_text = _decode_data_bytes(src.read_bytes(), encoding)
    if suffix == ".csv":
        parsed = _parse_csv_rows(raw_text)
        if parsed is None:
            return "Το αρχείο CSV είναι κενό."
        headers, rows = parsed
    elif suffix == ".json":
        headers, rows = _parse_json_rows(raw_text)
    else:
        raise ValueError("Υποστηρίζονται αρχεία εισόδου .csv και .json.")
    fmt = str(target_format or "markdown").strip().lower()
    rendered = _render_converted_rows(headers, rows, fmt)
    if output_path and str(output_path).strip():
        return _write_converted_output(ws, str(output_path).strip(), fmt, rendered, expected_sha256, len(rows))
    return _clip_with_archive(ws, rendered)


from .tool_plugins_media import _guard_output_write, tool_image_tools, tool_pdf_toolkit, tool_zip_manager  # noqa: E402


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
        except (OSError, ValueError):
            return {}
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
