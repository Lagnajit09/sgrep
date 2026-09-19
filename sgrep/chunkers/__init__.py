from pathlib import Path

from . import pyast, treesitter
from .window import chunk_file as _window_chunk_file


def chunk_files(files, root, window=60, overlap=10):
    """Structure-aware chunking with a universal fallback.

    - `.py`            -> stdlib `ast` (function / class units)
    - `.js/.ts/.tsx/.java` -> tree-sitter (function / class units)
    - everything else, or on any parse/dependency failure -> line windows
    """
    out = []
    for f in files:
        ext = Path(f).suffix.lower()
        chunks = None
        if ext == ".py":
            chunks = pyast.chunk_file(f, root)
        elif treesitter.supported(ext):
            chunks = treesitter.chunk_file(f, root)
        if not chunks:
            chunks = _window_chunk_file(f, root, window=window, overlap=overlap)
        out.extend(chunks)
    return out
