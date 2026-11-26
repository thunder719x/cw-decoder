# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：onedir（启动快、误报少）。

用法：
    python -m PyInstaller tools/cw-decoder.spec --noconfirm

产物：dist/cw-decoder/（整个文件夹压成 zip 即 Release 附件）
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
sys.path.insert(0, ROOT)

datas = [
    (os.path.join(ROOT, "models"), "models"),
    (os.path.join(ROOT, "assets"), "assets"),
    (os.path.join(ROOT, "samples"), "samples"),
]

hiddenimports = [
    "pyaudiowpatch",
    "soundcard",
    "scipy.signal",
    "scipy.signal._sosfilt",
    "onnxruntime",
    "PIL.ImageTk",
]

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "pandas", "IPython", "pytest", "setuptools"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cw-decoder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 无控制台；崩溃信息写入 crash.log
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="cw-decoder",
)
