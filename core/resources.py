"""Resolve project data files in source and PyInstaller frozen layouts."""

import os
import sys


def resource_path(*parts):
    """Return an absolute path under the repository or frozen bundle root."""
    root = getattr(
        sys,
        "_MEIPASS",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    return os.path.join(root, *parts)