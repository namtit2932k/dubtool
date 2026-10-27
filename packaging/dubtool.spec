# PyInstaller spec for DubTool (AGU4 11) — windowed, one-directory build.
# Run from the repo root:  pyinstaller packaging/dubtool.spec --noconfirm
# Non-obvious collection decisions are listed in README_BUILD.md.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs

ROOT = Path(SPECPATH).resolve().parent  # repo root
sys.path.insert(0, str(ROOT / "src"))

datas = [
    (str(ROOT / "packaging" / "modes.yaml"), "packaging"),
    (str(ROOT / "vendor"), "vendor"),
]

# faster-whisper ships bundled VAD assets; ctranslate2/onnxruntime and av are
# mostly dynamic libraries with hooks that miss some builds.
datas += collect_data_files("faster_whisper")
datas += collect_dynamic_libs("ctranslate2", destdir=".")
datas += collect_dynamic_libs("onnxruntime", destdir=".")
datas += collect_dynamic_libs("av", destdir=".")
datas += collect_data_files("edge_tts")
datas += collect_data_files("certifi")

hiddenimports = []
hiddenimports += collect_submodules("keyring.backends")
hiddenimports += ["keyring.backends.Windows"]
hiddenimports += collect_submodules("faster_whisper")
hiddenimports += collect_submodules("edge_tts")

binaries = []

a = Analysis(
    [str(ROOT / "src" / "dubtool" / "app" / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest", "xmlrpc", "pydoc_data"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DubTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "packaging" / "icon.png"),
    version=str(ROOT / "packaging" / "version.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DubTool",
)
