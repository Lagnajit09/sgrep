from pathlib import Path

from ..models import Chunk


def _read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    except OSError:
        return None


def chunk_file(path, root, window=60, overlap=10):
    """Slice one file into overlapping line windows (universal, language-agnostic)."""
    text = _read_text(Path(path))
    if text is None:
        return []
    lines = text.splitlines()
    n = len(lines)
    if n == 0:
        return []
    rel = Path(path).relative_to(root).as_posix()
    step = max(1, window - overlap)
    chunks = []
    i = 0
    while i < n:
        seg = lines[i:i + window]
        body = "\n".join(seg)
        if body.strip():
            chunks.append(
                Chunk(file=rel, start_line=i + 1, end_line=min(i + window, n), text=body)
            )
        i += step
    return chunks


def chunk_files(files, root, window=60, overlap=10):
    out = []
    for f in files:
        out.extend(chunk_file(f, root, window=window, overlap=overlap))
    return out
