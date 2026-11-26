"""Current Python and multi-language structure and complete source blocks; never a replacement for source."""
from __future__ import annotations
import ast
import re
from pathlib import Path

READ_LINES = 400

JS_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".htm"}
JAVA_CS_SUFFIXES = {".java", ".cs"}
C_CPP_SUFFIXES = {".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"}
RUST_SUFFIXES = {".rs"}
GO_SUFFIXES = {".go"}
PHP_SUFFIXES = {".php"}
SUPPORTED_STRUCT_SUFFIXES = (
    JS_SUFFIXES | JAVA_CS_SUFFIXES | C_CPP_SUFFIXES | RUST_SUFFIXES | GO_SUFFIXES | PHP_SUFFIXES
)

_CONTROL_KEYWORDS = {
    "if", "else", "for", "foreach", "while", "do", "switch", "case", "catch",
    "try", "finally", "return", "throw", "new", "delete", "sizeof", "typeof",
    "yield", "await", "using", "lock", "fixed", "checked", "unchecked",
    "synchronized", "goto", "co_return", "co_await", "co_yield",
    "static_assert", "decltype", "alignof", "noexcept", "super", "this", "base",
}

_NON_RETURN_START = {
    "return", "throw", "delete", "else", "case", "goto", "break", "continue",
    "yield", "await", "co_return", "co_await", "co_yield", "using", "import",
    "package", "namespace", "typedef", "assert", "if", "for", "foreach", "while",
    "switch", "catch", "lock", "synchronized",
}

# Language-specific regexes (matched on comment/string-sanitized lines)
_JS_CONTAINER_RE = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?(?:declare\s+)?(?:abstract\s+)?"
    r"(class|interface|enum)\s+([A-Za-z_$][\w$]*)"
)
_JS_FN_RE = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?(?:declare\s+)?(?:async\s+)?"
    r"(?:function\s*\*?\s+([A-Za-z_$][\w$]*)"
    r"|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=\s*(?:async\s*)?(?:\([^)\n]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=>)"
)
_JS_METHOD_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|readonly|override|abstract|async|get|set)\s+)*"
    r"\*?([A-Za-z_$#][\w$]*)\s*(?:<[^>\n]*>)?\s*\([^;\n]*\)\s*(?::\s*[^={\n]+)?\s*\{"
)

_RUST_CONTAINER_RE = re.compile(
    r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?(?:unsafe\s+)?"
    r"(struct|enum|trait|mod|union)\s+([A-Za-z_]\w*)"
)
_RUST_IMPL_RE = re.compile(
    r"^\s*(?:unsafe\s+)?impl(?:\s*<[^>]*>)?\s+(?:[A-Za-z_][\w:<>' ,+*&()]*?\s+for\s+)?([A-Za-z_][\w:]*)"
)
_RUST_FN_RE = re.compile(
    r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?(?:default\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
    r"(?:extern(?:\s+\"[^\"]*\")?\s+)?fn\s+([A-Za-z_]\w*)"
)

_GO_TYPE_RE = re.compile(
    r"^\s*type\s+([A-Za-z_]\w*)(?:\s*\[[^\]]*\])?\s+(struct|interface)\b"
)
_GO_FUNC_RE = re.compile(
    r"^\s*func\s+(?:\(\s*\w+\s+\*?([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*\)\s+)?([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*\("
)

_PHP_CONTAINER_RE = re.compile(
    r"^\s*(?:(?:abstract|final|readonly)\s+)*(class|interface|trait|enum)\s+([A-Za-z_]\w*)"
)
_PHP_FN_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+&?\s*([A-Za-z_]\w*)\s*\("
)

_JAVA_CS_CONTAINER_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|abstract|final|sealed|partial|readonly|ref|unsafe|new|file|strictfp)\s+)*"
    r"(class|interface|enum|struct|record|namespace)\s+([A-Za-z_][\w.]*)"
)
_JAVA_CS_MODS = {
    "public", "private", "protected", "internal", "static", "final", "abstract",
    "synchronized", "native", "default", "strictfp", "virtual", "override",
    "sealed", "extern", "unsafe", "async", "partial", "new", "readonly", "ref",
}

_CPP_CONTAINER_RE = re.compile(
    r"^\s*(?:template\s*<[^>]*>\s*)?(class|struct|enum(?:\s+class|\s+struct)?|union|namespace)\s+([A-Za-z_]\w*)"
)
_CHAR_LIT_RE = re.compile(r"'(?:\\(?:u\{[0-9a-fA-F]+\}|x[0-9a-fA-F]{2}|.)|[^'\\])'")


def _sanitize_lines(lines: list[str], suffix: str) -> list[str]:
    """Replace comments and string/char literal bodies with spaces while preserving line count and structure."""
    out: list[str] = []
    in_block_comment = False
    in_backtick = False
    allow_backtick = suffix in JS_SUFFIXES or suffix in GO_SUFFIXES
    single_is_string = suffix in JS_SUFFIXES or suffix in PHP_SUFFIXES
    hash_comment = suffix in PHP_SUFFIXES

    for line in lines:
        chars: list[str] = []
        i = 0
        n = len(line)
        while i < n:
            ch = line[i]
            nxt = line[i + 1] if i + 1 < n else ""
            if in_block_comment:
                if ch == "*" and nxt == "/":
                    chars.extend((" ", " "))
                    in_block_comment = False
                    i += 2
                else:
                    chars.append(" ")
                    i += 1
                continue
            if in_backtick:
                if ch == "\\" and suffix in JS_SUFFIXES and i + 1 < n:
                    chars.extend((" ", " "))
                    i += 2
                elif ch == "`":
                    chars.append("`")
                    in_backtick = False
                    i += 1
                else:
                    chars.append(" ")
                    i += 1
                continue
            if ch == "/" and nxt == "*":
                chars.extend((" ", " "))
                in_block_comment = True
                i += 2
                continue
            if ch == "/" and nxt == "/":
                chars.extend(" " * (n - i))
                break
            if hash_comment and ch == "#" and nxt != "[":
                chars.extend(" " * (n - i))
                break
            if ch == '"':
                chars.append('"')
                i += 1
                while i < n:
                    c2 = line[i]
                    if c2 == "\\" and i + 1 < n:
                        chars.extend((" ", " "))
                        i += 2
                    elif c2 == '"':
                        chars.append('"')
                        i += 1
                        break
                    else:
                        chars.append(" ")
                        i += 1
                continue
            if allow_backtick and ch == "`":
                chars.append("`")
                in_backtick = True
                i += 1
                continue
            if ch == "'":
                if single_is_string:
                    chars.append("'")
                    i += 1
                    while i < n:
                        c2 = line[i]
                        if c2 == "\\" and i + 1 < n:
                            chars.extend((" ", " "))
                            i += 2
                        elif c2 == "'":
                            chars.append("'")
                            i += 1
                            break
                        else:
                            chars.append(" ")
                            i += 1
                    continue
                # For C/C++/Rust/Java/C#/Go: only treat valid char literals (never Rust lifetimes 'a or C++ 1'000)
                prev_ch = line[i - 1] if i > 0 else ""
                if not (prev_ch.isalnum() or prev_ch == "_"):
                    m_char = _CHAR_LIT_RE.match(line, i)
                    if m_char:
                        span_len = m_char.end() - i
                        chars.append("'")
                        chars.extend(" " * max(0, span_len - 2))
                        chars.append("'")
                        i = m_char.end()
                        continue
            chars.append(ch)
            i += 1
        out.append("".join(chars))
    return out


def _find_block_end(sanitized: list[str], start_idx: int, max_open_lines: int = 12, max_span: int = 800) -> tuple[int, bool]:
    """Return (1-based end_line, opened_brace) for a brace-delimited or statement block."""
    n_lines = len(sanitized)
    depth = 0
    opened = False
    end_line = start_idx + 1
    limit = min(n_lines, start_idx + max_span)
    for j in range(start_idx, limit):
        cur = sanitized[j]
        for ch in cur:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
            elif ch == ";" and not opened:
                return j + 1, False
        end_line = j + 1
        if opened and depth <= 0:
            return end_line, True
        if not opened and j - start_idx >= max_open_lines:
            return start_idx + 1, False
    return max(start_idx + 1, end_line), opened


def _collect_paren_terminator(sanitized: list[str], start_idx: int, paren_col: int, max_lines: int = 10) -> tuple[int, str]:
    """Find the closing ')' matching paren_col and inspect what follows: '{', '=>', ';', or ''."""
    n_lines = len(sanitized)
    pdepth = 0
    closed_at: tuple[int, int] | None = None
    for j in range(start_idx, min(n_lines, start_idx + max_lines)):
        s = sanitized[j]
        k = paren_col if j == start_idx else 0
        while k < len(s):
            ch = s[k]
            if ch == "(":
                pdepth += 1
            elif ch == ")":
                pdepth -= 1
                if pdepth == 0:
                    closed_at = (j, k + 1)
                    break
            k += 1
        if closed_at is not None:
            break
    if closed_at is None:
        return start_idx, ""
    cj, col = closed_at
    for j in range(cj, min(n_lines, cj + 5)):
        tail = sanitized[j][col:] if j == cj else sanitized[j]
        i = 0
        while i < len(tail):
            ch = tail[i]
            if ch == "{":
                return j, "{"
            if ch == ";":
                return j, ";"
            if ch == "=" and i + 1 < len(tail) and tail[i + 1] == ">":
                return j, "=>"
            i += 1
    return cj, ""


def _strip_angle_groups(text: str) -> str:
    """Replace balanced <...> generic/template arguments with spaces for easy token splitting."""
    out = []
    depth = 0
    for ch in text:
        if ch == "<":
            depth += 1
            out.append(" ")
        elif ch == ">" and depth > 0:
            depth -= 1
            out.append(" ")
        elif depth == 0:
            out.append(ch)
    return "".join(out)


_INLINE_ANNOTATION_RE = re.compile(
    r"^\s*(?:@[A-Za-z_][\w.]*(?:\s*\([^)\n]*\))?|\[[^\]\n]+\])\s+"
)


def _match_java_cs_method(sline: str, sanitized: list[str], idx: int, enclosing_type: str, in_interface: bool) -> tuple[str, int, str] | None:
    work = sline
    while True:
        m_ann = _INLINE_ANNOTATION_RE.match(work)
        if not m_ann:
            break
        work = work[m_ann.end():]
    stripped = work.strip()
    if not stripped or stripped.startswith(("@", "[", "#", ".", "//", "/*")):
        return None
    local_paren = work.find("(")
    if local_paren <= 0:
        return None
    paren_col = (len(sline) - len(work)) + local_paren
    before_paren = work[:local_paren]
    if "=" in before_paren or ";" in before_paren or "{" in before_paren or "}" in before_paren:
        return None
    # Remove leading generic type params like `<T> void foo(` and trailing generic method params `Foo<T>(`
    cleaned = _strip_angle_groups(before_paren)
    # Reject chained calls like `obj.method(` where a dot immediately precedes the identifier
    if re.search(r"\.\s*[A-Za-z_]\w*\s*$", cleaned):
        return None
    tokens = re.findall(r"[A-Za-z_][\w.]*", cleaned)
    if not tokens:
        return None
    name = tokens[-1]
    if "." in name or name in _CONTROL_KEYWORDS:
        return None
    first_tok = tokens[0]
    if first_tok in _NON_RETURN_START:
        return None
    prefix_tokens = tokens[:-1]
    non_mod_prefix = [t for t in prefix_tokens if t not in _JAVA_CS_MODS]
    is_ctor = bool(enclosing_type and name == enclosing_type.split(".")[-1] and not non_mod_prefix)
    # Must either have a return type (non-modifier token before name) or be a constructor
    if not non_mod_prefix and not is_ctor:
        return None
    # Avoid matching `new Foo(` as a constructor/method when `new` is the only prefix
    if prefix_tokens == ["new"]:
        return None
    term_idx, term = _collect_paren_terminator(sanitized, idx, paren_col)
    if term == "{":
        return name, term_idx, "{"
    if term == "=>":
        return name, term_idx, "=>"
    if term == ";" and (in_interface or any(m in ("abstract", "extern", "native", "partial") for m in prefix_tokens)):
        return name, term_idx, ";"
    return None


def _match_c_cpp_function(sline: str, sanitized: list[str], idx: int, suffix: str, enclosing_type: str) -> tuple[str, int, int, str] | None:
    stripped = sline.strip()
    if not stripped or stripped.startswith(("#", ".", "//", "/*", "case ", "default:")):
        return None
    paren_col = sline.find("(")
    if paren_col <= 0:
        return None
    before_paren = sline[:paren_col]
    if ";" in before_paren or "{" in before_paren or "}" in before_paren:
        return None
    if "=" in before_paren and "operator" not in before_paren:
        return None
    cleaned = _strip_angle_groups(before_paren)
    # Reject member calls like `obj.foo(` or `ptr->foo(`
    if re.search(r"(?:\.|->)\s*[A-Za-z_~]\w*\s*$", cleaned):
        return None
    m_name = re.search(r"([A-Za-z_~][\w:~]*)\s*$", cleaned)
    if not m_name:
        return None
    full_name = m_name.group(1)
    base_name = full_name.rsplit("::", 1)[-1]
    if base_name in _CONTROL_KEYWORDS or full_name in _CONTROL_KEYWORDS:
        return None
    prefix_part = cleaned[: m_name.start()].strip()
    prefix_words = re.findall(r"[A-Za-z_]\w*", prefix_part)
    if prefix_words and prefix_words[0] in _NON_RETURN_START:
        return None

    start_idx = idx
    is_ctor_dtor = False
    if enclosing_type:
        cls_short = enclosing_type.rsplit("::", 1)[-1]
        if base_name == cls_short or base_name == "~" + cls_short:
            is_ctor_dtor = True
    if "::" in full_name:
        parts = full_name.split("::")
        if len(parts) >= 2 and (parts[-1] == parts[-2] or parts[-1] == "~" + parts[-2]):
            is_ctor_dtor = True

    has_return_type = bool(prefix_words)
    # Handle 2-line C/C++ style where return type is on the immediately preceding line:
    #   static std::string
    #   format_user(const User& u) const {
    if not has_return_type and not is_ctor_dtor and idx > 0:
        prev = sanitized[idx - 1].strip()
        prev_words = re.findall(r"[A-Za-z_]\w*", _strip_angle_groups(prev))
        if (
            prev
            and not prev.startswith("#")
            and not prev.endswith((";", "{", "}", ":", ","))
            and prev_words
            and prev_words[0] not in _NON_RETURN_START
            and prev_words[0] not in _CONTROL_KEYWORDS
        ):
            has_return_type = True
            start_idx = idx - 1

    if not has_return_type and not is_ctor_dtor:
        return None

    term_idx, term = _collect_paren_terminator(sanitized, idx, paren_col)
    is_header = suffix in {".h", ".hpp", ".hh", ".hxx"}
    if term == "{":
        return full_name, start_idx, term_idx, "{"
    if term == ";" and is_header and has_return_type:
        return full_name, start_idx, term_idx, ";"
    return None


def _scan_js_line(ctx: dict, idx: int, sline: str, raw_header: str, qualify) -> None:
    line_no = idx + 1
    sanitized, blocks, scope_stack = ctx["sanitized"], ctx["blocks"], ctx["scope_stack"]
    mc = _JS_CONTAINER_RE.match(sline)
    if mc:
        kind, name = mc.group(1), mc.group(2)
        end_line, opened = _find_block_end(sanitized, idx)
        blocks.append((line_no, end_line, qualify(name), raw_header))
        if opened and end_line > line_no:
            scope_stack.append((name, kind, end_line))
        return
    mf = _JS_FN_RE.match(sline)
    if mf:
        name = mf.group(1) or mf.group(2)
        if name:
            end_line, _ = _find_block_end(sanitized, idx, max_open_lines=4)
            blocks.append((line_no, end_line, qualify(name), raw_header))
        return
    if scope_stack and scope_stack[-1][1] == "class" and line_no > ctx["in_fn_until"]:
        mm = _JS_METHOD_RE.match(sline)
        name = mm.group(1) if mm else ""
        if name and name not in _CONTROL_KEYWORDS:
            end_line, opened = _find_block_end(sanitized, idx)
            blocks.append((line_no, end_line, qualify(name), raw_header))
            if opened:
                ctx["in_fn_until"] = max(ctx["in_fn_until"], end_line)


def _scan_rust_line(ctx: dict, idx: int, sline: str, raw_header: str, qualify) -> None:
    line_no = idx + 1
    sanitized, blocks, scope_stack = ctx["sanitized"], ctx["blocks"], ctx["scope_stack"]
    mc = _RUST_CONTAINER_RE.match(sline)
    if mc:
        kind, name = mc.group(1), mc.group(2)
        end_line, opened = _find_block_end(sanitized, idx)
        blocks.append((line_no, end_line, qualify(name), raw_header))
        if opened and end_line > line_no:
            scope_stack.append((name, kind, end_line))
        return
    mi = _RUST_IMPL_RE.match(sline)
    if mi:
        target = mi.group(1).rsplit("::", 1)[-1]
        end_line, opened = _find_block_end(sanitized, idx)
        blocks.append((line_no, end_line, qualify(f"impl {target}"), raw_header))
        if opened and end_line > line_no:
            scope_stack.append((target, "impl", end_line))
        return
    mf = _RUST_FN_RE.match(sline)
    if mf and mf.group(1):
        end_line, _ = _find_block_end(sanitized, idx)
        blocks.append((line_no, end_line, qualify(mf.group(1)), raw_header))


def _scan_go_line(ctx: dict, idx: int, sline: str, raw_header: str, _qualify) -> None:
    line_no = idx + 1
    sanitized, blocks = ctx["sanitized"], ctx["blocks"]
    mt = _GO_TYPE_RE.match(sline)
    if mt:
        end_line, _ = _find_block_end(sanitized, idx)
        blocks.append((line_no, end_line, mt.group(1), raw_header))
        return
    mf = _GO_FUNC_RE.match(sline)
    if mf:
        recv, name = mf.group(1), mf.group(2)
        qname = f"{recv}.{name}" if recv else name
        end_line, _ = _find_block_end(sanitized, idx)
        blocks.append((line_no, end_line, qname, raw_header))


def _scan_php_line(ctx: dict, idx: int, sline: str, raw_header: str, qualify) -> None:
    line_no = idx + 1
    sanitized, blocks, scope_stack = ctx["sanitized"], ctx["blocks"], ctx["scope_stack"]
    mc = _PHP_CONTAINER_RE.match(sline)
    if mc:
        kind, name = mc.group(1), mc.group(2)
        end_line, opened = _find_block_end(sanitized, idx)
        blocks.append((line_no, end_line, qualify(name), raw_header))
        if opened and end_line > line_no:
            scope_stack.append((name, kind, end_line))
        return
    mf = _PHP_FN_RE.match(sline)
    if mf and mf.group(1):
        end_line, _ = _find_block_end(sanitized, idx)
        blocks.append((line_no, end_line, qualify(mf.group(1)), raw_header))


def _scan_java_cs_line(ctx: dict, idx: int, sline: str, raw_header: str, qualify) -> None:
    line_no = idx + 1
    sanitized, blocks, scope_stack = ctx["sanitized"], ctx["blocks"], ctx["scope_stack"]
    mc = _JAVA_CS_CONTAINER_RE.match(sline)
    if mc:
        kind, name = mc.group(1), mc.group(2)
        end_line, opened = _find_block_end(sanitized, idx)
        if kind != "namespace":
            blocks.append((line_no, end_line, qualify(name), raw_header))
        if opened and end_line > line_no:
            scope_stack.append((name, kind, end_line))
        return
    if line_no <= ctx["in_fn_until"]:
        return
    enclosing = scope_stack[-1][0] if scope_stack and scope_stack[-1][1] != "namespace" else ""
    in_iface = bool(scope_stack and scope_stack[-1][1] == "interface")
    mm = _match_java_cs_method(sline, sanitized, idx, enclosing, in_iface)
    if not mm:
        return
    name, term_idx, term = mm
    if term == "{":
        end_line, _ = _find_block_end(sanitized, idx)
    elif term == "=>":
        end_line = next(
            (j + 1 for j in range(term_idx, min(len(sanitized), term_idx + 15)) if ";" in sanitized[j]),
            min(len(sanitized), term_idx + 15),
        )
    else:
        end_line = term_idx + 1
    blocks.append((line_no, end_line, qualify(name), raw_header))
    ctx["in_fn_until"] = max(ctx["in_fn_until"], end_line)


def _scan_c_cpp_line(ctx: dict, idx: int, sline: str, raw_header: str, qualify) -> None:
    line_no = idx + 1
    sanitized, blocks, scope_stack = ctx["sanitized"], ctx["blocks"], ctx["scope_stack"]
    mc = _CPP_CONTAINER_RE.match(sline)
    if mc:
        kind, name = mc.group(1), mc.group(2)
        end_line, opened = _find_block_end(sanitized, idx, max_open_lines=4)
        if opened:
            if kind != "namespace":
                blocks.append((line_no, end_line, qualify(name), raw_header))
            if kind == "namespace" or end_line > line_no:
                scope_stack.append((name, kind, end_line))
            return
    if line_no <= ctx["in_fn_until"]:
        return
    enclosing = scope_stack[-1][0] if scope_stack and scope_stack[-1][1] != "namespace" else ""
    mf = _match_c_cpp_function(sline, sanitized, idx, ctx["suffix"], enclosing)
    if not mf:
        return
    name, start_i, term_idx, term = mf
    end_line = _find_block_end(sanitized, idx)[0] if term == "{" else term_idx + 1
    hdr = ctx["lines"][idx].strip()[:160]
    qname = name if "::" in name else qualify(name)
    blocks.append((start_i + 1, end_line, qname, hdr))
    ctx["in_fn_until"] = max(ctx["in_fn_until"], end_line)


def _pick_language_scanner(suffix: str):
    for suffixes, scanner in (
        (JS_SUFFIXES, _scan_js_line),
        (RUST_SUFFIXES, _scan_rust_line),
        (GO_SUFFIXES, _scan_go_line),
        (PHP_SUFFIXES, _scan_php_line),
        (JAVA_CS_SUFFIXES, _scan_java_cs_line),
        (C_CPP_SUFFIXES, _scan_c_cpp_line),
    ):
        if suffix in suffixes:
            return scanner
    return None


def scan_definitions(lines: list[str], suffix: str) -> list[tuple[int, int, str, str]]:
    """Scan structural definitions (classes, structs, traits, functions, methods) for non-Python files."""
    scanner = _pick_language_scanner(suffix) if lines and suffix in SUPPORTED_STRUCT_SUFFIXES else None
    if scanner is None:
        return []
    sanitized = _sanitize_lines(lines, suffix)
    scope_stack: list[tuple[str, str, int]] = []
    ctx = {
        "sanitized": sanitized,
        "lines": lines,
        "suffix": suffix,
        "blocks": [],
        "scope_stack": scope_stack,
        "in_fn_until": 0,
    }
    for idx, sline in enumerate(sanitized):
        line_no = idx + 1
        while scope_stack and line_no > scope_stack[-1][2]:
            scope_stack.pop()
        if not sline.strip():
            continue
        raw_header = lines[idx].strip()[:160]
        prefix = ".".join(s[0] for s in scope_stack)
        scanner(ctx, idx, sline, raw_header, lambda nm, p=prefix: f"{p}.{nm}" if p else nm)
    return ctx["blocks"]


def _scan_script_blocks(lines: list[str], suffix: str = ".js") -> list[tuple[int, int, str, str]]:
    return scan_definitions(lines, suffix)


class SourceIndex:
    def __init__(self):
        self.cache = {}

    def load(self, path):
        from .syntax_index import LANGUAGES, source_suffix, symbols as ts_symbols

        path = Path(path)
        info = path.stat()
        version = (str(path.resolve()), info.st_mtime_ns, info.st_size)
        cached = self.cache.get(str(path))
        if cached and cached[0] == version:
            return cached[1]
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        lines = text.splitlines()
        blocks = []
        tree = None
        analysis = {"symbols": [], "notice": "", "cacheable": True}
        suffix = source_suffix(path)
        if suffix in (".py", ".pyw"):
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                pass

        def visit(node, parents=()):
            named = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            scope = parents
            if named:
                first = min([node.lineno] + [d.lineno for d in node.decorator_list])
                blocks.append((first, node.end_lineno, ".".join((*parents, node.name)), lines[node.lineno - 1].strip()))
                scope = (*parents, node.name)
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

        if tree is not None:
            visit(tree)
        elif suffix in LANGUAGES:
            analysis = ts_symbols("\n".join(lines), suffix)
            if analysis.get("missing_parser") and suffix in SUPPORTED_STRUCT_SUFFIXES:
                # Graceful fallback to built-in structural scanner when optional tree-sitter grammar is not installed.
                blocks.extend(scan_definitions(lines, suffix))
                analysis = {"symbols": [], "notice": "", "cacheable": True}
            else:
                blocks.extend((row["first"], row["last"], row["name"], row["header"]) for row in analysis["symbols"])
        elif suffix in SUPPORTED_STRUCT_SUFFIXES:
            blocks.extend(scan_definitions(lines, suffix))
        data = {"version": version, "lines": lines, "blocks": sorted(blocks), "tree": tree, "analysis": analysis}
        if analysis.get("cacheable", True):
            self.cache[str(path)] = (version, data)
        return data

    def span(self, path, args):
        data = self.load(path)
        total = len(data["lines"])
        anchor = args.get("anchor_line")
        if anchor is not None:
            anchor = int(anchor)
            if not 1 <= anchor <= total:
                raise ValueError(f"Η anchor_line πρέπει να είναι μεταξύ 1 και {total}.")
            parsed = [row for row in data.get("analysis", {}).get("symbols", []) if row["first"] <= anchor <= row["last"]]
            if parsed:
                parents = set()
                stack = []
                for row in sorted(parsed, key=lambda r: (r["start"], -r["end"])):
                    bounds = (row["start"], row["end"])
                    while stack and not (stack[-1][0] <= bounds[0] and stack[-1][1] >= bounds[1]):
                        stack.pop()
                    if stack and stack[-1] != bounds:
                        parents.add(stack[-1])
                    if not stack or stack[-1] != bounds:
                        stack.append(bounds)
                unique = {}
                rank = {"function": 0, "type": 1, "declaration": 2, "binding": 3, "css": 4, "template": 5, "script": 6}
                for row in parsed:
                    bounds = (row["start"], row["end"])
                    if bounds in parents:
                        continue
                    previous = unique.get(bounds)
                    if previous is None or rank.get(row["kind"], 7) < rank.get(previous["kind"], 7):
                        unique[bounds] = row
                leaves = list(unique.values())
                if len(leaves) > 1:
                    return (
                        anchor,
                        anchor,
                        data,
                        "Αμφίσημη γραμμή: περιέχει πολλαπλές δηλώσεις ("
                        + ", ".join(row["name"] for row in leaves[:6])
                        + "). Επιστρέφεται η γραμμή, όχι επιβεβαιωμένη μοναδική συνάρτηση.",
                    )
                row = leaves[0]
                label = {
                    "declaration": "Πλήρης δήλωση ",
                    "css": "Πλήρης κανόνας CSS ",
                    "xml": "Πλήρες στοιχείο XML ",
                    "template": "Πλήρες τμήμα template ",
                    "binding": "Πλήρες template binding ",
                    "html": "Πλήρες στοιχείο HTML ",
                    "script": "Πλήρες script block ",
                    "handler": "Πλήρης event handler ",
                }.get(row["kind"], "Πλήρες τμήμα ")
                notice = data.get("analysis", {}).get("notice", "")
                return row["first"], row["last"], data, label + row["name"] + (" · " + notice if notice else "")
            matches = [b for b in data["blocks"] if b[0] <= anchor <= b[1]]
            if matches:
                block = min(matches, key=lambda b: b[1] - b[0])
                return block[0], block[1], data, "Πλήρες τμήμα " + block[2]
            if data["tree"] is not None:
                statements = [n for n in data["tree"].body if getattr(n, "lineno", total + 1) <= anchor <= getattr(n, "end_lineno", 0)]
                if statements:
                    node = statements[0]
                    return node.lineno, node.end_lineno, data, "Πλήρης εντολή Python"
            notice = data.get("analysis", {}).get("notice", "")
            return (
                max(1, anchor - 40),
                min(total, anchor + 40),
                data,
                "Παράθυρο γραμμών: δεν αναγνωρίστηκε πλήρης συνάρτηση" + (" · " + notice if notice else ""),
            )
        first = max(1, int(args.get("start") or 1))
        last = min(total, int(args["end"]) if args.get("end") else total, first + READ_LINES - 1)
        return first, last, data, ""

    def outline(self, path, label=None, limit=2400):
        data = self.load(path)
        label = label or str(path)
        notice = data.get("analysis", {}).get("notice", "")
        if not data["blocks"]:
            return f"{label}: {len(data['lines'])} γραμμές — δεν εντοπίστηκαν ορισμοί δομής." + ("\n" + notice if notice else "")
        rows = [f"{label} — χάρτης ορισμών (υπογραφές, όχι σώματα συναρτήσεων):"]
        if notice:
            rows.append(notice)
        for first, last, name, header in data["blocks"]:
            row = f"{first}-{last} | {name}: {header}"
            if sum(len(r) + 1 for r in rows) + len(row) > limit:
                rows.append("… επιπλέον ορισμοί· ζήτησε outline για το συγκεκριμένο αρχείο.")
                break
            rows.append(row)
        return "\n".join(rows)


SOURCE_INDEX = SourceIndex()
