# PyInstaller spec for DubTool (AGU4 11) — windowed, one-directory build.
# Run from the repo root:  pyinstaller packaging/dubtool.spec --noconfirm
# Non-obvious collection decisions are listed in README_BUILD.md.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs

ROOT = Path(SPECPATH).resolve().parent  # repo root
sys.path.insert(0, str(ROOT / "src"))

# AGU9 5.2: single version source. The exe's version resource is GENERATED
# here from `dubtool.__version__` (1.0.<update number>) — packaging/version.txt
# is no longer read, so one bump updates every artifact at once.
from dubtool import __version__ as _VERSION  # noqa: E402

_VERSION_TRIPLE = tuple(int(p) for p in _VERSION.split("."))
from PyInstaller.utils.win32 import versioninfo as _vi  # noqa: E402  (Windows-only build)

_vinfo = _vi.VSVersionInfo(
    ffi=_vi.FixedFileInfo(
        filevers=(*_VERSION_TRIPLE, 0),
        prodvers=(*_VERSION_TRIPLE, 0),
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        _vi.StringFileInfo([
            _vi.StringTable(
                "040904b0",
                [
                    _vi.StringStruct("CompanyName", "DubTool project"),
                    _vi.StringStruct("FileDescription", "DubTool - English to Vietnamese video dubbing"),
                    _vi.StringStruct("FileVersion", _VERSION),
                    _vi.StringStruct("InternalName", "DubTool"),
                    _vi.StringStruct(
                        "LegalCopyright", "MIT License - see packaging/THIRD_PARTY.md"
                    ),
                    _vi.StringStruct("OriginalFilename", "DubTool.exe"),
                    _vi.StringStruct("ProductName", "DubTool"),
                    _vi.StringStruct("ProductVersion", _VERSION),
                ],
            )
        ]),
        _vi.VarFileInfo([_vi.VarStruct("Translation", [1033, 1200])]),
    ],
)
_tmp_spec_dir = Path(os.environ.get("DUBTOOL_SPEC_TMP", ROOT / "build"))
_tmp_spec_dir.mkdir(parents=True, exist_ok=True)
_txt_version = _tmp_spec_dir / "version-info.txt"
_txt_version.write_text(str(_vinfo), encoding="utf-8")

datas = [
    (str(ROOT / "packaging" / "modes.yaml"), "packaging"),
]

# AGU9 task 8: vendor/ is listed file-by-file, NOT as a directory entry —
# PyInstaller copies directory entries wholesale, so the per-file
# ffprobe.exe filter below never matched and the 121 MB ffprobe build
# stayed in the shipped folder of the 262f9eb build.
for _src in (ROOT / "vendor").iterdir():
    if _src.name.lower() == "ffprobe.exe":
        continue
    if _src.is_file():
        datas.append((str(_src), "vendor"))
    else:
        datas.append((str(_src), "vendor/" + _src.name))

# faster-whisper ships bundled VAD assets; ctranslate2/onnxruntime and av are
# mostly dynamic libraries with hooks that miss some builds.
datas += collect_data_files("faster_whisper")
datas += collect_dynamic_libs("ctranslate2", destdir=".")
datas += collect_dynamic_libs("onnxruntime", destdir=".")
datas += collect_dynamic_libs("av", destdir=".")
datas += collect_data_files("edge_tts")
datas += collect_data_files("certifi")
# AGENT_UPDATE_7.md 5.6: python-docx ships its default template as data
# files — a frozen app without them fails at runtime when saving a docx.
datas += collect_data_files("docx")

hiddenimports = []
hiddenimports += collect_submodules("keyring.backends")
hiddenimports += ["keyring.backends.Windows"]
hiddenimports += collect_submodules("faster_whisper")
hiddenimports += collect_submodules("edge_tts")
# python-docx + its lxml dependency (readable translation export, AGU7 5.6)
hiddenimports += ["docx", "docx.oxml", "docx.oxml.ns", "lxml", "lxml.etree"]

binaries = []

# ---------------------------------------------------------------- pruning
# AGU9 task 8: verified-by-measurement removals. Each rule maps to a check
# that passed on a pruned trial copy (DubTool.exe --selftest + --gui-smoke,
# 9/9 steps) before it was encoded here.

# vendor/ ships ffmpeg.exe, ffprobe.exe, fonts/ — all probed in-process via
# PyAV since AGU6 (verified on mp4/mkv/mov/webm/avi/m4v), so the duplicated
# 122 MB ffprobe build is not shipped; PATH fallback stays.
data_datas = [
    (src, dst) for (src, dst) in datas
    if not src.replace("\\", "/").lower().endswith("ffprobe.exe")
]
datas = data_datas

# Qt modules the QtWidgets app never imports (AGU9 task 8.2) — QtTest is
# KEPT: the app's own --gui-smoke runner drives widgets with it.
_QT_DROP_PREFIXES = (
    "Qt6Quick", "Qt6Qml", "Qt6Pdf", "Qt6VirtualKeyboard",
)

def _binaries_filter(items):
    out = []
    for src, dest in items:
        name = Path(src).name
        if name.startswith(_QT_DROP_PREFIXES):
            continue
        out.append((src, dest))
    return out

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

# Root-level duplicated runtime DLLs
def _root_filename(dest_name: str) -> bool:
    return ("\\" not in dest_name) and ("/" not in dest_name)


# Root-level duplicated runtime DLLs: collect_dynamic_libs already places
# them next to their importing .pyd (ctranslate2/ and onnxruntime/capi/),
# and Windows loads the .pyd's own directory first — the root copies were
# dead weight (AGU9 trial: removed, selftest + gui-smoke pass).
_DUP_ROOT_DLLS = {
    "ctranslate2.dll", "onnxruntime.dll", "onnxruntime_providers_shared.dll",
    "libiomp5md.dll", "cudnn64_9.dll",  # CPU-only build; never loaded
}

def _prune_binaries(items):
    # In a.binaries each entry is (dest_name, src_path, typecode); dest_name
    # is a bare filename for copies placed at the _internal root and
    # "package/dll.dll" for copies next to their package.
    for dest_name, src_path, *rest in items:
        name = Path(dest_name).name
        if name in _DUP_ROOT_DLLS and _root_filename(dest_name):
            continue
        if name.startswith(_QT_DROP_PREFIXES) and "PySide6" in dest_name.replace("\\", "/"):
            continue
        yield (dest_name, src_path, *rest)

def _prune_datas(items):
    for dest_name, src_path, *rest in items:
        if src_path.replace("\\", "/").split("/")[-1].endswith(".qm") and "PySide6" in src_path.replace("\\", "/"):
            base = src_path.replace("\\", "/").split("/")[-1]
            if base not in ("qt_en.qm", "qt_vi.qm", "qtbase_en.qm", "qtbase_vi.qm"):
                continue
        yield (dest_name, src_path, *rest)

a.binaries = TOC(_prune_binaries(a.binaries))
a.datas = TOC(_prune_datas(a.datas))

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
    # .ico: PyInstaller on Windows needs the container format directly (the
    # build venv has no Pillow to convert the PNG at build time).
    icon=str(ROOT / "packaging" / "icon.ico"),
    version=str(_txt_version),
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
