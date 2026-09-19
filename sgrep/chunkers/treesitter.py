from pathlib import Path

from ..models import Chunk

_LANG_BY_EXT = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".java": "java",
}

# Node types (verified against the real grammars).
_FUNC_TYPES = {
    "function_declaration", "generator_function_declaration", "function_signature",
    "method_definition", "method_declaration", "constructor_declaration",
}
_CLASS_TYPES = {"class_declaration", "abstract_class_declaration"}
_WHOLE_TYPES = {"interface_declaration", "enum_declaration", "type_alias_declaration"}
_DECL_TYPES = {"lexical_declaration", "variable_declaration"}  # const Foo = () => ...
_FUNC_VALUE_TYPES = {"arrow_function", "function_expression"}
# Express/Node route + middleware registrations: router.post(...), app.get(...), etc.
_ROUTE_VERBS = {"get", "post", "put", "patch", "delete", "options", "head", "all", "use", "route"}

_MAX_WHOLE = 80
_parsers = {}


def supported(ext):
    return ext.lower() in _LANG_BY_EXT


def _get_parser(lang):
    if lang not in _parsers:
        from tree_sitter_language_pack import get_parser  # optional dependency
        _parsers[lang] = get_parser(lang)
    return _parsers[lang]


def _contains_func_value(node):
    if node.type in _FUNC_VALUE_TYPES:
        return True
    return any(_contains_func_value(c) for c in node.named_children)


def _route_call(node):
    """If `node` is an expression statement that registers a route/handler,
    return its call node; else None. Captures `router.post("/x", handler)` and
    `app.get("/y", (req,res) => {...})` — the core Node/Express pattern.
    """
    call = next((c for c in node.named_children if c.type == "call_expression"), None)
    if call is None:
        return None
    fn = call.child_by_field_name("function")
    if fn is not None and fn.type == "member_expression":
        prop = fn.child_by_field_name("property")
        if prop is not None and prop.text.decode() in _ROUTE_VERBS:
            return call
    return call if _contains_func_value(call) else None


def _find_body(node):
    b = node.child_by_field_name("body")
    if b is not None:
        return b
    for c in node.named_children:
        if c.type.endswith("_body"):
            return c
    return None


def chunk_file(path, root):
    """Chunk a JS/TS/TSX/Java file into function / class units via tree-sitter.

    Returns None if tree-sitter is unavailable or parsing fails, so the caller
    falls back to window chunking.
    """
    path = Path(path)
    lang = _LANG_BY_EXT.get(path.suffix.lower())
    if lang is None:
        return None
    try:
        parser = _get_parser(lang)
        src = path.read_text(encoding="utf-8")
        tree = parser.parse(bytes(src, "utf8"))
    except Exception:  # noqa: BLE001 -- missing dep / read / parse error -> fallback
        return None

    rel = path.relative_to(root).as_posix()
    lines = src.split("\n")
    out = []

    def add(start0, end0, context=None):
        body = "\n".join(lines[start0:end0 + 1])
        if context:
            body = f"// {context}\n{body}"
        if body.strip():
            out.append(Chunk(file=rel, start_line=start0 + 1, end_line=end0 + 1, text=body))

    def handle_class(node):
        name = node.child_by_field_name("name")
        cname = name.text.decode() if name else "?"
        body = _find_body(node)
        methods = [c for c in body.named_children if c.type in _FUNC_TYPES] if body else []
        span = node.end_point[0] - node.start_point[0] + 1
        if not methods or span <= _MAX_WHOLE:
            add(node.start_point[0], node.end_point[0])
            return
        add(node.start_point[0], max(node.start_point[0], methods[0].start_point[0] - 1))  # header
        for m in methods:
            add(m.start_point[0], m.end_point[0], context=f"class {cname}")

    def process(node):
        t = node.type
        if t == "export_statement":
            for c in node.named_children:
                process(c)
        elif t in _FUNC_TYPES or t in _WHOLE_TYPES:
            add(node.start_point[0], node.end_point[0])
        elif t in _CLASS_TYPES:
            handle_class(node)
        elif t in _DECL_TYPES and _contains_func_value(node):
            add(node.start_point[0], node.end_point[0])
        elif t == "expression_statement" and _route_call(node) is not None:
            add(node.start_point[0], node.end_point[0])

    for child in tree.root_node.named_children:
        process(child)

    return out or None
