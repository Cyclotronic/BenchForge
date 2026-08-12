# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for BenchForge Studio.

Windows file metadata is generated from core/version.py so the shipped binary
identifies itself. Without it the exe has no Product Name, no File Version and
no Company in its Properties dialog -- a tester who has two builds cannot tell
them apart, which matters most for exactly the audience this is aimed at.
"""
import re

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo,
)

# Read the version without importing the package (the spec runs before the
# application is importable).
_version_src = open("core/version.py", encoding="utf-8").read()
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', _version_src).group(1)

# Windows wants four integers. A final release such as '1.0.0' becomes
# (1, 0, 0, 0); a pre-release keeps its number in the fourth field, so
# '1.0.0-rc1' -> (1, 0, 0, 1) and sorts below the final build.
_m = re.match(r"(\d+)\.(\d+)\.(\d+)(?:-\w*?(\d+))?", VERSION)
FILEVERS = (int(_m.group(1)), int(_m.group(2)), int(_m.group(3)),
            int(_m.group(4) or 0))

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=FILEVERS, prodvers=FILEVERS,
                      mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                      subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "BenchForge"),
            StringStruct("FileDescription",
                         "Universal Bench Instrument & Gateway Emulator"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "BenchForge"),
            StringStruct("LegalCopyright", "MIT Licence"),
            StringStruct("OriginalFilename", "BenchForge.exe"),
            StringStruct("ProductName", "BenchForge Studio"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(
    ['benchforge.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Verbatim ++help capture from the physical controller. The ONLY data
        # file the running app reads; tools/verify_frozen_build.py asserts it
        # arrives intact at 1879 bytes, because a missing one fails silently.
        ('core/prologix_help.txt', 'core'),
        # License texts and third-party notices must travel with every binary
        # distribution. Keeping these as loose files also makes the LGPL terms
        # and Qt DLL replacement path visible to recipients.
        ('LICENSE', 'licenses'),
        ('LICENSES', 'licenses'),
        # Runtime Qt icon plus the original Windows icon resource.
        ('assets/benchforge-icon.png', 'assets'),
        ('assets/benchforge-icon.ico', 'assets'),
        # NOTE: core/sample_devices is deliberately NOT bundled. Nothing in
        # core/ reads it -- only the test suite does -- so shipping it would
        # put test fixtures in a release binary.
    ],
    hiddenimports=['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim the bundle: the UI is Qt, so Tk is dead weight, and PyQt6 must never
    # be collected (it is GPL-only and would conflict with the MIT licence).
    excludes=[
        'PyQt6', 'PyQt5', 'tkinter', '_tkinter',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.Qt3DCore',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtNetwork', 'PySide6.QtSql', 'PySide6.QtTest',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BenchForge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is OFF deliberately. Packed executables are a well-known
    # false-positive trigger for antivirus engines, and this binary is handed
    # to an external developer: a quarantine on first download costs more
    # credibility than the few MB compression saves.
    upx=False,
    console=False,
    icon='assets/benchforge-icon.ico',
    version=version_info,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
# `excludes=` above filters Python MODULES, not the Qt shared libraries they
# would have loaded, so QtQuick/QtQml/Pdf came along anyway -- roughly 40 MB of
# a 110 MB bundle for an app that only uses QtCore, QtGui and QtWidgets.
# Filtering a.binaries is the only way to drop them.
#
# opengl32sw.dll is Qt's SOFTWARE OpenGL fallback and the single largest file
# at 19.7 MB. It is kept: this is a widgets app that does not need it locally,
# but it is what makes Qt render over Remote Desktop and inside VMs, and a
# developer running the emulator on a headless lab box is a likely user.
# Trading 19.7 MB for "fails to start on RDP" is a bad deal.
UNUSED_QT_BINARIES = (
    "qt6quick", "qt6qml", "qt6qmlmodels", "qt6qmlmeta", "qt6qmlworkerscript",
    "qt6pdf", "qt6virtualkeyboard",
)


def _wanted(entry):
    name = entry[0].lower().replace("\\", "/").rsplit("/", 1)[-1]
    stem = name[:-4] if name.endswith(".dll") else name
    return stem not in UNUSED_QT_BINARIES


a.binaries = [b for b in a.binaries if _wanted(b)]

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='BenchForge',
)
