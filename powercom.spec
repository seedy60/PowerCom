# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_dynamic_libs

binaries = []
# prismatoid >= 0.17 ships _prism_cffi.pyd in prism/_native, loaded via a
# runtime __path__ extension PyInstaller can't see; collect it by pattern.
binaries += collect_dynamic_libs('prism', search_patterns=['*.dll', '*.pyd'])
binaries += collect_dynamic_libs('sound_lib')


a = Analysis(
    ['powercom.py'],
    pathex=[],
    binaries=binaries,
    datas=[],
    hiddenimports=['notifypy', 'ntfpy', 'prism', 'prism.core', '_cffi_backend', 'logging.handlers', 'pyprowl', 'sound_lib', 'sound_lib.output', 'sound_lib.stream', 'watchdog', 'watchdog.observers', 'wx', 'wx.adv', 'powercom_config', 'powercom_config_model'],
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
    [],
    exclude_binaries=True,
    name='powercom',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='powercom',
)
