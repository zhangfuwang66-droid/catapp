# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from pathlib import Path

app_root = Path(SPECPATH)
sys.path.insert(0, str(app_root))
from build_config import SERVER_ROOT, collect_bundle_files

datas, binaries, hiddenimports = collect_bundle_files()
debug_console = os.environ.get("CYBERMIAO_BUILD_CONSOLE") == "1"

a = Analysis(
    [str(app_root / "src" / "launcher.py")],
    pathex=[str(app_root / "src"), str(SERVER_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": ["Agg"]}},
    runtime_hooks=[],
    excludes=["IPython", "jupyter", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="CyberMiaoDebug" if debug_console else "CyberMiao",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=debug_console, disable_windowed_traceback=False,
    icon=str(app_root / "assets" / "icon.ico"),
)
