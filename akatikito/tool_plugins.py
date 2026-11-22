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
