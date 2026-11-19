# -*- mode: python ; coding: utf-8 -*-
"""Build a self-contained portable Windows EXE (no _internal directory)."""
from pathlib import Path

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('licenses', 'licenses'), ('assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
# Preserve the tested Qt multimedia and PDF functionality; skip unused ICU /
# virtual-keyboard binaries inherited from other tools on the runner's PATH.
a.binaries = [
    binary for binary in a.binaries
    if Path(binary[0]).name.lower() not in {
        'icuuc.dll', 'icudt78.dll',
        'qtvirtualkeyboardplugin.dll', 'qt6virtualkeyboard.dll',
    }
]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WeChatFileManager',
    console=False,
    debug=False,
    strip=False,
    upx=False,
    icon='assets/app.ico',
)
