# MeadowLark Glossary

Shared vocabulary for this project. When in doubt, use these terms exactly as defined here.

---

## Quick Reference

| Term | One-line definition |
|------|---------------------|
| **Playlist** | A YouTube/web playlist URL containing a collection of Videos |
| **Podcast** | A Playlist configured for audio-only extraction; items are Episodes |
| **Playlist File** | A `.txt` file containing a list of Playlist URLs |
| **Label** | The `#Name` above a Playlist in a Playlist File; for Podcasts it is the shared output folder (category) |
| **Video** | An individual item within a video Playlist |
| **Episode** | An individual item within a Podcast |
| **Video Key** | The unique YouTube identifier from a URL (e.g. `Pyd3cOxuaQk` from `?v=Pyd3cOxuaQk`) |
| **Drop Target** | A colored UI box that accepts URL drops to trigger a download |
| **Resolution Preset** | One rung of the rendition ladder (e.g. 1080), identified by its height; owns a Drop Target, a Playlist File, and a colour |
| **Archive** | File tracking downloaded Video Keys to prevent re-downloading |
| **Live Queue** | File storing URLs of live/upcoming Videos awaiting download |
| **Source** | *(Code)* Routing key determining format, output dir, and filtering rules |
| **Entry** | *(Code)* yt-dlp dict for a single Video or Episode |
| **qmeta** | *(Code)* Metadata dict attached to a download for routing/logging |

---

## Full Definitions

### Playlist
A YouTube (or other web) playlist URL that groups a collection of Videos together. Identified by a playlist ID embedded in the URL (e.g. `youtube.com/playlist?list=PLxxxxxxxx`). A Playlist is the unit you add to a Playlist File.

**Podcast is a subset of Playlist** — all Podcasts are Playlists, but not all Playlists are Podcasts.

---

### Podcast
A Playlist configured for audio-only extraction. Podcasts live in the audio Playlist File (`audio playlists.txt`) and are processed with special filtering rules:
- Items are called **Episodes** (not Videos)
- Episodes shorter than 3 minutes are skipped
- Episodes with "(Update)" in the title are skipped
- SponsorBlock integration skips sponsor segments
- Output goes to `~/Music/Podcasts/`

---

### Playlist File
A `.txt` file that contains a list of Playlist URLs, one per line. A `#Name` line gives a **Label** to every Playlist below it, up to the next `#` line (a lone `#` clears it; a commented-out link does not). There are three Playlist Files:

| File | Quality | Contains |
|------|---------|----------|
| `playlists.txt` | 1080p | Video Playlists |
| `720playlists.txt` | 720p | Video Playlists |
| `audio playlists.txt` | audio only | Podcasts |

---

### Label
The name a Playlist gets from the nearest `#Name` line above it in a Playlist File. For Podcasts the Label is the output folder, so several Podcasts under one Label (a **category**) share a folder. For video Playlists it is only the fallback name for an `NA/` folder. Code: `PlaylistFileEntry.label`, `labels_by_playlist_id()`, `playlist_comments`.

---

### Video
An individual item within a video Playlist. A Video has a Video Key, a title, a duration, and an upload date. The code calls this an **Entry**.

---

### Episode
An individual item within a Podcast. An Episode has the same structure as a Video but is subject to podcast-specific filtering before download. The code calls this an **Entry**.

Use "Video" for items in video Playlists, "Episode" for items in Podcasts.

---

### Video Key
The unique character string that YouTube assigns to a video, found as the `v=` parameter in the URL.

```
https://www.youtube.com/watch?v=Pyd3cOxuaQk
                                ^^^^^^^^^^^
                                Video Key
```

The Archive stores Video Keys in the format `youtube <video_key>`. The code uses the variable name `video_id` for this value.

---

### Drop Target
A colored box in the main UI window that accepts URL drops (drag-and-drop or paste) to trigger a download. Each Drop Target is bound to a fixed Source, so dropping a URL onto the "Podcasts" Drop Target processes it as an audio download. Code name: `DropLabel`.

---

### Resolution Preset
One rung of the rendition ladder (2160, 1440, 1080, 720, 480, or 360), identified by its height. Each Resolution Preset owns a Drop Target, a Playlist File, and a tile colour, and downloads the best available quality at or below its height. Which presets are enabled is configurable in **Settings → Resolutions**; at least one must stay enabled. Code name: `ResolutionPreset` (registry in `src/resolutions.py`).

---

### Archive
A text file at `resources/archive.txt` that records every Video Key that has already been downloaded. yt-dlp checks this file before downloading to skip previously-downloaded Videos and Episodes. Format: one `youtube <video_key>` per line.

---

### Live Queue
A file at `resources/live_queue.txt` that stores URLs of live or upcoming Videos that cannot be downloaded yet. A background loop polls these URLs and downloads them once the stream ends. Each entry stores the URL, its Source, and optionally its Playlist ID.

---

## Code-Only Terms

These terms appear in code and are documented here for reference, but prefer the plain-English equivalents above in conversation.

### Source
A string routing key that flows through the download pipeline to determine:
- Which Playlist File to load
- Output directory and filename template
- Download format (video height or audio codec)
- Filtering rules (match filter, podcast filtering)

Common values: `"1080playlists"`, `"720playlists"`, `"audio_playlists"`, `"audio"`, `"1080"`, `"720"`.

### Entry
The yt-dlp Python dict representing a single Video or Episode. Key fields: `id` (Video Key), `title`, `duration`, `timestamp`, `playlist_id`, `playlist_index`. Prefer "Video" or "Episode" in conversation.

### qmeta (Queue Metadata)
A dict attached to each download job that carries routing and logging information:
- `site` — detected platform (`"youtube"`, `"nebula"`, `"unknown"`)
- `type` — the Source value
- `playlist_comments` — dict mapping playlist ID → **Label**
- `playlist_id` — ID of the parent Playlist, if applicable

---

## Term Relationships

```
Playlist File
└── contains one or more Playlists
    ├── Video Playlist
    │   └── contains Videos  (each has a Video Key)
    └── Podcast  (audio Playlist)
        └── contains Episodes  (each has a Video Key)

Drop Target  →  triggers download with a fixed Source
Archive      →  stores Video Keys of completed downloads
Live Queue   →  stores URLs of in-progress/upcoming Videos
```
