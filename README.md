# MeadowLark

[![Latest release](https://img.shields.io/github/v/release/TheGeneCode/MeadowLark)](https://github.com/TheGeneCode/MeadowLark/releases)
[![CI](https://github.com/TheGeneCode/MeadowLark/actions/workflows/ci.yml/badge.svg)](https://github.com/TheGeneCode/MeadowLark/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/github/license/TheGeneCode/MeadowLark)](LICENSE)

A simple desktop app for downloading videos, playlists, and podcasts from YouTube and other sites. Built on top of [yt-dlp](https://github.com/yt-dlp/yt-dlp).

![MeadowLark main window](docs/screenshot.png)

> MeadowLark is meant for personal use — content you own the rights to, or that's public domain, Creative Commons, or otherwise fair to save for yourself. Respect copyright and the terms of the sites you download from. Not affiliated with YouTube, Google, or the yt-dlp project.

💛 [Sponsor this project](https://github.com/sponsors/TheGeneCode) — and consider tipping the yt-dlp [maintainers](https://github.com/yt-dlp/yt-dlp/blob/master/Maintainers.md#maintainers) that do the work that this is built on, too.

---

## Table of Contents

- [What MeadowLark Does](#what-meadowlark-does)
- [Features at a Glance](#features-at-a-glance)
- [Installation](#installation)
- [First Launch](#first-launch)
- [How to Use](#how-to-use)
  - [Downloading a Single Video or Audio File](#downloading-a-single-video-or-audio-file)
  - [Downloading a Playlist](#downloading-a-playlist)
  - [Downloading Podcasts](#downloading-podcasts)
  - [Live Videos](#live-videos)
- [Settings Reference](#settings-reference)
  - [Resolutions Tab](#resolutions-tab)
  - [Downloads Tab](#downloads-tab)
  - [Playlists Tab](#playlists-tab)
  - [Interface Tab](#interface-tab)
  - [Automation Tab](#automation-tab)
- [cookies.txt — What It Is and How to Get One](#cookiestxt--what-it-is-and-how-to-get-one)
- [Download History](#download-history)
- [Failed Downloads](#failed-downloads)
- [Pending Downloads](#pending-downloads)
- [Updates](#updates)
- [Developer Setup](#developer-setup)
- [Environment Variable Reference](#environment-variable-reference)

---

## What MeadowLark Does

MeadowLark lets you save videos and audio from YouTube (and hundreds of other sites) directly to your computer. You drag a URL onto the app and the file lands in your chosen folder. That's it.

It also handles:
- Bulk playlist downloads at a scheduled interval
- Podcast feeds saved as audio files
- Age-restricted videos (when you supply a cookies.txt from your browser)
- Automatically skipping videos you've already downloaded

---

## Features at a Glance

| Feature | What it does |
|---|---|
| **Drag-and-drop downloads** | Drop a URL onto any enabled resolution zone (2160 / 1440 / 1080 / 720 / 480 / 360) or the Audio zone to start downloading immediately |
| **Playlist downloader** | Point the app at a text file of playlist URLs; it downloads new entries on a schedule |
| **Podcast mode** | Downloads audio-only (m4a/mp3) and skips anything shorter than 3 minutes |
| **Archive / skip already-downloaded** | Keeps a log so videos are never downloaded twice |
| **SponsorBlock integration** | Skips sponsor segments when downloading podcasts |
| **Mark as watched** | Optionally tells YouTube a video is watched after you download it |
| **Live video queue** | Queues live streams and retries them automatically until they're available |
| **Pending download list** | Surfaces premieres/live-streams not yet available on a ⏳ button; force-download now or remove |
| **Failed download list** | Surfaces failures on a ⚠ button; review why each one failed and retry or dismiss it |

---

## Installation

> **Windows 10/11 only.**

1. Go to the [Releases page](https://github.com/TheGeneCode/MeadowLark/releases) and download `MeadowLark-Setup-{version}.exe`.
2. Double-click the installer and follow the wizard. You can optionally create a desktop shortcut during setup.
3. Launch MeadowLark from the Start Menu or your desktop shortcut.

> **Seeing a blue "Windows protected your PC" screen?** That's Microsoft SmartScreen — it flags any new installer without an expensive code-signing certificate, not a sign of a problem. Click **More info**, then **Run anyway** to continue.

---

## First Launch

The first time you open MeadowLark, a short setup wizard appears and asks two questions:

1. **Where should videos be saved?** — defaults to your `Videos` folder.
2. **Where should podcast episodes be saved?** — defaults to `Music\Podcasts`.

Pick your folders and click **OK**. The app remembers these choices in `AppData\Roaming\MeadowLark` and you won't be asked again. You can change them any time in **Settings → Downloads**.

---

## How to Use

### Downloading a Single Video or Audio File

1. Drag the URL from your browser's address bar and drop it onto one of the drop zones in the app window: one per enabled resolution (2160/1440/1080/720/480/360), plus **audio** which extracts audio only and saves as m4a. Each zone downloads the best available quality **at or below** its number, so a video that only exists at a lower resolution still downloads rather than being skipped — and if a resolution is blocked, the download automatically retries at the next lower enabled preset.
2. The status bar at the bottom shows download progress. When it says **[ Ready ]** again, the file is in your folder.

### Queuing
MeadowLark queues tasks automatically — keep dragging/dropping without waiting. Videos and playlists download in order. Audio (podcasts/playlists) downloads concurrently while videos process, so you won't block on podcast queues.

### Downloading a Playlist

MeadowLark can batch-download entire YouTube playlists. It tracks which videos it has already downloaded and skips them on future runs.

**Set up a playlist file:**

1. Open Notepad and add one YouTube playlist URL per line. Lines starting with `#` are treated as comments and skipped.

   ```
   # My tech videos
   https://www.youtube.com/playlist?list=PLxxxxxxxxxxxxxxxx

   # Gaming channel
   https://www.youtube.com/playlist?list=PLyyyyyyyyyyyyyyyy
   ```

2. Save the file as `playlists.txt` (or any name you like).

3. In MeadowLark, open **Settings → Playlists** and use the **Browse…** button next to the relevant playlist file:
   - **Playlists file (1080p)** — full quality video
   - **Playlists file (720p)** — medium quality video
   - **Playlists file (audio)** — audio only

4. Click **Apply** and then use the matching button in the main window (**Playlists**, **720 Playlists**, or **YT Podcasts**) to run a download.

### Downloading Podcasts

Podcast mode works just like the playlist downloader but saves audio files and filters out anything shorter than 3 minutes (so shorts and trailers are skipped automatically).

1. Add YouTube channel or podcast playlist URLs to your audio playlist file (see above).
2. Enable **Automation → Auto-check podcasts** if you want the app to check for new episodes on a schedule without you clicking anything.
3. New episodes land in your **Audio directory** (default: `Music\Podcasts`).

### Live Videos

If a video is currently live (not yet archived), MeadowLark adds it to an internal queue and retries it every 30 minutes until the stream has ended and a recording is available. No action is required from you — just drop the URL and forget it.

---

## Settings Reference

Open Settings from the menu or toolbar. Click **Apply** to save any change.

### Resolutions Tab

| Setting | What it does |
|---|---|
| **2160 / 1440 / 1080 / 720 / 480 / 360** | One checkbox per resolution rung. Checked rungs are the ones offered elsewhere in the app; at least one must stay checked, or Apply refuses the change and warns instead. |

Each preset downloads the best available quality at or below its height — a video that only exists at a lower resolution still downloads, it is not skipped. If a resolution is blocked by YouTube, the app automatically retries at the next lower *enabled* preset. Disabling a rung here doesn't erase its playlist file or labels; it only hides its row (marked `(hidden)`) on the Playlists and Interface tabs so you can configure it before turning it on.

### Downloads Tab

| Setting | What it does |
|---|---|
| **Video directory** | Folder where downloaded videos are saved |
| **Audio directory** | Folder where podcast/audio files are saved |
| **Video format** | Container for video files: `mp4` (widest compatibility), `mkv`, or `webm` |
| **Audio format** | Format for audio files: `m4a` (recommended), `mp3`, `opus`, `flac`, or `wav` |
| **Mark watched on YouTube** | After a video downloads, automatically marks it as watched in your YouTube account. Requires a [cookies.txt](#cookiestxt--what-it-is-and-how-to-get-one) file with an active login. |

### Playlists Tab

| Setting | What it does |
|---|---|
| **Playlists file (2160p / 1440p / 1080p / 720p / 480p / 360p)** | Text file containing YouTube playlist URLs to download at that resolution. All six rungs are listed regardless of whether they're enabled on the Resolutions tab — a disabled rung's row is marked `(hidden)` but still configurable. |
| **Playlists file (audio)** | Same, but downloads audio only (podcast mode) |
| **Cookies.txt** | Path to your browser cookies export. Used for age-restricted or account gated (premium) videos and the "Mark watched" feature. See [below](#cookiestxt--what-it-is-and-how-to-get-one). |

> The playlist files are copied into AppData automatically when you browse and apply, so the originals can be moved or deleted.

### Interface Tab

| Setting | What it does |
|---|---|
| **Drop label — 2160/1440/1080/720/480/360/audio** | The text shown on each drop zone. Cosmetic only; doesn't change behavior. All six resolution rungs are listed regardless of whether they're enabled on the Resolutions tab; a disabled rung's row is marked `(hidden)`. |
| **Ready text** | Status bar text shown when the app is idle |
| **Button labels** | Rename any of the playlist/podcast buttons, one per resolution rung plus podcasts |
| **Always on top** | Keeps the MeadowLark window above all other windows |
| **Auto-check for app updates** | Checks GitHub for a new release once a week at startup and asks if you'd like any available update. Uncheck to opt out. |

### Automation Tab

| Setting | What it does |
|---|---|
| **Auto-check podcasts** | When on, the app automatically checks your podcast playlist file for new episodes |
| **Check interval** | How often to check, in minutes (5–1440). Default is 60 minutes. |

---

## cookies.txt — What It Is and How to Get One

Some videos on YouTube, or other sites, are age-restricted or require a login. MeadowLark can use a **cookies.txt** file — an export of your browser's YouTube session — to download these as if you were logged in.

The same file is needed if you enable **Mark watched on YouTube**.

### What is a cookies.txt file?

It's a plain text file containing the login tokens from your browser's session. Think of it like a temporary pass that tells websites "this is me." It does not contain your password.

### How to export one

**Option A — Browser extension (easiest)**

1. Install the **Get cookies.txt LOCALLY** extension:
   - [Chrome / Edge](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - [Firefox](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)
2. Make sure you are logged into the sites you need an account for in that browser.
3. With the sites all open, click the extension icon, and click **Export**.
4. Save the file somewhere easy to find, e.g. `C:\Users\YourName\cookies.txt`.

**Option B — yt-dlp CLI (for advanced users)**

```powershell
yt-dlp --cookies-from-browser chrome --cookies cookies.txt --skip-download https://www.youtube.com
```

Replace `chrome` with `firefox`, `edge`, or `brave` as appropriate.

### Pointing MeadowLark at the file

1. Open **Settings → Playlists**.
2. Next to **Cookies.txt**, click **Browse…** and select the file you exported.
3. Click **Apply**.

> **Important:** MeadowLark reads the file in-place and does not copy it. If your browser extension keeps the file updated automatically (some do), MeadowLark will always use the latest version.

### Cookies expire

Browser cookies expire eventually (usually after a few weeks to a few months). If you start getting login errors or age-restriction errors, re-export a fresh cookies.txt and update the path in Settings.

---

## Download History

MeadowLark keeps two logs in its AppData folder:

- **history_log.txt** — a record of every successful download (title, URL, timestamp)
- **error_log.txt** — errors and failures

You can view recent history inside the app via the **History** menu item (if available in your version). The archive file (`archive.txt`) is what yt-dlp uses internally to skip already-downloaded videos; you normally don't need to touch it. There is an **Ignore Archive?** checkbox that will download a video you have previously downloaded, if you need. Be careful to uncheck it when you no longer need it, or you could accidentally download whole playlists you've already seen.

---

## Failed Downloads

When a download fails, MeadowLark records it instead of letting it scroll past in the log. A red **⚠ N** button appears in the top-right corner showing how many failures are waiting; it is hidden entirely when there are none.

Click it to open the **Failed Downloads** window, a list of every failed item with the time it failed, the site, the download type (the resolution preset, audio, or playlist), and the title. Hover any row to see the error message that caused the failure.

Select one or more rows (Ctrl-click, Shift-click, or Ctrl+A for all) and use — each action applies to every selected row, skipping any it can't handle:

- **Retry** — re-queues the download exactly as if you had dropped the URL again. Already-completed entries of a playlist are skipped via the archive, so only the failed parts download. If it fails a second time, it reappears in the list with a fresh timestamp.
- **Mark as Downloaded** — adds a YouTube video to the download archive so playlist scans stop retrying it (e.g. a private or deleted video), and removes it from the list.
- **Delete** — removes the item from the list without downloading it.
- **Right-click → Open in Browser** — opens each selected item's original URL so you can check whether the video still exists.

Retry is disabled for any record whose download type can no longer be recognised (for example, a record written by an older version); Delete still works on those.

The list lives in `failed_downloads.json` next to the app's other resources and survives restarts — if failures are pending when you close the app, the ⚠ button is there again at next launch.

---

## Pending Downloads

A **⏳ N** button appears in the top-right corner showing how many downloads are parked waiting to become available; it is hidden when there are none.

Click it to open the **Pending Downloads** window, a list of every parked item with its expected availability time, kind (`live` or `premiere`), download type, and title. Hover any row to see the error or reason why nothing has downloaded yet, or the URL.

Select a row and use:

- **Download Now** — force the item through the normal download pipeline immediately, ignoring its release time. If it's genuinely not available yet, it will simply re-park itself with a fresh release time.
- **Remove** — drop it from the list without downloading it.
- **Right-click → Open in Browser** — opens the original URL so you can check the video page.

The list lives in `pending_queue.json` next to the app's other resources and survives restarts — if pending downloads are waiting when you close the app, the ⏳ button is there again at next launch.

The app polls automatically every `VID_DL_LIVE_QUEUE_CHECK_INTERVAL_MINUTES` minutes (documented in the [Environment Variable Reference](#environment-variable-reference)).

---

## Updates

MeadowLark checks GitHub for new releases once a week at startup. When an update is found, a dialog appears with a download link.

To check manually: **Settings → About → Check for Updates**.

To turn off automatic checks: **Settings → Interface → Auto-check for app updates** (uncheck).

---

## Developer Setup

```sh
git clone https://github.com/TheGeneCode/MeadowLark
cd MeadowLark
uv sync
uv run python scripts/setup_pot_provider.py   # installs the vendored PO-token provider deps (needed for 1080p)
cp .env.example .env
git config core.hooksPath .githooks
uv run python meadowlark.pyw
```

> Skipping `setup_pot_provider.py` means 1080p downloads fail with HTTP 403 and the app shows a "PO Token Providers: none" warning at startup. See [YouTube 1080p Downloads](#youtube-1080p-downloads--the-po-token-provider).

### Prerequisites

- **Python ≥ 3.10** and **[uv](https://docs.astral.sh/uv/getting-started/installation/)**
- **FFmpeg** — required for audio/podcast downloads. Install via [ffmpeg.org](https://ffmpeg.org/download.html) or a package manager, and make sure it's on `PATH`.
- **Deno** — auto-installed into `.venv/Scripts` when you run `uv sync`.

---

## YouTube 1080p Downloads & the PO-Token Provider

YouTube gates 1080p (and higher) video streams behind a per-video **GVS PO token** (yt-dlp [#12482](https://github.com/yt-dlp/yt-dlp/issues/12482)). Without that token, the app can still fetch the metadata and *lower* resolutions, but every 1080p download fails at the media stage with:

```
unable to download video data: HTTP Error 403: Forbidden
```

MeadowLark mints the token with the **[bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)** plugin running in **script (Deno) mode**. The plugin is a pinned dependency (installed by `uv sync`), and the Deno runtime is auto-installed into `.venv/Scripts`. The provider's generate script is **vendored** into the repo under `vendor/bgutil-pot-provider/server` (pinned to the same version as the plugin); its Node dependencies (`node_modules`) are generated once by `scripts/setup_pot_provider.py`.

### The token is necessary but not sufficient: the ~1 MB cutoff

As of **August 2026** a valid token stopped being enough. With the `mweb` client, media URLs carry a correct `pot=`, the transfer starts, and YouTube then 403s it after roughly **one megabyte**. Measured on one video within a single minute, varying only the request:

| Request | Result |
| --- | --- |
| `--test` (10 KB range) | succeeds |
| full download | 403 |
| `--http-chunk-size` 10 KB / 256 KB / 1 MB | reaches ~1.00–1.07 MB, then 403 |
| `--http-chunk-size` 5 MB / 10 MB | 403 immediately |

This is SABR enforcement: YouTube expects its own streaming protocol rather than plain range GETs, and allows non-SABR clients only a small byte quota. The trap for debugging is that **any short-range probe passes** — a `--test` repro reports success while every real download fails.

`tv`, `ios`, `android`, `android_vr`, `web` and `web_safari` fail earlier still, exposing only SABR formats (`Requested format is not available`). **`web_embedded`** is the client measured to serve complete files — full downloads finish with no 403 or truncation — so it is the default (`VID_DL_YT_PLAYER_CLIENT`). Its predecessor `tv_embedded` had the same property but was dropped from yt-dlp's client registry (requesting it now just logs "Skipping unsupported client" and silently falls back to `tv_downgraded`, which YouTube broke for cookie-authenticated requests in August 2026 — [yt-dlp#17389](https://github.com/yt-dlp/yt-dlp/issues/17389)). Do **not** add `mweb` as a fallback: the first client supplying a format id wins, so it would take rungs `web_embedded` could have served and bring the cutoff back.

Upgrading yt-dlp does not help (the 2026.08.04 nightly fails identically), and neither cookies nor the user agent affect it — the gating is server-side and per client.

### What the app checks at startup

On launch MeadowLark probes the provider and, if anything is missing, shows a **"PO Token Providers: none"** warning naming the missing pieces. It looks for:

| Component | Where |
|---|---|
| Provider plugin | importable `yt_dlp_plugins.extractor.getpot_bgutil_script` (from `uv sync`) |
| Deno runtime ≥ 2.0 | `deno.exe` in `.venv/Scripts` (from `uv sync`) |
| Generate script | `{server_home}/src/generate_once.ts` |
| Script dependencies | `{server_home}/node_modules` |

`server_home` defaults to the vendored `vendor/bgutil-pot-provider/server` (dev) or the bundled `bgutil-server` dir (frozen build), and is overridable with the `VID_DL_POT_SERVER_HOME` environment variable.

### Setting up the provider server

> **Installer users don't need this.** The packaged build (from `installer\setup.iss`) bundles the Deno runtime, the provider plugin, and the generate script **with its `node_modules` already built** into the app's `bgutil-server` folder, so 1080p works out of the box. The steps below are only for running MeadowLark **from source**.

The generate script is already vendored in the repo, so setup is a single command:

```sh
uv run python scripts/setup_pot_provider.py
```

This runs `deno install` in `vendor/bgutil-pot-provider/server`, creating `node_modules` next to the vendored `src/generate_once.ts`, then **warms Deno's module cache** (see below). Run it once after `uv sync` — the `deno install` half is a no-op if `node_modules` already exists (unless you pass `--force`), while the warm-up runs every time and costs ~2s once warm. Then restart MeadowLark — the warning dialog should no longer appear, and 1080p downloads will succeed.

| Flag | Default | What it does |
|---|---|---|
| `--force` | off | Re-run `deno install` even when `node_modules` already exists |
| `--skip-warm` | off | Skip the Deno module-cache warm-up. Used by CI, where the runner's cache is thrown away at the end of the job |

The script exits `0` when the dependencies are in place, `2` when Deno or the vendored server dir cannot be found, and otherwise forwards `deno install`'s exit code. A failed warm-up is a **warning, not a failure** — it prints to stderr and still exits `0`.

> If you keep the provider somewhere else, point `VID_DL_POT_SERVER_HOME` at that `server` directory (the folder containing `src/generate_once.ts` and `node_modules`).

### The Deno module cache (`DENO_DIR`)

`node_modules` alone is not enough. Deno keeps a **second** cache — the npm registry payload and the transpiled TypeScript — under `DENO_DIR` (default `%LOCALAPPDATA%\deno`, ~63 MB once filled). It is not part of the repo and not shipped in the installer.

This matters because the plugin gives its script-version probe a hard **15-second budget**. Against a cold `DENO_DIR` that probe takes ~26s and blows straight through it, so the plugin reports itself unavailable, no PO token is minted, and the download 403s — with no error naming Deno at all. Once the cache is warm the same probe takes ~1.5s.

MeadowLark fills the cache for you, so there is normally nothing to do:

- **From source:** `scripts/setup_pot_provider.py` warms it at the end of the run.
- **Installer users:** the app warms it in a background thread at startup (the first launch pulls the ~63 MB; the UI stays responsive throughout, and later launches are a no-op).

A download queued before that warm-up finishes would race the plugin's 15-second probe and lose, so the download worker waits for the warm-up to complete before starting its first item — the log shows `Preparing downloader (warming Deno cache)...` while it does. On a first launch that wait can last as long as the download of the npm payload; every later launch passes straight through.

### Diagnosing a 403 that survives all of the above

Set `VID_DL_YTDLP_VERBOSE=true` to capture yt-dlp's own diagnostic stream to `resources/ytdlp_debug.log` (rotating, 5 MB). Without it the error log holds only the final exception, which cannot distinguish "no token was minted" from "a token was minted but the served format did not carry it". Four lines in the capture settle that:

| Line | Meaning |
| --- | --- |
| `[pot:bgutil:script-deno] Generating a gvs PO Token …` | the provider ran |
| `Retrieved a gvs PO Token for <client>` | which client obtained a token |
| `Downloading N format(s): <ids>` | which formats were selected |
| `Invoking http downloader` — is `pot=` in the URL? | **decisive**: no `pot=` means the format came from a client that does not consume the token, and it will 403 on any gated video |

> The capture contains PO tokens, visitor data and signed media URLs. It is gitignored; do not paste it into issues without redacting. Leave the setting off for normal use.

## Environment Variable Reference

Advanced users can override defaults by editing the `.env` file in `AppData\Roaming\MeadowLark\.env`. Most settings are easier to change through the Settings dialog.

| Variable | Default | Description |
|---|---|---|
| `VID_DL_VIDEO_STORAGE_DIR` | `~/Videos` | Video output directory |
| `VID_DL_ARCHIVE_PATH` | `resources/archive.txt` | yt-dlp download archive |
| `VID_DL_PODCAST_MISC_OUTPUT_DIR` | `~/Music/Podcasts` | Misc podcast output directory |
| `VID_DL_ERROR_LOG` | `error_log.txt` | Error log file path |
| `VID_DL_HISTORY_LOG` | `history_log.txt` | Download history log path |
| `VID_DL_RESOURCES_DIR` | `resources` | Resources directory |
| `VID_DL_VENV_SCRIPTS` | `.venv/Scripts` | Virtual environment Scripts directory |
| `VID_DL_HTTP_TIMEOUT` | `120` | yt-dlp HTTP timeout (seconds) |
| `VID_DL_SOCKET_TIMEOUT` | `120` | yt-dlp socket timeout (seconds) |
| `VID_DL_HTTP_REQUEST_TIMEOUT` | `5` | External API request timeout (seconds) |
| `VID_DL_MAX_FRAGMENT_RETRIES` | `10` | Max fragment retry attempts |
| `VID_DL_PODCAST_MIN_DURATION_SECONDS` | `180` | Minimum duration to count as a podcast |
| `VID_DL_SPONSORBLOCK_CACHE_TTL_HOURS` | `6` | SponsorBlock cache TTL |
| `VID_DL_LIVE_QUEUE_CHECK_INTERVAL_MINUTES` | `30` | Live queue polling interval |
| `VID_DL_PODCAST_LOOKAHEAD_MAX_ATTEMPTS` | `5` | Max lookahead attempts for podcast fetching |
| `VID_DL_MERGE_OUTPUT_FORMAT` | `mp4` | Merge output container format |
| `VID_DL_APP_UPDATE_AUTO_CHECK` | `true` | Check for a new app release once per week at startup (set to `false` to opt out) |
| `VID_DL_APP_UPDATE_LAST_CHECKED` | _(empty)_ | ISO date of the last automatic update check; written by the app, not normally set by hand |
| `VID_DL_MARK_WATCHED` | `false` | Auto-mark downloaded YouTube videos as watched via cookies session (requires valid `cookies.txt`) |
| `VID_DL_ENABLED_RESOLUTIONS` | `1080,720` | Comma-separated resolution rungs offered by the app. Registered rungs: `2160,1440,1080,720,480,360`. Unknown/malformed entries are dropped; an empty result falls back to the default pair. Editable from **Settings → Resolutions**. |
| `VID_DL_PLAYLISTS_<HEIGHT>_FILE` | see `.env.example` | Per-rung playlist file, e.g. `VID_DL_PLAYLISTS_2160_FILE`. 1080p and 720p keep their legacy names (`VID_DL_PLAYLISTS_FILE`, `VID_DL_PLAYLISTS_720_FILE`) for backward compatibility. |
| `VID_DL_LABEL_DROP_<HEIGHT>` | rung height, e.g. `2160` | Per-rung drop-zone label |
| `VID_DL_LABEL_BTN_<HEIGHT>` | see `.env.example` | Per-rung playlist button label. 1080p and 720p keep their legacy names (`VID_DL_LABEL_BTN_PLAYLISTS`, `VID_DL_LABEL_BTN_720`). |
| `VID_DL_POT_SERVER_HOME` | `vendor/bgutil-pot-provider/server` (dev) / bundled `bgutil-server` (frozen) | PO-token provider server home (bgutil script-deno mode); must contain `src/generate_once.ts` and `node_modules` (run `scripts/setup_pot_provider.py`). See [YouTube 1080p Downloads](#youtube-1080p-downloads--the-po-token-provider). |
| `VID_DL_YT_PLAYER_CLIENT` | `web_embedded` | YouTube player clients, comma-separated in priority order. See [the ~1 MB cutoff](#the-token-is-necessary-but-not-sufficient-the-1-mb-cutoff) before changing this — most clients now 403 mid-transfer or expose no downloadable formats. |
| `VID_DL_YTDLP_VERBOSE` | `false` | Capture yt-dlp's verbose output for diagnosing 403s. Contains PO tokens and signed URLs — leave off unless debugging. |
| `VID_DL_YTDLP_DEBUG_LOG` | `resources/ytdlp_debug.log` | Where that capture is written (rotating, 5 MB × 2 backups) |
