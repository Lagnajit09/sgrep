<!-- GitHub Release body for v0.2.1.
     Suggested title: sgrep v0.2.1 — run it from anywhere (global config + cache) + URL fix
     Create with:  gh release create v0.2.1 -F RELEASE_NOTES.md -t "sgrep v0.2.1 — run it from anywhere" -->

A small release that makes **sgrep** behave like a properly installed tool — set your key
once, run it from any directory, and never leave cache files in the repo you're scanning.
Builds on 0.2.0 (batch queries + warm daemon).

### ✨ What's new

- 🔑 **Global API-key config** — set `TYPESAFE_API_KEY` once in `~/.config/sgrep/.env`
  (`%APPDATA%\sgrep\.env` on Windows) and use `sgrep` from anywhere. Resolution order:
  real env var → `./.env` → global config. (The scanned repo's own `.env` is never read.)
- 🗂️ **Global, per-repo cache** — caches now live in `~/.cache/sgrep/<repo>-<hash>/`
  (`%LOCALAPPDATA%\sgrep\...` on Windows), keyed by the scanned path. They no longer land
  in your cwd or the target repo, and are reused no matter where you run sgrep. Override
  with `SGREP_CACHE_DIR`.
- 🏷️ **`sgrep --version`**.
- 🩹 **Fixed the install URL** in the docs and package metadata — it now points at the real
  repo, `github.com/Lagnajit09/sgrep`.

### 📦 Install
```bash
# straight from GitHub (no PyPI needed); installed command is `sgrep`:
pipx install "git+https://github.com/Lagnajit09/sgrep@v0.2.1"
```

### ⬆️ Upgrade from 0.2.0
```bash
pipx install --force "git+https://github.com/Lagnajit09/sgrep@v0.2.1"
```

### ⬆️ Upgrading behavior note
Caches moved out of the working directory. Any old `.sgrep-cache.json` /
`.sgrep-chunkcache.json` left in a repo or cwd from 0.2.0 are now unused and safe to
delete. Fully backward compatible otherwise.

### ⚠️ Known limitations
- Needs a Jev API key (TypeSafe). The Vercel free tier is heavily rate-limited.
- Benchmark numbers (see `BENCHMARK.md`) are n=1 per cell — directional, not ±1%.

---
**Full changelog:** see [`CHANGELOG.md`](CHANGELOG.md). 🎉
