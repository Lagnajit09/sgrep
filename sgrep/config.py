import hashlib
import os
import sys
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


def _platform_dir(kind: str) -> Path:
    """Per-OS base dir for 'config' or 'cache'. No external deps (platformdirs-style)."""
    home = Path.home()
    if sys.platform == "win32":
        var = "LOCALAPPDATA" if kind == "cache" else "APPDATA"
        base = os.environ.get(var)
        base = Path(base) if base else home / "AppData" / ("Local" if kind == "cache" else "Roaming")
        return base / "sgrep"
    xdg = os.environ.get("XDG_CACHE_HOME" if kind == "cache" else "XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "sgrep"
    if sys.platform == "darwin":
        return home / "Library" / ("Caches" if kind == "cache" else "Application Support") / "sgrep"
    return home / (".cache" if kind == "cache" else ".config") / "sgrep"


def global_config_dir() -> Path:
    """~/.config/sgrep (or OS equivalent). Holds a global .env for the API key."""
    return _platform_dir("config")


def load_config(cwd: Path) -> None:
    """Load API keys / settings. Precedence (highest first):

    1. real OS environment (already set — never overridden)
    2. <cwd>/.env            (project-local)
    3. <global config>/.env  (~/.config/sgrep/.env — set the key once, use everywhere)

    setdefault means the first value to appear wins, so this order falls out naturally.
    The scanned repo's own .env is deliberately NOT loaded (it could hold unrelated
    secrets and isn't ours to read).
    """
    load_env(cwd)
    load_env(global_config_dir())


def cache_dir_for(root: Path) -> Path:
    """Where to keep caches for a scan of `root`.

    Global by default (never pollutes the scanned repo or the cwd) and keyed by the
    resolved repo path, so the cache is reused no matter which directory you invoke
    sgrep from, and different repos stay isolated. Override the base with
    SGREP_CACHE_DIR (e.g. for CI). Returns an existing directory.
    """
    override = os.environ.get("SGREP_CACHE_DIR")
    base = Path(override) if override else _platform_dir("cache")
    root = Path(root).resolve()
    key = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12]
    d = base / f"{root.name}-{key}"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return base
    return d
