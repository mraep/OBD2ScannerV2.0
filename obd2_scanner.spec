# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec untuk OBD2 Scanner GUI.
# Jalankan build ini di WINDOWS (bukan di sini) dengan:
#   pyinstaller --clean obd2_scanner.spec
# atau cukup double-click build_exe.bat

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# 'obd' (python-obd) dan 'matplotlib' punya submodule/data yang kadang tidak
# otomatis kedeteksi PyInstaller (mis. protokol OBD2, backend TkAgg) -
# collect_all memastikan semuanya ikut terbundle ke exe.
for pkg in ("obd", "matplotlib"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += ["serial", "serial.tools.list_ports"]

a = Analysis(
    ["obd2_scanner_gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OBD2Scanner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # False = jendela GUI saja, tanpa console hitam di belakang
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,            # ganti mis. "icon.ico" kalau punya ikon sendiri
)
