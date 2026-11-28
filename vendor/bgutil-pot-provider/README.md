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

## Local changes on top of upstream

The vendored copy is **not** byte-identical to the tag. Upstream had no patched release, so the
locks were bumped here (2026-11-28, backlog #29, osv-scanner):

- `package.json` `overrides.basic-ftp` 6.2.1 → 6.2.4 (also mirrored in `deno.lock`'s `workspace`
  block).
- Transitive patch bumps in **both** `package-lock.json` and `deno.lock`: `body-parser` 2.4.0,
  `follow-redirects` 1.16.3, `undici` 7.30.2, `qs` 6.17.1, `ip-address` 10.7.7, `basic-ftp` 6.2.4.
- Not fixed: `minimatch` 10.2.6 (GHSA-g3hv-75wg-m249). The fix is 11.x, outside eslint's `^10`
  range. eslint is a dev dependency the app never runs, so the advisory cannot be reached.

`deno.lock` is the one that matters at runtime: `setup_pot_provider.py` runs
`deno install --frozen`, which never reads `package-lock.json`. Scanners read `package-lock.json`,
so keep the two in step. Recipe: change `package.json`, then run
`npm install --package-lock-only --ignore-scripts` and
`npm update <pkgs> --package-lock-only --ignore-scripts`. Copy each bumped version's key and
`integrity` into `deno.lock`. Deno refers to a single-version package by bare name, so only the
entry key changes. A clean `deno install --frozen` (rc 0, lock left unchanged) proves the edit is
consistent.

On the next tag bump (below), replacing `server/` drops these changes. Re-scan with
`osv-scanner scan source --lockfile server/package-lock.json` and reapply only what is still
flagged.

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
