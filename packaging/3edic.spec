# PyInstaller spec for 3EDIČ. Windows: folder with 3EDIC.exe (window) + 3EDIC-console.exe (for --smoke and logs).
# macOS: 3EDIC.app. Linux: console build, used only to check the bundling in development.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).parent
build = Path(SPECPATH) / "build"

datas = [
    (str(root / "edic" / "ui"), "edic/ui"),
    (str(root / "edic" / "data"), "edic/data"),
    (str(root / "edic" / "models" / "registry.json"), "edic/models"),
]
datas += collect_data_files("rapidocr_onnxruntime")            # OCR models and config live inside that package
binaries = collect_dynamic_libs("onnxruntime")
hidden = collect_submodules("uvicorn") + ["multipart", "python_multipart"]
try:
    hidden += collect_submodules("webview")                      # native window (only when pywebview is installed)
except Exception:
    pass

# RapidOCR appends its own folder to sys.path and imports ch_ppocr_* as TOP-LEVEL modules at run time,
# which PyInstaller cannot see. Put that folder on the analysis path and name the modules explicitly.
import pkgutil
import rapidocr_onnxruntime

rapid_dir = Path(rapidocr_onnxruntime.__file__).parent
hidden += collect_submodules("rapidocr_onnxruntime")
for sub in sorted(p.name for p in rapid_dir.iterdir() if p.is_dir() and p.name.startswith("ch_ppocr")):
    hidden += [sub] + [f"{sub}.{m.name}" for m in pkgutil.iter_modules([str(rapid_dir / sub)])]

excludes = ["tkinter", "matplotlib", "scipy", "sklearn", "torch", "pytest", "IPython", "notebook"]

a = Analysis([str(Path(SPECPATH) / "launcher.py")], pathex=[str(root), str(rapid_dir)], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)

icon_ico = str(build / "icon.ico") if (build / "icon.ico").exists() else None
icon_icns = str(build / "icon.icns") if (build / "icon.icns").exists() else None

windowed = sys.platform in ("win32", "darwin")
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="3EDIC", console=not windowed,
          icon=icon_ico if sys.platform == "win32" else icon_icns, upx=False)
exes = [exe]
if sys.platform == "win32":
    # a console twin: the windowed exe has no stdout, so CI and support use this one
    exes.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name="3EDIC-console", console=True, icon=icon_ico, upx=False))

coll = COLLECT(*exes, a.binaries, a.datas, strip=False, upx=False, name="3EDIC")

if sys.platform == "darwin":
    app = BUNDLE(coll, name="3EDIC.app", icon=icon_icns, bundle_identifier="app.edic.desktop",
                 info_plist={"CFBundleDisplayName": "3EDIČ", "CFBundleName": "3EDIC", "NSHighResolutionCapable": True,
                             "LSMinimumSystemVersion": "11.0", "NSHumanReadableCopyright": "AGPL-3.0"})
