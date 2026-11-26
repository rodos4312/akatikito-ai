"""Offline HTML structure and bounded script injections with original byte mapping."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from html import unescape
from html.entities import html5
import re
import time
from urllib.parse import urlsplit

HTML_SUFFIXES = {".html", ".htm"}
MAX_SECONDS = 2.0
MAX_INJECTIONS = 128
MAX_ATTRIBUTE_BYTES = 65536
MAX_ENTITIES = 4096
OPAQUE = {"textarea", "title", "xmp", "iframe", "noembed", "noframes", "plaintext", "noscript", "style"}
JS_MIMES = {
    "application/ecmascript",
    "application/javascript",
    "application/x-ecmascript",
    "application/x-javascript",
    "text/ecmascript",
    "text/javascript",
    "text/javascript1.0",
    "text/javascript1.1",
    "text/javascript1.2",
    "text/javascript1.3",
    "text/javascript1.4",
    "text/javascript1.5",
    "text/jscript",
    "text/livescript",
    "text/x-ecmascript",
    "text/x-javascript",
}
DEV_TYPES = {
    "text/typescript": ".ts",
    "application/typescript": ".ts",
    "text/ts": ".ts",
    "text/tsx": ".tsx",
    "application/tsx": ".tsx",
    "text/jsx": ".jsx",
    "application/jsx": ".jsx",
    "text/babel": ".jsx",
}
DEV_LANGS = {"ts": ".ts", "typescript": ".ts", "tsx": ".tsx", "jsx": ".jsx"}
EVENTS = set(
    (
        "abort afterprint animationcancel animationend animationiteration animationstart auxclick beforeinput "
        "beforeprint beforetoggle beforeunload blur canplay canplaythrough change click close contextmenu copy cuechange cut "
        "dblclick drag dragend dragenter dragleave dragover dragstart drop durationchange emptied ended error focus focusin "
        "focusout formdata gotpointercapture hashchange input invalid keydown keypress keyup languagechange load loadeddata "
        "loadedmetadata loadstart lostpointercapture message messageerror mousedown mouseenter mouseleave mousemove mouseout "
        "mouseover mouseup offline online pagehide pageshow paste pause play playing pointercancel pointerdown pointerenter "
        "pointerleave pointermove pointerout pointerover pointerup popstate progress ratechange rejectionhandled reset resize "
        "scroll scrollend search seeked seeking select selectionchange selectstart slotchange stalled storage submit suspend "
        "timeupdate toggle touchcancel touchend touchmove touchstart transitioncancel transitionend transitionrun "
        "transitionstart unhandledrejection unload volumechange waiting wheel"
    ).split()
)
NUMERIC = re.compile(r"&#(?:[xX][0-9a-fA-F]+|[0-9]+);?")
MAX_ENTITY_NAME = max(map(len, html5))


def _entity(source, start):
    numeric = NUMERIC.match(source, start)
    if numeric:
        value = numeric.group()
        digits = value[2:].rstrip(";")
        hex_ = digits[:1].lower() == "x"
        digits = (digits[1:] if hex_ else digits).lstrip("0") or "0"
        decoded = "\ufffd" if len(digits) > 8 else unescape("&#" + ("x" if hex_ else "") + digits + ";")
        return numeric.end(), decoded
    end = min(len(source), start + 1 + MAX_ENTITY_NAME)
    for stop in range(end, start + 1, -1):
        key = source[start + 1 : stop]
        if key not in html5:
            continue
        following = source[stop : stop + 1]
        if not key.endswith(";") and following and (following == "=" or (following.isascii() and following.isalnum())):
            return None
        return stop, html5[key]
    return None


class AttributeCode:
    """Piecewise UTF-8 offsets: decoded JS bytes -> original attribute bytes."""

    def __init__(self, raw, deadline=None):
        if len(raw) > MAX_ATTRIBUTE_BYTES:
            raise ValueError("Το attribute ξεπερνά το όριο 64 KiB.")
        source = raw.decode("utf-8")
        parts = []
        self.segments = []
        pos = 0
        byte = 0
        chunk = 0
        chunk_byte = 0
        decoded = 0
        entities = 0
        steps = 0

        def add(data, a, b, identity):
            nonlocal decoded
            if data:
                self.segments.append((decoded, decoded + len(data), a, b, identity))
                parts.append(data)
                decoded += len(data)

        while pos < len(source):
            steps += 1
            if steps % 256 == 0 and deadline is not None and time.monotonic() > deadline:
                raise ValueError("Χρονικό όριο ανάλυσης HTML attribute.")
            entity = _entity(source, pos) if source[pos] == "&" else None
            if entity:
                entities += 1
                if entities > MAX_ENTITIES:
                    raise ValueError("Πάρα πολλά character references στο attribute.")
                stop, value = entity
                add(source[chunk:pos].encode("utf-8"), chunk_byte, byte, True)
                after = byte + len(source[pos:stop].encode("utf-8"))
                add(value.encode("utf-8"), byte, after, False)
                pos = stop
                byte = after
                chunk = pos
                chunk_byte = byte
            else:
                byte += len(source[pos].encode("utf-8"))
                pos += 1
        add(source[chunk:].encode("utf-8"), chunk_byte, byte, True)
        self.raw_length = len(raw)
        self.data = b"".join(parts)
        self.ends = [part[1] for part in self.segments]

    def offset(self, value, end=False):
        if not self.segments or value <= 0:
            return 0
        value = max(0, min(value, len(self.data)))
        if value == len(self.data):
            return self.raw_length
        index = bisect_left(self.ends, value) if end else bisect_right(self.ends, value)
        a, b, x, y, identity = self.segments[index]
        return x + value - a if identity else (y if end else x)


def _value(attribute):
    for child in attribute.named_children:
        if child.type == "attribute_value":
            return child
        if child.type == "quoted_attribute_value":
            return next((part for part in child.named_children if part.type == "attribute_value"), None)
    return None


def _attributes(start, raw, deadline):
    attrs = {}
    nodes = {}
    for child in start.named_children:
        if child.type != "attribute":
            continue
        name = next((n for n in child.named_children if n.type == "attribute_name"), None)
        if name is None:
            continue
        key = raw[name.start_byte : name.end_byte].decode("utf-8").lower()
        if key in attrs:
            continue
        value = _value(child)
        if value is None:
            attrs[key] = ""
            nodes[key] = (child, None)
        elif value.end_byte - value.start_byte > MAX_ATTRIBUTE_BYTES:
            attrs[key] = None
            nodes[key] = (child, value)
        else:
            attrs[key] = AttributeCode(raw[value.start_byte : value.end_byte], deadline).data.decode("utf-8")
            nodes[key] = (child, value)
    return attrs, nodes


def _script_type(attrs):
    if "type" in attrs:
        value = attrs["type"]
        if value is None:
            return None, "unknown"
        if value == "" or value.strip(" \t\n\r\f").lower() in JS_MIMES:
            kind = ".js"
            mode = "classic"
        elif value.lower() == "module":
            kind = ".js"
            mode = "module"
        elif value.lower() in DEV_TYPES:
            return DEV_TYPES[value.lower()], "developer"
        else:
            return None, "data"
    else:
        legacy = attrs.get("language", "")
        if legacy is None or (legacy and ("text/" + legacy).lower() not in JS_MIMES):
            return None, "data"
        kind = ".js"
        mode = "classic"
    if (attrs.get("lang") or "").lower() in DEV_LANGS:
        return DEV_LANGS[attrs["lang"].lower()], "developer"
    return kind, mode


def _local_src(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 33 for c in value) or "\\" in value or "%" in value:
        return None
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if url.scheme or url.netloc or not url.path or url.path.startswith(("/", "?", "#")):
        return None
    return "html-script:" + url.path


class _HtmlAnalyzer:
    """Bounded HTML tree walker with embedded JS/CSS symbol injection."""

    def __init__(self, raw, script_symbols, limits):
        self.raw = raw
        self.script_symbols = script_symbols
        self.limits = limits
        self.deadline = time.monotonic() + MAX_SECONDS
        self.line_starts = [0] + [i + 1 for i, c in enumerate(raw) if c == 10]
        self.result = []
        self.refs = set()
        self.injections = []
        self.style_injections = []
        self.notices = []
        self.cacheable = True
        self.counts = Counter()
        self.visited = 0
        self.embedded = 0
        self.base_present = False

    def notice(self, message):
        if len(self.notices) < 16:
            self.notices.append(message[:240])

    def row(self, a, b, name, header, kind):
        return {
            "first": bisect_right(self.line_starts, a),
            "last": bisect_right(self.line_starts, max(a, b - 1)),
            "name": name,
            "header": header,
            "kind": kind,
            "start": a,
            "end": b,
        }

    def inject(self, a, b, label, language, mapper=None):
        if self.embedded >= MAX_INJECTIONS or time.monotonic() > self.deadline:
            self.cacheable = False
            self.notice("Μερική δομή: εξαντλήθηκε το όριο embedded scripts/χρόνου.")
            return False
        self.embedded += 1
        code = mapper.data if mapper else self.raw[a:b]
        analysis = self.script_symbols(code.decode("utf-8"), language)
        self.cacheable = self.cacheable and analysis["cacheable"]
        bucket = self.style_injections if language == ".css" else self.injections
        bucket.append(
            {"name": label, "language": language, "first": bisect_right(self.line_starts, a), "last": bisect_right(self.line_starts, max(a, b - 1))}
        )
        if analysis["notice"]:
            self.notice(label + ": " + analysis["notice"])
        for child in analysis["symbols"]:
            if len(self.result) >= self.limits["symbols"]:
                self.cacheable = False
                self.notice("Μερική δομή: όριο συμβόλων HTML/scripts.")
                break
            x = a + (mapper.offset(child["start"]) if mapper else child["start"])
            y = a + (mapper.offset(child["end"], True) if mapper else child["end"])
            if a <= x < y <= b:
                self.result.append(self.row(x, y, label + "." + child["name"], child["header"], child["kind"]))
        self.refs.update(analysis["refs"])
        return not analysis["notice"]

    def _inject_handlers(self, attribute_nodes, closed, label):
        if not closed:
            return
        for key, (attribute, value) in attribute_nodes.items():
            if not key.startswith("on") or key[2:] not in EVENTS or value is None or attribute.has_error:
                continue
            name = label + "." + key
            try:
                mapper = AttributeCode(self.raw[value.start_byte : value.end_byte], self.deadline)
            except ValueError as exc:
                self.notice(name + ": " + str(exc))
                self.cacheable = False
                continue
            if self.inject(value.start_byte, value.end_byte, name, ".js", mapper) and len(self.result) < self.limits["symbols"]:
                self.result.append(self.row(attribute.start_byte, attribute.end_byte, name, key + " (JavaScript)", "handler"))

    def _handle_script(self, node, region, attrs, attrs_known, closed, is_foreign, label, header):
        if not closed or not attrs_known:
            return
        language, mode = _script_type(attrs)
        if region:
            suffix_type = " " + str(attrs.get("type"))[:80] if "type" in attrs else ""
            region["header"] = header + " [" + mode + suffix_type + "]"
        if "src" in attrs:
            reference = _local_src(attrs["src"]) if language else None
            if reference:
                self.refs.add(reference)
            return
        if language is None:
            return
        if is_foreign:
            self.notice(label + ": scripts σε foreign SVG/MathML δεν αναλύθηκαν ως HTML script.")
            return
        body = next((c for c in node.named_children if c.type == "raw_text"), None)
        if body is not None and self.inject(body.start_byte, body.end_byte, label, language):
            region["kind"] = "script"

    def _handle_style(self, node, region, attrs, attrs_known, closed, label):
        if not closed or not attrs_known:
            return
        if attrs.get("type", "text/css") not in ("", "text/css") or attrs.get("lang", "css") != "css":
            self.notice(label + ": μη υποστηριζόμενη γλώσσα style· το περιεχόμενο παραμένει κείμενο.")
            return
        body = next((c for c in node.named_children if c.type == "raw_text"), None)
        if body is not None and self.inject(body.start_byte, body.end_byte, label, ".css") and region:
            region["kind"] = "css"

    def _enqueue_children(self, pending, children, foreign):
        if len(pending) + len(children) > self.limits["nodes"]:
            self.cacheable = False
            self.notice("Μερική δομή HTML: όριο κόμβων· πάρα πολλοί κόμβοι.")
            return False
        pending.extend((child, foreign) for child in reversed(children))
        return True

    def _read_element_attrs(self, start):
        try:
            attrs, attribute_nodes = _attributes(start, self.raw, self.deadline)
            return attrs, attribute_nodes, True
        except ValueError as exc:
            self.notice(str(exc))
            self.cacheable = False
            return {}, {}, False

    def _element_meta(self, node):
        start = node if node.type == "self_closing_tag" else next(
            (c for c in node.named_children if c.type in ("start_tag", "self_closing_tag")), None
        )
        tag_node = next((c for c in start.named_children if c.type == "tag_name"), None) if start is not None else None
        if tag_node is None:
            return None
        tag = self.raw[tag_node.start_byte : tag_node.end_byte].decode("utf-8").lower()
        self.counts[tag] += 1
        attrs, attribute_nodes, attrs_known = self._read_element_attrs(start)
        identity = " ".join((attrs.get("id") or "").split())[:80]
        display_tag = tag[:80] + ("…" if len(tag) > 80 else "")
        label = display_tag + ("#" + identity if identity else "") + "[" + str(self.counts[tag]) + "]"
        header = "<" + display_tag + (' id="' + identity + '"' if identity else "") + ">"
        closed = not node.has_error and not node.is_missing and not start.has_error
        return tag, attrs, attribute_nodes, attrs_known, label, header, closed

    def _visit_element(self, node, foreign, pending):
        meta = self._element_meta(node)
        if meta is None:
            return True
        tag, attrs, attribute_nodes, attrs_known, label, header, closed = meta
        if tag == "base" and ("href" in attrs or not attrs_known):
            self.base_present = True
        region = self.row(node.start_byte, node.end_byte, label, header, "html") if closed else None
        if region:
            self.result.append(region)
        is_foreign = foreign or tag in ("svg", "math")
        self._inject_handlers(attribute_nodes, closed, label)
        if tag == "script":
            self._handle_script(node, region, attrs, attrs_known, closed, is_foreign, label, header)
            return True
        if tag == "style":
            self._handle_style(node, region, attrs, attrs_known, closed, label)
            return True
        if tag in OPAQUE:
            return True
        children = [c for c in node.named_children if c.type not in ("start_tag", "end_tag", "self_closing_tag")]
        return self._enqueue_children(pending, children, is_foreign)

    def run(self, root):
        if root.has_error:
            self.notice("Μερική δομή HTML: υπάρχουν σφάλματα ή ατελείς ετικέτες.")
        pending = [(root, False)]
        while pending:
            if (self.visited >= self.limits["nodes"] or len(self.result) >= self.limits["symbols"]
                    or len(self.refs) >= self.limits["symbols"] or time.monotonic() > self.deadline):
                self.cacheable = False
                self.notice("Μερική δομή HTML: όριο χρόνου/κόμβων/συμβόλων. Χρησιμοποίησε start/end.")
                break
            node, foreign = pending.pop()
            self.visited += 1
            if node.type not in ("element", "script_element", "style_element", "self_closing_tag"):
                if node.type not in ("comment", "raw_text", "text", "doctype"):
                    if not self._enqueue_children(pending, node.named_children, foreign):
                        break
                continue
            if not self._visit_element(node, foreign, pending):
                break
        if self.base_present:
            if self.refs:
                self.notice("Υπάρχει <base href>: οι σχετικές συνδέσεις scripts/imports δεν επιλύθηκαν με μαντεψιά.")
            self.refs.clear()
        return {
            "symbols": sorted(self.result, key=lambda item: (item["first"], item["last"], item["name"])),
            "notice": "\n".join(dict.fromkeys(self.notices))[:2400],
            "backend": "tree-sitter html + embedded parsers",
            "cacheable": self.cacheable,
            "refs": sorted(self.refs),
            "injections": self.injections,
            "style_injections": self.style_injections,
        }


def analyze(text, suffix, parse, script_symbols, limits):
    raw = text.encode("utf-8")
    empty = {"symbols": [], "notice": "", "backend": "tree-sitter html", "cacheable": True, "refs": [], "injections": []}
    if len(raw) > limits["bytes"]:
        return {**empty, "notice": "Η δομή HTML δεν αναλύθηκε: όριο 2 MiB. Χρησιμοποίησε start/end."}
    try:
        tree = parse(raw, "html")
    except (ImportError, OSError, ValueError, AttributeError, TypeError) as exc:
        return {
            **empty,
            "cacheable": False,
            "missing_parser": True,
            "notice": "Η δομή HTML δεν αναλύθηκε: λείπει ή απέτυχε ο τοπικός HTML parser (" + type(exc).__name__ + "). Έλεγξε requirements-features.txt· χρησιμοποίησε start/end.",
        }
    if tree is None:
        return {**empty, "cacheable": False, "notice": "Χρονικό όριο του HTML parser. Χρησιμοποίησε start/end."}
    return _HtmlAnalyzer(raw, script_symbols, limits).run(tree.root_node)
