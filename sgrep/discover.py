from fnmatch import fnmatch
from pathlib import Path

DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", "target", ".mypy_cache", ".pytest_cache",
    ".idea", ".vscode", "vendor", "coverage", ".cache", "out", ".gradle",
}

DEFAULT_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb",
    ".php", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".kt", ".swift",
    ".scala", ".m", ".mm", ".sh", ".bash", ".sql", ".vue", ".svelte",
    ".dart", ".lua", ".ex", ".exs",
}


def discover_files(root, exts=None, include=None, exclude=None, max_bytes=1_000_000):
    """Return source files under `root`, filtering ignored dirs, size, and globs."""
    root = Path(root)
    exts = exts or DEFAULT_EXTS
    files = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if set(rel_parts[:-1]) & DEFAULT_IGNORE_DIRS:
            continue
        rel = p.relative_to(root).as_posix()
        if exclude and any(fnmatch(rel, pat) for pat in exclude):
            continue
        if include:
            if not any(fnmatch(rel, pat) for pat in include):
                continue
        elif p.suffix.lower() not in exts:
            continue
        try:
            if p.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        files.append(p)
    return files
