# Vendored bgutil PO-token provider (server script)

This directory vendors the **server-script source** of
[bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider),
used in "script-deno" mode to mint the per-video GVS PO token that YouTube requires
for 1080p+ downloads (yt-dlp #12482).

- **Upstream:** https://github.com/Brainicism/bgutil-ytdlp-pot-provider
- **Pinned tag:** `1.3.1` — must match the `bgutil-ytdlp-pot-provider` version pinned in
  `pyproject.toml` / `uv.lock` (the plugin's `_check_version` hard-fails on a major mismatch).
- **License:** GPL-3.0 — see [LICENSE](LICENSE) (copied verbatim from upstream at the pinned tag).

Only the text source is vendored (`src/`, `deno.lock`, `package.json`, `package-lock.json`,
`tsconfig.json`, etc.). The installed `node_modules/` is **generated**, not committed — run:

```
uv run python scripts/setup_pot_provider.py
```

once after `uv sync` to populate `server/node_modules/` via `deno install`. `src/config.py`
points `POT_PROVIDER_SERVER_HOME` here (dev) or at the bundled `bgutil-server` dir (frozen).

## Updating the pin

Re-clone the new tag's `server/` directory over this one (excluding `node_modules/`,
`build/`, `.git/`), bump the pin in `pyproject.toml`, run `uv lock`, and re-run
`scripts/setup_pot_provider.py --force`.
