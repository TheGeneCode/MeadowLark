# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

a = Analysis(
    ["meadowlark.pyw"],
    pathex=[],
    binaries=[
        (".venv/Scripts/deno.exe", "."),
    ],
    datas=copy_metadata("meadowlark")
    + copy_metadata("bgutil-ytdlp-pot-provider")
    + [
        # Only the icons ship. Everything else under resources/ is per-user runtime state
        # (cookies.txt, archive.txt, playlists/, *_queue.json, failed_downloads.json) that a
        # local build would otherwise bundle into the installer.
        ("resources/icons", "resources/icons"),
        (
            ".venv/Lib/site-packages/yt_dlp_plugins/extractor",
            "yt-dlp-plugins/extractor",
        ),
        ("vendor/bgutil-pot-provider/server", "bgutil-server"),
    ],
    hiddenimports=[
        *collect_submodules("yt_dlp"),
        *collect_submodules("yt_dlp_plugins"),
        "yt_dlp_plugins.extractor.getpot_bgutil",
        "yt_dlp_plugins.extractor.getpot_bgutil_http",
        "yt_dlp_plugins.extractor.getpot_bgutil_script",
        "wakepy._implementations",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeadowLark",
    debug=False,
    strip=False,
    upx=True,
    console=False,
    icon="resources/icons/meadowlark.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MeadowLark",
)
