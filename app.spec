# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files


a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('base_vintab.db', '.'),
        ('custom_charts.json', '.'),
        *collect_data_files('plotly', includes=['package_data/plotly.min.js']),
    ],
    hiddenimports=[
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VinTab',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
