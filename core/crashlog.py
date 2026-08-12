'''Local crash-report creation for the windowed Windows application.'''

import os
import platform
import sys
import tempfile
import time
import traceback

from .version import __version__


def write_crash_report(exc_type, exc_value, exc_traceback):
    '''Write an unhandled exception locally and return its path, if possible.'''
    root = os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()
    log_dir = os.path.join(root, 'BenchForge', 'logs')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    path = os.path.join(log_dir, 'benchforge-crash-%s-%d.log'
                        % (stamp, os.getpid()))
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write('BenchForge Studio %s\n' % __version__)
            handle.write('Python %s\n' % platform.python_version())
            handle.write('Platform %s\n\n' % platform.platform())
            traceback.print_exception(
                exc_type, exc_value, exc_traceback, file=handle)
        return path
    except OSError:
        traceback.print_exception(
            exc_type, exc_value, exc_traceback, file=sys.stderr)
        return None
