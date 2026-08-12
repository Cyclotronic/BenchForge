# BenchForge Studio — Third-Party Notices

BenchForge Studio is distributed under the MIT License. A copy is included as
`LICENSE` in this directory in packaged builds.

The Windows distribution also contains the following runtimes and libraries.
They remain under their respective licenses; BenchForge's MIT license does not
replace or restrict those terms.

## Python

The bundled Python runtime is licensed under the Python Software Foundation
License and related historical notices. See `Python-Software-Foundation.txt`.

Source and release information: https://www.python.org/downloads/source/

## Qt for Python: PySide6 and Shiboken6

PySide6, its Essentials package, and Shiboken6 are used under the GNU Lesser
General Public License version 3. The distribution dynamically loads the Qt and
Shiboken DLLs from the `_internal` directory. Recipients may replace those DLLs
with compatible modified builds as permitted by the LGPL. BenchForge imposes no
contractual restriction on reverse engineering needed to debug such changes.

See `LGPL-3.0.txt` and `GPL-3.0.txt`. Corresponding source for the exact Qt for
Python release used to create a build is available from:

- https://code.qt.io/cgit/pyside/pyside-setup.git/
- https://download.qt.io/official_releases/QtForPython/

Qt for Python contains additional third-party components and attribution
statements. The authoritative inventory for the installed release is:
https://doc.qt.io/qtforpython-6/licenses.html

## Qt 6

The application uses Qt Core, Qt GUI, Qt Widgets, Windows platform integration,
and image-format plugins under the GNU Lesser General Public License version 3.
See `LGPL-3.0.txt` and `GPL-3.0.txt`.

Corresponding Qt source and the module-by-module third-party attribution
inventory are available from:

- https://download.qt.io/official_releases/qt/
- https://code.qt.io/cgit/qt/qt5.git/
- https://doc.qt.io/qt-6/licenses-used-in-qt.html

The exact Python, PySide6, Qt, and PyInstaller versions used for a release must
be recorded in that release's notes so recipients can retrieve matching source.

## PyInstaller

PyInstaller's bootloader is distributed under the GNU General Public License
version 2 with a special exception permitting distribution of executables it
creates. PyInstaller is a build tool and its Python package is not installed as
an application runtime dependency.

Source and license: https://github.com/pyinstaller/pyinstaller

## Referenced projects — no code included

The two projects below are **not** bundled, linked, or redistributed in any
form. They are named here because BenchForge's documentation refers to them and
because being explicit about what was and was not copied is the point of a
notices file.

### AR488 Arduino GPIB controller

The AR488 emulation persona was written from the observable behaviour of the
AR488 firmware by John Chajecki (`Twilight-Logic/AR488`, ver. 0.53.46), which is
published under the **GNU General Public License v3.0** — see `GPL-3.0.txt`.

`profiles/AR488_ADAPTER_PROFILE.md` quotes one short function from that firmware
for reference and identification, with attribution at the quotation. No AR488
code is present in BenchForge, and none is compiled into any build: the persona
implements the command vocabulary, error strings, argument ranges and
terminators the firmware documents, written independently.

That profile is marked source-derived and has never been checked against a
physical adapter.

### TestController

TestController is third-party closed-source software, and BenchForge is
developed to be a faithful gateway for it to talk to.

`docs/TESTCONTROLLER_OBSERVATIONS.md` reports interoperability findings about
its client behaviour. It contains **no TestController source code**: the
observations are descriptions of behaviour with class, method and line
references so the maintainer can consult their own source.

Some of those findings came from decompiling the application to understand what
a client expects on the wire. That was done for interoperability. The decompiled
output has never been committed to this repository and is not distributed with
any release. No TestController code is present in BenchForge.

## No warranty

All components are provided without warranty to the extent permitted by their
respective licenses. This notice is informational and is not legal advice.