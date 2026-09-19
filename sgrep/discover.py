import os
from fnmatch import fnmatch
from pathlib import Path

DEFAULT_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build",
    ".next", "target", ".mypy_cache", ".pytest_cache", ".idea", ".vscode", "vendor",
    "coverage", ".cache", "out", ".gradle", "generated", "__generated__", ".turbo",
    ".svelte-kit", ".angular", "storybook-static", ".output", ".parcel-cache", "bin", "obj",
}

DEFAULT_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb", ".php",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".kt", ".swift", ".scala", ".m",
    ".mm", ".sh", ".bash", ".sql", ".vue", ".svelte", ".dart", ".lua", ".ex", ".exs",
}

IGNORE_FILE = ".sgrepignore"
_MINIFIED_LINE = 5000  # a single line longer than this ⇒ minified/generated ⇒ skip


def load_ignore_patterns(root):
    """Read gitignore-style patterns from `.sgrepignore` in the scan root and cwd."""
    patterns = []
    seen = set()
    for base in (Path(root), Path.cwd()):
        f = base / IGNORE_FILE
        if f in seen or not f.exists():
            continue
        seen.add(f)
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line.rstrip("/"))
        except OSError:
            pass
    return patterns


def _matches(rel, parts, patterns):
    for pat in patterns:
        if "/" in pat:
            if fnmatch(rel, pat) or fnmatch(rel, pat + "/*"):
                return True
        elif any(fnmatch(p, pat) for p in parts):
            return True
    return False


def _looks_generated(path):
    """Cheap check for minified/generated/binary files (skip them before chunking)."""
    try:
        with open(path, "rb") as fh:
            sample = fh.read(65536)
    except OSError:
        return True
    if b"\x00" in sample:
        return True
    longest = max((len(x) for x in sample.split(b"\n")), default=0)
    return longest > _MINIFIED_LINE


def discover_files(root, exts=None, include=None, exclude=None, max_bytes=1_000_000, ignore_patterns=None):
    """Return source files under `root`, filtering ignored dirs, size, globs,
    `.sgrepignore` patterns, and minified/generated files.

    Uses os.walk with in-place dir pruning so it never descends into node_modules,
    .git, etc. — critical on big JS monorepos where rglob would list 100k+ files.
    """
    root = Path(root)
    exts = exts or DEFAULT_EXTS
    ignore_patterns = ignore_patterns or []
    dir_ignores = {p for p in ignore_patterns if "/" not in p and not any( c in p for c in "*?[")}
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in DEFAULT_IGNORE_DIRS and d not in dir_ignores and not d.startswith(".")
        ]
        for fn in filenames:
            full = Path(dirpath) / fn
            parts = full.relative_to(root).parts
            rel = "/".join(parts)
            if exclude and any(fnmatch(rel, pat) for pat in exclude):
                continue
            if ignore_patterns and _matches(rel, parts, ignore_patterns):
                continue
            if include:
                if not any(fnmatch(rel, pat) for pat in include):
                    continue
            elif full.suffix.lower() not in exts:
                continue
            try:
                if full.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            if _looks_generated(full):
                continue
            files.append(full)
    return files
