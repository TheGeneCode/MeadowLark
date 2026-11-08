# Vendored bgutil PO-token provider (server script)

This directory vendors the **server-script source** of
[bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider),
used in "script-deno" mode to mint the per-video GVS PO token that YouTube requires
for 1080p+ downloads (yt-dlp #12482).

- **Upstream:** https://github.com/Brainicism/bgutil-ytdlp-pot-provider
- **Pinned tag:** `2.0.2` — must match the `bgutil-ytdlp-pot-provider` version pinned in
  `pyproject.toml` / `uv.lock` (the plugin's `_check_version` hard-fails on a major mismatch).
- **License:** GPL-3.0 — see [LICENSE](LICENSE) (copied verbatim from upstream at the pinned tag).

Only the text source is vendored (`src/`, `deno.json`, `deno.lock`, `package.json`,
`package-lock.json`, `tsconfig.json`, etc.). The installed `node_modules/` is **generated**, not committed — run:

```
uv run python scripts/setup_pot_provider.py
```

once after `uv sync` to populate `server/node_modules/` via `deno install`. `src/config.py`
points `POT_PROVIDER_SERVER_HOME` here (dev) or at the bundled `bgutil-server` dir (frozen).

## Updating the pin

`tests/test_pot_provider_pin.py` fails when the plugin, this directory, the pin above, or the
Deno-minimum mirror drift apart. Dependabot opens bgutil bumps as a PR of their own (excluded from
the Python group), so do the steps below on that PR's branch.

1. Replace the vendored source. Clone the new tag, then remove the tracked `server/` files and copy
   the clone's `server/` in, so files upstream deleted are deleted here too (gitignored
   `node_modules/` stays):
   ```
   git clone --depth 1 --branch <tag> https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git <clone-dir>
   git rm -r -q vendor/bgutil-pot-provider/server
   cp -r <clone-dir>/server/. vendor/bgutil-pot-provider/server/
   git add vendor/bgutil-pot-provider/server
   ```
   Compare `LICENSE` against the clone's (ignoring line endings) and update the **Pinned tag** line
   above.
2. Bump the floor in `pyproject.toml`, run `uv lock --upgrade-package bgutil-ytdlp-pot-provider`,
   then `uv sync`.
3. **Delete** `server/node_modules` and run `uv run python scripts/setup_pot_provider.py`. Do not
   use `--force`: `deno install` does not prune packages the new lock dropped, and
   `scripts/collect_licenses.py` walks `node_modules/.deno`, so stale packages would ship and get
   licence-listed.
4. Diff the plugin's `getpot_bgutil.py` and `getpot_bgutil_script.py` between the old and new tags
   and update the mirrors in `src/pot_provider.py` (`_DENO_MIN_VERSION`, `build_warm_cmd`). Raise
   the `deno` floor in `pyproject.toml` if the plugin's minimum moved past it.
5. Run `uv run pytest tests/test_pot_provider_pin.py`.
6. Restart the app, then do a real, full 1080p download with `VID_DL_YTDLP_VERBOSE=true` and check
   the "Invoking http downloader" URL for `pot=`. Never use `--test`: a 10 KB probe passes while
   real downloads 403.
