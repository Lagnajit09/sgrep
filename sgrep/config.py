import os
from pathlib import Path


def load_env(root: Path) -> None:
    """Load KEY=VALUE lines from <root>/.env into os.environ.

    Existing environment variables win (setdefault), so an explicitly exported
    key is never overwritten by the file. Quotes and comments are handled.
    """
    env_path = Path(root) / ".env"
    if not env_path.exists():
        return
    try:
        raw = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)
