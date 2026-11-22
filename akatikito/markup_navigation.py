"""Bounded CSS, XML/XHTML and official framework template syntax navigation."""
from __future__ import annotations

from bisect import bisect_right
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from xml.parsers import expat

XML_SUFFIXES = {".xml", ".xhtml", ".xht", ".svg", ".xsl", ".xslt", ".xsd", ".wsdl"}
FRAMEWORK_SUFFIXES = {".vue": "vue", ".svelte": "svelte", ".angular": "angular", ".ng": "angular"}
MAX_SECONDS = 3.0
FRAMEWORK_SECONDS = 6
BRIDGE = Path(__file__).with_name("template_parsers") / "parse.mjs"
MAX_OUTPUT = 2 * 1024 * 1024


class Collector:
    def __init__(self, text, backend, limits):
        self.raw = text.encode("utf-8")
        self.lines = [0] + [i + 1 for i, b in enumerate(self.raw) if b == 10]
        self.limits = limits
        self.deadline = time.monotonic() + MAX_SECONDS
        self.count = 0
        self.result = {"symbols": [], "notice": "", "backend": backend, "cacheable": True, "refs": [], "injections": []}
        self.notices = []

    def note(self, message, transient=False):
        if len(self.notices) < 16:
            self.notices.append(str(message)[:240])
        if transient:
            self.result["cacheable"] = False

    def budget(self):
        self.count += 1
        if self.count > self.limits["nodes"] or len(self.result["symbols"]) >= self.limits["symbols"] or time.monotonic() > self.deadline:
            self.note("Μερική δομή: όριο χρόνου/κόμβων/συμβόλων. Χρησιμοποίησε start/end.", True)
            return False
        return True

    def row(self, a, b, name, header, kind):
        if not 0 <= a < b <= len(self.raw):
            return
        if not self.budget():
            return
        self.result["symbols"].append(
            {
                "first": bisect_right(self.lines, a),
                "last": bisect_right(self.lines, b - 1),
                "start": a,
                "end": b,
                "name": name[:180],
                "header": header[:256],
                "kind": kind,
            }
        )

    def inject(self, a, b, label, language, symbols, mapper=None):
        if len(self.result["injections"]) >= 128 or not self.budget():
            self.note("Όριο embedded regions.", True)
            return
        code = mapper.data if mapper else self.raw[a:b]
        analysis = symbols(code.decode("utf-8"), language)
        self.result["cacheable"] &= analysis["cacheable"]
        if analysis["notice"]:
            self.note(label + ": " + analysis["notice"])
        self.result["injections"].append(
            {"name": label, "language": language, "first": bisect_right(self.lines, a), "last": bisect_right(self.lines, max(a, b - 1))}
        )
        for child in analysis["symbols"]:
            x = a + (mapper.offset(child["start"]) if mapper else child["start"])
            y = a + (mapper.offset(child["end"], True) if mapper else child["end"])
            if a <= x < y <= b:
                self.row(x, y, label + "." + child["name"], child["header"], child["kind"])
        self.result["refs"] = sorted(set(self.result["refs"]) | set(analysis["refs"]))

    def finish(self):
        self.result["symbols"].sort(key=lambda r: (r["first"], r["last"], r["name"]))
        self.result["notice"] = "\n".join(dict.fromkeys(self.notices))[:2400]
        return self.result


def _tree(c, parse, name):
    if len(c.raw) > c.limits["bytes"]:
        c.note("Η δομή δεν αναλύθηκε: όριο μεγέθους 2 MiB. Χρησιμοποίησε start/end.")
        return None
    try:
        tree = parse(c.raw, name)
    except (ImportError, OSError, ValueError, AttributeError, TypeError) as exc:
        c.result["missing_parser"] = True
        c.note("Λείπει ή απέτυχε ο τοπικός parser " + name + " (" + type(exc).__name__ + "). Έλεγξε requirements-features.txt· χρησιμοποίησε start/end.", True)
        return None
    if tree is None:
        c.note("Χρονικό όριο parser " + name + ". Χρησιμοποίησε start/end.", True)
        return None
    if tree.root_node.has_error:
        c.note("Μερική δομή " + name + ": σφάλματα ή ατελής σύνταξη.")
    return tree


def css(text, parse, limits):
    c = Collector(text, "tree-sitter css", limits)
    tree = _tree(c, parse, "css")
    if tree is None:
        return c.finish()
    if tree.root_node.has_error:
        try:
            data = _framework_request(text, "css")
            if not data["notices"] and data["cacheable"]:
                c.notices = []
                c.deadline = time.monotonic() + MAX_SECONDS
                c.result["backend"] = "tree-sitter css + CSS Tree"
                for row in data["rows"]:
                    c.row(row["start"], row["end"], row["name"], row["header"], "css")
                return c.finish()
            if not data["cacheable"]:
                c.note("CSS Tree parser μη διαθέσιμος ή όριο ανάλυσης.", True)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            c.note("CSS Tree fallback μη διαθέσιμος· παραμένει η μερική native δομή.", True)
        c.deadline = time.monotonic() + MAX_SECONDS
    pending = [tree.root_node]
    while pending:
        if not c.budget():
            break
        node = pending.pop()
        kind = node.type
        if not node.has_error and not node.is_missing and (kind == "rule_set" or kind.endswith("_statement") or kind == "at_rule" or kind == "keyframe_block"):
            block = next((x for x in node.named_children if x.type in ("block", "keyframe_block_list")), None)
            end = block.start_byte if block else node.end_byte
            header = " ".join(c.raw[node.start_byte : min(end, node.start_byte + 1024)].decode("utf-8").split())[:180]
            c.row(node.start_byte, node.end_byte, header, header, "css")
        if node.type not in ("comment", "string_value"):
            if len(pending) + node.named_child_count > limits["nodes"]:
                c.note("Όριο κόμβων CSS.", True)
                break
            pending.extend(reversed(node.named_children))
    return c.finish()


class XMLMapping:
    """XML character data/CDATA/entity tokens to original UTF-8 bytes, no DTD expansion."""

    def __init__(self, raw, nodes, origin):
        self.parts = []
        parts = []
        total = 0
        pending = list(reversed(nodes))
        steps = 0
        while pending:
            steps += 1
            if steps > 100000:
                raise ValueError("Όριο XML character-data nodes.")
            node = pending.pop()
            typ = node.type
            a, b = node.start_byte, node.end_byte
            if typ == "CDSect":
                pending.extend(reversed(node.named_children))
                continue
            if typ in ("CDStart",):
                continue
            value = raw[a:b]
            if typ in ("Reference", "EntityRef", "CharRef"):
                entity = value.decode("ascii")
                value = {"&lt;": b"<", "&gt;": b">", "&amp;": b"&", "&quot;": b'"', "&apos;": b"'"}.get(entity)
                if value is None:
                    digits = entity[2:-1]
                    base = 10
                    if digits.startswith("x"):
                        base = 16
                        digits = digits[1:]
                    if not entity.startswith("&#") or len(digits.lstrip("0")) > 8:
                        raise ValueError("Μη υποστηριζόμενη XML entity.")
                    value = chr(int(digits.lstrip("0") or "0", base)).encode("utf-8")
            elif typ not in ("CharData", "CData"):
                raise ValueError("Μη υποστηριζόμενο περιεχόμενο XML script/style.")
            if value:
                self.parts.append((total, total + len(value), a - origin, b - origin, value == raw[a:b]))
                parts.append(value)
                total += len(value)
        self.data = b"".join(parts)
        self.ends = [x[1] for x in self.parts]

    def offset(self, value, end=False):
        from bisect import bisect_left

        if not self.parts:
            return 0
        if value <= 0:
            return self.parts[0][2]
        if value >= len(self.data):
            return self.parts[-1][3]
        p = self.parts[(bisect_left if end else bisect_right)(self.ends, value)]
        return p[2] + value - p[0] if p[4] else (p[3] if end else p[2])


class EntityExpansionDisabled(Exception):
    pass


class XMLValidationLimit(Exception):
    pass


def _validate_xml(raw, limits, deadline):
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def blocked(*args):
        raise EntityExpansionDisabled("Δηλώσεις/επεκτάσεις entities δεν εκτελούνται.")

    elements = {}

    def element(name, attrs):
        if len(elements) >= limits["nodes"] or time.monotonic() > deadline:
            raise XMLValidationLimit("Όριο XML validation.")
        elements[parser.CurrentByteIndex] = (name, attrs)

    parser.StartElementHandler = element
    parser.EntityDeclHandler = blocked
    parser.ExternalEntityRefHandler = blocked
    parser.Parse(raw, True)
    return elements


def xml(text, suffix, parse, symbols, limits):
    c = Collector(text, "tree-sitter xml + Expat validation", limits)
    tree = _tree(c, parse, "xml")
    if tree is None:
        return c.finish()
    try:
        elements = _validate_xml(c.raw, limits, c.deadline)
    except (expat.ExpatError, EntityExpansionDisabled) as exc:
        c.note("Δεν δίνεται πλήρης δομή XML: " + str(exc))
        return c.finish()
    except XMLValidationLimit as exc:
        c.note(str(exc), True)
        return c.finish()
    pending = [tree.root_node]
    counts = {}
    while pending:
        if not c.budget():
            break
        node = pending.pop()
        if node.type == "element":
            start = next((n for n in node.named_children if n.type in ("STag", "EmptyElemTag")), None)
            if start is None or node.has_error:
                continue
            name_node = next((n for n in start.named_children if n.type == "Name"), None)
            if name_node is None:
                continue
            name = c.raw[name_node.start_byte : name_node.end_byte].decode()
            resolved, attrs = elements.get(start.start_byte, ("", {}))
            counts[name] = counts.get(name, 0) + 1
            label = name + "[" + str(counts[name]) + "]"
            c.row(node.start_byte, node.end_byte, label, "<" + name + ">", "xml")
            namespace, _, local = resolved.rpartition("}")
            local = local or resolved
            is_xhtml = namespace == "http://www.w3.org/1999/xhtml"
            if is_xhtml and local in ("script", "style"):
                from .html_navigation import _script_type

                language = ".css" if local == "style" and attrs.get("type", "text/css") == "text/css" else (_script_type(attrs)[0] if local == "script" else None)
                content = next((n for n in node.named_children if n.type == "content"), None)
                if content and language and "src" not in attrs:
                    try:
                        mapper = XMLMapping(c.raw, content.named_children, content.start_byte)
                    except (ValueError, OverflowError) as exc:
                        c.note(str(exc))
                        continue
                    c.inject(content.start_byte, content.end_byte, label, language, symbols, mapper)
                continue
            children = [n for n in node.named_children if n.type == "content"]
            pending.extend(reversed(children))
        elif node.type in ("document", "content"):
            if len(pending) + node.named_child_count > limits["nodes"]:
                c.note("Όριο κόμβων XML.", True)
                break
            pending.extend(n for n in reversed(node.named_children) if n.type not in ("prolog", "Comment", "PI", "CDSect"))
    return c.finish()


def _framework_request(text, framework_name):
    node = shutil.which("node")
    if not node:
        raise OSError("Δεν βρέθηκε Node.js για τους τοπικούς framework parsers.")
    env = os.environ.copy()
    for key in ("NODE_OPTIONS", "NODE_PATH"):
        env.pop(key, None)
    result = subprocess.run(
        [node, "--max-old-space-size=256", str(BRIDGE)],
        input=json.dumps({"source": text, "framework": framework_name}, ensure_ascii=False),
        cwd=BRIDGE.parent,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=FRAMEWORK_SECONDS,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError("Ο framework parser απέτυχε (exit " + str(result.returncode) + ").")
    if len(result.stdout.encode("utf-8")) > MAX_OUTPUT:
        raise ValueError("Όριο αποτελέσματος framework parser.")
    return json.loads(result.stdout)


def framework(text, name, parse, symbols, limits):
    c = Collector(text, "official " + name + " parser + embedded parsers", limits)
    if len(c.raw) > limits["bytes"]:
        c.note("Όριο μεγέθους template 2 MiB. Χρησιμοποίησε start/end.")
        return c.finish()
    try:
        data = _framework_request(text, name)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        c.result["missing_parser"] = True
        c.note("Ο τοπικός " + name + " parser δεν είναι διαθέσιμος (" + type(exc).__name__ + "). Έλεγξε template_parsers/package-lock.json και Node.js. Χρησιμοποίησε start/end.", True)
        return c.finish()
    c.deadline = time.monotonic() + MAX_SECONDS
    c.result["cacheable"] = bool(data["cacheable"])
    for message in data["notices"]:
        c.note(name + ": " + message)
    for row in data["rows"]:
        if not c.budget():
            break
        c.row(row["start"], row["end"], row["name"], row["header"], row["kind"])
    for r in data["regions"]:
        if not c.budget():
            break
        if r["src"] is not None:
            c.note(r["name"] + ": εξωτερικό src· δεν φορτώνεται.")
            continue
        a, b = r["start"], r["end"]
        if not 0 <= a <= b <= len(c.raw):
            c.note("Μη έγκυρη θέση embedded region.", True)
            continue
        c.row(a, b, r["name"], r["name"] + " " + r["language"], "css" if r["language"] == ".css" else "script")
        c.inject(a, b, r["name"], r["language"], symbols)
    return c.finish()
