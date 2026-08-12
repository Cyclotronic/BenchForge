"""
BenchForge Application Entry Point (`benchforge.py`)

Universal Bench Instrument & Gateway Emulator Suite.
Run this script to launch the desktop application:
    python benchforge.py
"""

import os
import sys

# Ensure the repository root is importable when run as a script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.crashlog import write_crash_report


def _show_crash_dialog(path):
    detail = ('A crash report was written to:\n%s' % path if path else
              'The crash report could not be written. Check Windows event logs.')
    message = 'BenchForge Studio encountered an unexpected error.\n\n' + detail
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QMessageBox.critical(None, 'BenchForge Studio Error', message)
            return
    except Exception:
        pass

    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, message, 'BenchForge Studio Error', 0x10)
        except Exception:
            pass


def _handle_unhandled_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    path = write_crash_report(exc_type, exc_value, exc_traceback)
    _show_crash_dialog(path)


# Install before importing Qt or the GUI so early startup/import failures are
# actionable in the console-free packaged application too.
sys.excepthook = _handle_unhandled_exception

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core.gui_qt import BenchForgeQtApp
from core.resources import resource_path
from core.version import PROFILES_VERIFIED, __version__


def main():
    print("================================================================================")
    print("  BenchForge Studio %s" % __version__)
    print("  Universal Bench Instrument & Gateway Emulator Suite")
    print("  Prologix Ethernet  |  Keysight E5810A (VXI-11)  |  AR488")
    print("  Hardware profiles verified %s" % PROFILES_VERIFIED)
    print("================================================================================")

    # High-DPI rounding must be set before the QApplication exists, or the 1 px
    # hairlines this design relies on render unevenly at 125% and 150% scaling.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("BenchForge Studio")
    app.setOrganizationName("BenchForge")
    app.setWindowIcon(QIcon(resource_path("assets", "benchforge-icon.png")))

    window = BenchForgeQtApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
