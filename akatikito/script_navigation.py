"""Tree-sitter node interpretation for JavaScript/TypeScript/TSX and Go.

Binding inference is structural and bounded. It never evaluates computed names,
callbacks, JSX, imports, modules or project code.
"""
from __future__ import annotations

SCRIPT_LANGS = {"javascript", "typescript", "go"}
JS_LANGS = {"javascript", "typescript"}
JS_FUNCTIONS = {
    "function_declaration",
    "generator_function_declaration",
    "function_expression",
    "generator_function",
    "arrow_function",
    "method_definition",
    "function_signature",
    "method_signature",
    "abstract_method_signature",
}
JS_TYPES = {
    "class",
    "class_declaration",
    "abstract_class_declaration",
    "interface_declaration",
    "type_alias_declaration",
    "enum_declaration",
    "internal_module",
}
GO_FUNCTIONS = {"function_declaration", "method_declaration", "func_literal", "method_elem"}
GO_TYPES = {"type_spec", "type_alias"}
WRAPPERS = {"parenthesized_expression", "as_expression", "satisfies_expression", "non_null_expression", "type_assertion"}


def text(node, raw, limit=256):
    if node is None:
        return ""
    return " ".join(raw[node.start_byte : min(node.end_byte, node.start_byte + limit * 4)].decode("utf-8", errors="replace").split())[:limit]


def literal(node, raw):
    if node is None or node.type != "string" or node.has_error or node.end_byte - node.start_byte > 4096:
        return None
    value = raw[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    if len(value) < 2 or value[0] not in ('"', "'") or value[-1] != value[0] or "\\" in value:
        return None
    value = value[1:-1]
    return value if not any(ord(c) < 32 for c in value) else None


def property_name(node, raw):
    if node is None:
        return ""
    if node.type == "string":
        return literal(node, raw) or text(node, raw)
    if node.type == "computed_property_name" and node.named_child_count == 1:
        value = literal(node.named_child(0), raw)
        if value is not None:
            return value
    return text(node, raw)


def _member_name(node, raw):
    parts = []
    current = node
    for _ in range(64):
        if current is None:
            return ""
        if current.type in ("identifier", "property_identifier", "private_property_identifier"):
            parts.append(text(current, raw))
            value = ".".join(reversed(parts))
            return value if len(value) <= 256 else ""
        if current.type != "member_expression":
            return ""
        prop = current.child_by_field_name("property")
        if prop is None:
            return ""
        parts.append(text(prop, raw))
        current = current.child_by_field_name("object")
    return ""


def binding(node, raw):
    """Find only direct bindings or the primary callback of memo/forwardRef."""
    current = node
    separate_callback = False
    for _ in range(24):
        parent = current.parent
        if parent is None:
            break
        if parent.type in WRAPPERS and parent.named_child(0) == current:
            current = parent
            continue
        if parent.type == "arguments" and parent.named_child_count and parent.named_child(0) == current:
            call = parent.parent
            if call is not None and call.type == "call_expression" and _member_name(call.child_by_field_name("function"), raw).split(".")[-1] in ("memo", "forwardRef"):
                separate_callback |= parent.named_child_count > 1
                current = call
                continue
        if parent.type in ("variable_declarator", "field_definition", "public_field_definition", "pair") and parent.child_by_field_name("value") == current:
            name = parent.child_by_field_name("name") or parent.child_by_field_name("property") or parent.child_by_field_name("key")
            if name is not None and name.type not in ("object_pattern", "array_pattern"):
                return property_name(name, raw), node if separate_callback else parent
        if parent.type == "assignment_expression" and parent.child_by_field_name("right") == current:
            name = _member_name(parent.child_by_field_name("left"), raw)
            if name:
                return name, node if separate_callback else parent
        if parent.type == "export_statement" and parent.child_by_field_name("value") == current and any(c.type == "default" for c in parent.children):
            return "default", node if separate_callback else parent
        break
    return "", node


def _go_binding(node, raw):
    current = node
    if current.parent is not None and current.parent.type == "parenthesized_expression":
        current = current.parent
    values = current.parent
    if values is None or values.type != "expression_list":
        return "", node
    owner = values.parent
    if owner is None:
        return "", node
    if owner.type == "var_spec" and owner.child_by_field_name("value") == values:
        names = owner.children_by_field_name("name")
    elif owner.type == "short_var_declaration" and owner.child_by_field_name("right") == values:
        left = owner.child_by_field_name("left")
        names = left.named_children if left is not None else []
    else:
        return "", node
    items = values.named_children
    if len(items) != len(names):
        return "", node
    position = next((i for i, item in enumerate(items) if item == current), None)
    if position is None or names[position].type != "identifier":
        return "", node
    name = text(names[position], raw)
    return (name, owner if len(items) == 1 else node) if name != "_" else ("", node)


def _go_receiver(node, raw):
    receiver = node.child_by_field_name("receiver")
    if receiver is None or not receiver.named_child_count:
        return ""
    current = receiver.named_child(0).child_by_field_name("type")
    for _ in range(12):
        if current is None:
            break
        if current.type == "pointer_type":
            current = current.named_child(0)
        elif current.type == "generic_type":
            current = current.child_by_field_name("type")
        else:
            return text(current, raw)
    return ""


def _extend(node):
    parent = node.parent
    if parent is not None and parent.type in ("lexical_declaration", "variable_declaration") and parent.named_child_count == 1:
        node = parent
        parent = node.parent
    if parent is not None and parent.type in ("expression_statement", "export_statement", "ambient_declaration", "type_declaration", "var_declaration") and parent.named_child_count == 1:
        node = parent
    parent = node.parent
    if parent is not None and parent.type == "export_statement" and (parent.child_by_field_name("declaration") == node or parent.child_by_field_name("value") == node):
        node = parent
    return node


def describe(node, raw, lang):
    """name, kind, extent node, lexical scope name, body/header boundary."""
    kind = node.type
    name = ""
    tag = ""
    extent = node
    scope = ""
    body = node.child_by_field_name("body")
    if lang in JS_LANGS:
        if kind in JS_FUNCTIONS or kind in JS_TYPES:
            bound, owner = binding(node, raw)
            explicit = property_name(node.child_by_field_name("name"), raw)
            name = explicit if bound == "default" and explicit else bound or explicit
            extent = owner if bound else node
            tag = "type" if kind in JS_TYPES else "function" if body is not None else "declaration"
        elif kind == "object":
            scope, _ = binding(node, raw)
        if name:
            scope = name
            extent = _extend(extent)
    else:
        if kind in GO_FUNCTIONS or kind in GO_TYPES:
            name = text(node.child_by_field_name("name"), raw)
            if kind == "func_literal":
                name, extent = _go_binding(node, raw)
            if kind == "method_declaration":
                receiver = _go_receiver(node, raw)
                if receiver:
                    name = receiver + "." + name
            tag = "type" if kind in GO_TYPES else "function" if body is not None else "declaration"
            if kind in GO_TYPES:
                declared = node.child_by_field_name("type")
                if declared is not None:
                    body = next((child for child in declared.named_children if child.type == "field_declaration_list"), None)
                    if body is None and declared.type == "interface_type":
                        body = declared
            if name:
                scope = name
                extent = _extend(extent)
    return name, tag, extent, scope, body


def references(node, raw):
    """Literal JS imports/reexports/CommonJS/dynamic imports, no regex matches."""
    if node.has_error:
        return None
    source = None
    if node.type in ("import_statement", "export_statement", "import_require_clause"):
        source = node.child_by_field_name("source")
    elif node.type == "call_expression":
        function = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if function is not None and (function.type == "import" or (function.type == "identifier" and text(function, raw) == "require")) and args is not None and args.named_child_count == 1:
            source = args.named_child(0)
    value = literal(source, raw)
    return "js:" + value if value else None
