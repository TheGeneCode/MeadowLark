# Third-party notices

MeadowLark is licensed under the GNU General Public License v3.0 (see [LICENSE](LICENSE)). The Windows
installer bundles the third-party components listed below. Each installed copy carries the full
license texts and an `INVENTORY.txt` of every bundled package (including transitive Python and npm
dependencies) in the `licenses\` folder of the install directory. That folder is generated at build
time from the exact packages the installer contains, so it is authoritative; this file is the
human-readable summary of the top-level components.

MeadowLark's corresponding source for any release is the matching `vX.Y.Z` tag of this repository.

| Component | License | Upstream |
|---|---|---|
| `bgutil-ytdlp-pot-provider` | GPL-3.0 | <https://github.com/Brainicism/bgutil-ytdlp-pot-provider> |
| `deno` | MIT | <https://github.com/denoland/deno> |
| `genekit` | MIT | <https://github.com/TheGeneCode/genekit> |
| `hurry-filesize` | ZPL 2.1 | <https://pypi.org/project/hurry.filesize/> |
| `pyqt6` | GPL-3.0-only | <https://www.riverbankcomputing.com/software/pyqt/> |
| `python-dotenv` | BSD-3-Clause | <https://github.com/theskumar/python-dotenv> |
| `requests` | Apache-2.0 | <https://github.com/psf/requests> |
| `wakepy` | MIT | <https://github.com/fohrloop/wakepy> |
| `yt-dlp` | Unlicense | <https://github.com/yt-dlp/yt-dlp> |
| `yt-dlp-ejs` | Unlicense AND MIT AND ISC | <https://github.com/yt-dlp/ejs> |
| Qt 6 (via `PyQt6-Qt6`) | LGPL-3.0 | <https://www.qt.io/> |
| FFmpeg | GPL-3.0-or-later | <https://ffmpeg.org/> |
| bgutil PO-token server (vendored) | GPL-3.0 | <https://github.com/Brainicism/bgutil-ytdlp-pot-provider> |
| Python runtime | PSF-2.0 | <https://www.python.org/> |

The vendored bgutil server lives in `vendor/bgutil-pot-provider/` with its upstream
[LICENSE](vendor/bgutil-pot-provider/LICENSE); its npm dependencies are covered by the generated
`licenses\npm\` folder.

**FFmpeg.** The build is BtbN's `win64-gpl` variant, licensed GPL-3.0-or-later. Its source is the
upstream FFmpeg commit named in the bundled `licenses\binary\ffmpeg-*` folder, plus
<https://github.com/BtbN/FFmpeg-Builds>.
