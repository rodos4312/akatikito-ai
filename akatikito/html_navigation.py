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
    deadline = time.monotonic() + MAX_SECONDS
    line_starts = [0] + [i + 1 for i, c in enumerate(raw) if c == 10]
    result = []
    refs = set()
    injections = []
    notices = []
    cacheable = True
    counts = Counter()
    pending = [(tree.root_node, False)]
    visited = 0
    embedded = 0
    base_present = False
    if tree.root_node.has_error:
        notices.append("Μερική δομή HTML: υπάρχουν σφάλματα ή ατελείς ετικέτες.")

    def notice(message):
        if len(notices) < 16:
            notices.append(message[:240])

    def row(a, b, name, header, kind):
        return {
            "first": bisect_right(line_starts, a),
            "last": bisect_right(line_starts, max(a, b - 1)),
            "name": name,
            "header": header,
            "kind": kind,
            "start": a,
            "end": b,
        }

    def inject(a, b, label, language, mapper=None):
        nonlocal embedded, cacheable
        if embedded >= MAX_INJECTIONS or time.monotonic() > deadline:
            cacheable = False
            notice("Μερική δομή: εξαντλήθηκε το όριο embedded scripts/χρόνου.")
            return False
        embedded += 1
        code = mapper.data if mapper else raw[a:b]
        analysis = script_symbols(code.decode("utf-8"), language)
        cacheable = cacheable and analysis["cacheable"]
        (empty.setdefault("style_injections", []) if language == ".css" else injections).append(
            {"name": label, "language": language, "first": bisect_right(line_starts, a), "last": bisect_right(line_starts, max(a, b - 1))}
        )
        if analysis["notice"]:
            notice(label + ": " + analysis["notice"])
        for child in analysis["symbols"]:
            if len(result) >= limits["symbols"]:
                cacheable = False
                notice("Μερική δομή: όριο συμβόλων HTML/scripts.")
                break
            x = a + (mapper.offset(child["start"]) if mapper else child["start"])
            y = a + (mapper.offset(child["end"], True) if mapper else child["end"])
            if not a <= x < y <= b:
                continue
            result.append(row(x, y, label + "." + child["name"], child["header"], child["kind"]))
        refs.update(analysis["refs"])
        return not analysis["notice"]

    while pending:
        if visited >= limits["nodes"] or len(result) >= limits["symbols"] or len(refs) >= limits["symbols"] or time.monotonic() > deadline:
            cacheable = False
            notice("Μερική δομή HTML: όριο χρόνου/κόμβων/συμβόλων. Χρησιμοποίησε start/end.")
            break
        node, foreign = pending.pop()
        visited += 1
        if node.type not in ("element", "script_element", "style_element", "self_closing_tag"):
            if node.type not in ("comment", "raw_text", "text", "doctype"):
                if len(pending) + node.named_child_count > limits["nodes"]:
                    cacheable = False
                    notice("Μερική δομή HTML: όριο κόμβων· πάρα πολλοί κόμβοι.")
                    break
                pending.extend((child, foreign) for child in reversed(node.named_children))
            continue
        start = node if node.type == "self_closing_tag" else next((c for c in node.named_children if c.type in ("start_tag", "self_closing_tag")), None)
        if start is None:
            continue
        tag_node = next((c for c in start.named_children if c.type == "tag_name"), None)
        if tag_node is None:
            continue
        tag = raw[tag_node.start_byte : tag_node.end_byte].decode("utf-8").lower()
        counts[tag] += 1
        attrs_known = True
        try:
            attrs, attribute_nodes = _attributes(start, raw, deadline)
        except ValueError as exc:
            attrs = {}
            attribute_nodes = {}
            attrs_known = False
            notice(str(exc))
            cacheable = False
        identity = " ".join((attrs.get("id") or "").split())[:80]
        display_tag = tag[:80] + ("…" if len(tag) > 80 else "")
        label = display_tag + ("#" + identity if identity else "") + "[" + str(counts[tag]) + "]"
        header = "<" + display_tag + (' id="' + identity + '"' if identity else "") + ">"
        if tag == "base" and ("href" in attrs or not attrs_known):
            base_present = True
        closed = not node.has_error and not node.is_missing and not start.has_error
        region = None
        if closed:
            region = row(node.start_byte, node.end_byte, label, header, "html")
            result.append(region)
        is_foreign = foreign or tag in ("svg", "math")
        for key, (attribute, value) in attribute_nodes.items():
            if not closed or not key.startswith("on") or key[2:] not in EVENTS or value is None or attribute.has_error:
                continue
            name = label + "." + key
            try:
                mapper = AttributeCode(raw[value.start_byte : value.end_byte], deadline)
            except ValueError as exc:
                notice(name + ": " + str(exc))
                cacheable = False
                continue
            if inject(value.start_byte, value.end_byte, name, ".js", mapper) and len(result) < limits["symbols"]:
                result.append(row(attribute.start_byte, attribute.end_byte, name, key + " (JavaScript)", "handler"))
        if tag == "script":
            if not closed or not attrs_known:
                continue
            language, mode = _script_type(attrs)
            if region:
                region["header"] = header + " [" + mode + (" " + str(attrs.get("type"))[:80] if "type" in attrs else "") + "]"
            if "src" in attrs:
                reference = _local_src(attrs["src"]) if language else None
                if reference:
                    refs.add(reference)
                continue
            if language is None:
                continue
            if is_foreign:
                notice(label + ": scripts σε foreign SVG/MathML δεν αναλύθηκαν ως HTML script.")
                continue
            body = next((c for c in node.named_children if c.type == "raw_text"), None)
            if body is not None and inject(body.start_byte, body.end_byte, label, language):
                region["kind"] = "script"
            continue
        if tag == "style":
            if not closed or not attrs_known:
                continue
            if attrs.get("type", "text/css") not in ("", "text/css") or attrs.get("lang", "css") != "css":
                notice(label + ": μη υποστηριζόμενη γλώσσα style· το περιεχόμενο παραμένει κείμενο.")
                continue
            body = next((c for c in node.named_children if c.type == "raw_text"), None)
            if body is not None and inject(body.start_byte, body.end_byte, label, ".css") and region:
                region["kind"] = "css"
            continue
        if tag in OPAQUE:
            continue
        if len(pending) + node.named_child_count > limits["nodes"]:
            cacheable = False
            notice("Μερική δομή HTML: όριο κόμβων· πάρα πολλοί κόμβοι.")
            break
        pending.extend((child, is_foreign) for child in reversed(node.named_children) if child.type not in ("start_tag", "end_tag", "self_closing_tag"))
    if base_present:
        if refs:
            notice("Υπάρχει <base href>: οι σχετικές συνδέσεις scripts/imports δεν επιλύθηκαν με μαντεψιά.")
        refs.clear()
    return {
        "symbols": sorted(result, key=lambda item: (item["first"], item["last"], item["name"])),
        "notice": "\n".join(dict.fromkeys(notices))[:2400],
        "backend": "tree-sitter html + embedded parsers",
        "cacheable": cacheable,
        "refs": sorted(refs),
        "injections": injections,
        "style_injections": empty.get("style_injections", []),
    }
