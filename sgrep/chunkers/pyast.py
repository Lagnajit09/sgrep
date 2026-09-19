import ast
from pathlib import Path

from ..models import Chunk

_MAX_WHOLE = 80  # classes bigger than this are split into per-method chunks


def _start_with_decorators(node):
    start = node.lineno
    for d in getattr(node, "decorator_list", []):
        start = min(start, d.lineno)
    return start


def chunk_file(path, root):
    """Chunk a Python file into function / class units via the stdlib `ast`.

    Returns None on a syntax/read error so the caller can fall back to windows.
    """
    path = Path(path)
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None

    rel = path.relative_to(root).as_posix()
    lines = src.split("\n")
    out = []

    def add(start, end, context=None):
        body = "\n".join(lines[start - 1:end])
        if context:
            body = f"# {context}\n{body}"
        if body.strip():
            out.append(Chunk(file=rel, start_line=start, end_line=end, text=body))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(_start_with_decorators(node), node.end_lineno)
        elif isinstance(node, ast.ClassDef):
            start = _start_with_decorators(node)
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            span = node.end_lineno - start + 1
            if not methods or span <= _MAX_WHOLE:
                add(start, node.end_lineno)
            else:
                add(start, _start_with_decorators(methods[0]) - 1)  # class header
                for m in methods:
                    add(_start_with_decorators(m), m.end_lineno, context=f"class {node.name}")

    return out or None
