"""Bounded offline syntax navigation for supported programming languages.

Only installed, pinned Tree-sitter grammars are loaded. Source is parsed as data;
no preprocessor, compiler, project executable, plugin or network is invoked.
"""
from __future__ import annotations

from functools import lru_cache
import importlib
import time
import warnings

from .script_navigation import JS_LANGS, SCRIPT_LANGS, describe, references

LANGUAGES = {
    **{ext: ("javascript",) for ext in (".js", ".mjs", ".cjs", ".jsx")},
    **{ext: ("typescript",) for ext in (".ts", ".mts", ".cts")},
    ".tsx": ("tsx",),
    ".go": ("go",),
    ".html": ("html",),
    ".htm": ("html",),
    ".css": ("css",),
    **{ext: ("xml",) for ext in (".xml", ".xhtml", ".xht", ".svg", ".xsl", ".xslt", ".xsd", ".wsdl")},
    ".vue": ("vue",),
    ".svelte": ("svelte",),
    ".angular": ("angular",),
    ".ng": ("angular",),
    ".c": ("c",),
    ".h": ("c", "cpp"),
    **{ext: ("cpp",) for ext in (".cc", ".cpp", ".cxx", ".c++", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".inl", ".tpp")},
    ".rs": ("rust",),
    ".java": ("java",),
    ".cs": ("c_sharp",),
    **{ext: ("php", "php_only") for ext in (".php", ".phtml", ".php3", ".php4", ".php5", ".php7", ".php8")},
}
MAX_BYTES = 2 * 1024 * 1024
MAX_NODES = 100000
MAX_SYMBOLS = 4000
PARSE_MICROS = 150000
WALK_SECONDS = 0.5
TYPES = {
    "c": {"struct_specifier", "union_specifier", "enum_specifier"},
    "cpp": {"class_specifier", "struct_specifier", "union_specifier", "enum_specifier"},
    "rust": {"struct_item", "enum_item", "union_item", "trait_item", "impl_item", "mod_item"},
    "java": {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration", "annotation_type_declaration"},
    "c_sharp": {"class_declaration", "struct_declaration", "interface_declaration", "enum_declaration", "record_declaration"},
    "php": {"class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"},
}
FUNCTIONS = {
    "c": {"function_definition"},
    "cpp": {"function_definition"},
    "rust": {"function_item", "function_signature_item"},
    "java": {"method_declaration", "constructor_declaration", "compact_constructor_declaration"},
    "c_sharp": {
        "method_declaration",
        "constructor_declaration",
        "destructor_declaration",
        "operator_declaration",
        "conversion_operator_declaration",
        "local_function_statement",
    },
    "php": {"function_definition", "method_declaration"},
}


@lru_cache(maxsize=32)
def _language(name):
    from tree_sitter import Language

    package = "php" if name == "php_only" else "typescript" if name == "tsx" else name
    grammar = importlib.import_module("tree_sitter_" + package)
    entry = (
        "language_xml"
        if name == "xml"
        else "language_" + name
        if name in ("typescript", "tsx")
        else "language_php_only"
        if name == "php_only"
        else "language_php"
        if name == "php"
        else "language"
    )
    return Language(getattr(grammar, entry)())


def _parse(raw, name):
    from tree_sitter import Parser

    parser = Parser(_language(name))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        parser.timeout_micros = PARSE_MICROS
    return parser.parse(raw)


def _text(node, raw, limit=256):
    if node is None:
        return ""
    return " ".join(raw[node.start_byte : min(node.end_byte, node.start_byte + limit * 4)].decode("utf-8", errors="replace").split())[:limit]


def _declarator(node, raw):
    wrapper = ""
    steps = 0
    while node is not None and steps < 64:
        steps += 1
        if node.type == "operator_cast":
            return "operator " + _text(node.child_by_field_name("type"), raw), True
        if node.type in ("identifier", "field_identifier", "qualified_identifier", "destructor_name", "operator_name", "template_function"):
            return _text(node, raw), wrapper == "function_declarator"
        child = node.child_by_field_name("declarator")
        if child is None and node.type in ("parenthesized_declarator", "parenthesized_expression") and node.named_child_count == 1:
            child = node.named_child(0)
        if node.type not in ("parenthesized_declarator", "parenthesized_expression"):
            wrapper = node.type
        node = child
    return "", False


def _name(node, raw, lang):
    named = node.child_by_field_name("name")
    if named is not None:
        name = _text(named, raw)
        return "~" + name if node.type == "destructor_declaration" else name
    if node.type == "impl_item":
        return "impl " + _text(node.child_by_field_name("type"), raw)
    if node.type == "operator_declaration":
        return "operator " + _text(node.child_by_field_name("operator"), raw)
    if node.type == "conversion_operator_declaration":
        return "operator " + _text(node.child_by_field_name("type"), raw)
    if lang in ("c", "cpp"):
        return _declarator(node.child_by_field_name("declarator"), raw)[0]
    return ""


def _extent(node, raw, lang):
    start = node
    if lang == "cpp":
        while start.parent is not None and start.parent.type == "template_declaration":
            start = start.parent
    if lang in JS_LANGS:
        prev = start.prev_named_sibling
        while prev is not None and prev.type in ("decorator", "comment"):
            if prev.type == "decorator" and not prev.has_error:
                start = prev
            prev = prev.prev_named_sibling
    if lang == "rust":
        prev = start.prev_named_sibling
        while prev is not None and (
            prev.type == "attribute_item"
            or (prev.type in ("line_comment", "block_comment") and _text(prev, raw).startswith(("///", "/**")))
        ):
            start = prev
            prev = prev.prev_named_sibling
    return start.start_point.row + 1, node.end_point.row + (1 if node.end_point.column else 0), start.start_byte, node.end_byte


def symbols(text, suffix):
    """Return trustworthy complete nodes plus a visible partial/unavailable notice."""
    from .markup_navigation import FRAMEWORK_SUFFIXES, XML_SUFFIXES, css, framework, xml

    limits = {"bytes": MAX_BYTES, "nodes": MAX_NODES, "symbols": MAX_SYMBOLS}
    if suffix == ".css":
        return css(text, _parse, limits)
    if suffix in XML_SUFFIXES:
        return xml(text, suffix, _parse, symbols, limits)
    if suffix in FRAMEWORK_SUFFIXES:
        return framework(text, FRAMEWORK_SUFFIXES[suffix], _parse, symbols, limits)
    if suffix in (".html", ".htm"):
        from .html_navigation import analyze

        return analyze(text, suffix, _parse, symbols, limits)
    raw = text.encode("utf-8")
    empty = {"symbols": [], "notice": "", "backend": "tree-sitter", "cacheable": True, "refs": []}
    if len(raw) > MAX_BYTES:
        return {**empty, "notice": "Η δομή δεν αναλύθηκε: όριο 2 MiB. Το κείμενο παραμένει διαθέσιμο με start/end."}
    if suffix == ".go" and raw and not raw.endswith(b"\n"):
        raw += b"\n"
    try:
        attempts = []
        failures = []
        for language in LANGUAGES[suffix]:
            try:
                tree = _parse(raw, language)
                if tree is not None:
                    attempts.append((language, tree))
            except (ImportError, OSError, ValueError, AttributeError, TypeError) as exc:
                failures.append(exc)
        if not attempts:
            if failures:
                raise failures[0]
            return {
                **empty,
                "cacheable": False,
                "notice": "Η δομή δεν αναλύθηκε: ο parser έφτασε το χρονικό όριο. Χρησιμοποίησε start/end· δεν επιβεβαιώθηκε πλήρης συνάρτηση.",
            }
        prefer_php = (
            "php"
            if any(child.type == "php_tag" for lang, tree in attempts if lang == "php" for child in tree.root_node.named_children)
            else "php_only"
        )
        language, tree = min(attempts, key=lambda item: (item[1].root_node.has_error, 0 if item[0] == prefer_php else 1))
        lang = "php" if language == "php_only" else "typescript" if language == "tsx" else language
    except (ImportError, OSError, ValueError, AttributeError, TypeError) as exc:
        return {
            **empty,
            "cacheable": False,
            "missing_parser": True,
            "notice": "Η δομή δεν αναλύθηκε: λείπει ή απέτυχε ο τοπικός parser (" + type(exc).__name__ + "). Έλεγξε requirements-features.txt.",
        }
    cacheable = True
    result = []
    seen = set()
    refs = set()
    pending = [(tree.root_node, ())]
    count = 0
    deadline = time.monotonic() + WALK_SECONDS
    notice = "Μερική δομή: ο parser εντόπισε σφάλματα σύνταξης. Οι αβέβαιες δηλώσεις παραλείπονται." if tree.root_node.has_error else ""
    if failures:
        notice = ("Μερική διαθεσιμότητα parsers. Χρησιμοποιήθηκε " + language + ". " + notice).strip()
    while pending:
        if count >= MAX_NODES or len(result) >= MAX_SYMBOLS or len(refs) >= MAX_SYMBOLS or time.monotonic() > deadline:
            cacheable = False
            notice = "Μερική δομή: εξαντλήθηκε το όριο ανάλυσης. Το πλήρες κείμενο παραμένει διαθέσιμο με start/end."
            break
        node, scope = pending.pop()
        count += 1
        kind = node.type
        body = node.child_by_field_name("body")
        extent_node = node
        is_type = kind in TYPES.get(lang, set())
        is_function = kind in FUNCTIONS.get(lang, set())
        name = _name(node, raw, lang) if is_type or is_function else ""
        scope_name = name if is_type or is_function else ""
        entries = []
        if lang in SCRIPT_LANGS:
            name, tag, extent_node, scope_name, body = describe(node, raw, lang)
            if name and tag:
                entries.append((name, tag))
            if lang in JS_LANGS:
                ref = references(node, raw)
                if ref:
                    refs.add(ref)
        elif name:
            entries.append((name, "type" if is_type else "function" if body is not None else "declaration"))
        if lang in ("c", "cpp") and kind in ("declaration", "field_declaration"):
            for declarator in node.children_by_field_name("declarator"):
                prototype, callable_ = _declarator(declarator, raw)
                if prototype and callable_:
                    entries.append((prototype, "declaration"))
        if entries and not node.has_error and not node.is_missing and not extent_node.has_error:
            first, last, start, end = _extent(extent_node, raw, lang)
            header_end = body.start_byte if body is not None else node.end_byte
            header = " ".join(raw[start : min(header_end, start + 1000)].decode("utf-8", errors="replace").split())[:160]
            for entry, tag in entries:
                qualified = ".".join((*scope, entry))
                key = (start, end, qualified)
                if key in seen:
                    continue
                seen.add(key)
                result.append({"first": first, "last": max(first, last), "name": qualified, "header": header, "kind": tag, "start": start, "end": end})
        child_scope = (*scope, scope_name) if scope_name and len(scope) < 8 else scope
        if len(pending) + node.named_child_count > MAX_NODES:
            cacheable = False
            notice = "Μερική δομή: πάρα πολλοί κόμβοι. Χρησιμοποίησε start/end για τον υπόλοιπο κώδικα."
            break
        for child in reversed(node.named_children):
            pending.append((child, child_scope))
    return {
        "symbols": sorted(result, key=lambda row: (row["first"], row["last"], row["name"])),
        "notice": notice,
        "backend": "tree-sitter " + language,
        "cacheable": cacheable,
        "refs": sorted(refs),
    }


def source_suffix(path):
    """Explicit Angular filenames; ordinary HTML never guessed from text."""
    name = path.name.lower()
    if name.endswith((".component.html", ".angular.html", ".ng.html")):
        return ".angular"
    return path.suffix.lower()
