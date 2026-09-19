from pathlib import Path

from . import pyast, treesitter
from .window import chunk_file as _window_chunk_file


def _chunk_one(f, root, window, overlap):
    ext = Path(f).suffix.lower()
    chunks = None
    if ext == ".py":
        chunks = pyast.chunk_file(f, root)
    elif treesitter.supported(ext):
        chunks = treesitter.chunk_file(f, root)
    if not chunks:
        chunks = _window_chunk_file(f, root, window=window, overlap=overlap)
    return chunks


def chunk_files(files, root, window=60, overlap=10, cache=None):
    """Structure-aware chunking with a universal fallback and an optional per-file cache.

    - `.py`            -> stdlib `ast` (function / class units)
    - `.js/.ts/.tsx/.java` -> tree-sitter (function / class units, route handlers)
    - everything else, or on any parse/dependency failure -> line windows
    """
    out = []
    for f in files:
        p = Path(f)
        rel = p.relative_to(root).as_posix()
        try:
            st = p.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = (0, 0)
        chunks = cache.get(rel, sig) if cache else None
        if chunks is None:
            chunks = _chunk_one(f, root, window, overlap)
            if cache:
                cache.put(rel, sig, chunks)
        out.extend(chunks)
    return out
