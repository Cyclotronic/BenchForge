"""
PyInstaller Compilation Script (`build_exe.py`)

Compiles BenchForge Studio into a standalone executable:

    python build_exe.py                 # gated build
    python build_exe.py --skip-checks   # build anyway (not for a release)

The pre-flight checks are not ceremony. A missing `import os` in gui_qt.py once
passed the whole unit suite AND produced a clean PyInstaller build, and would
have shipped an executable that died silently at launch -- the app is packaged
windowed, so a NameError shows the user nothing at all. Lint was the only check
that caught it, so it runs before the compiler does.

After building, verify the BUNDLE, not just the source tree:

    python tools/verify_frozen_build.py
"""

import argparse
import importlib.util
import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

BUILD_PACKAGES = (
    'PySide6', 'PySide6_Addons', 'PySide6_Essentials', 'shiboken6',
    'pyinstaller', 'pyinstaller-hooks-contrib', 'altgraph', 'packaging',
    'pefile', 'pywin32-ctypes', 'setuptools', 'pyflakes',
)


def run(label, cmd, optional_module=None):
    """
    Run one pre-flight check. Returns True when the build may proceed.

    `optional_module` names a module the check needs but can do without. It is
    probed with find_spec rather than by catching FileNotFoundError: running
    `python -m missing_module` EXITS 1, it does not raise, so catching the
    exception never fired and a machine without pyflakes could not build at all.
    """
    if optional_module and importlib.util.find_spec(optional_module) is None:
        print("\n--- %s ---" % label)
        print("    [skip] %s is not installed" % optional_module)
        return True

    print("\n--- %s ---" % label)
    try:
        result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    except FileNotFoundError:
        print("    [FAIL] %s is not available" % cmd[0])
        return False
    if result.returncode == 0:
        print("    [ok] %s" % label)
        return True
    print("    [FAIL] %s (exit %d)" % (label, result.returncode))
    return False


def preflight():
    checks = [
        # Lint first: it is fast, and it is what catches the failures that
        # survive both the test suite and the compiler.
        ("lint", [sys.executable, "-m", "pyflakes", "."], "pyflakes"),
        ("unit tests", [sys.executable, "-m", "unittest", "discover",
                        "-s", "tests"], None),
        ("offline fidelity checks", [sys.executable,
                                     os.path.join("tools", "verify_offline.py")],
         None),
    ]
    # Run every check rather than short-circuiting: one build should report all
    # the problems, not make you rebuild to discover the next one.
    return all([run(label, cmd, optional) for label, cmd, optional in checks])


def build():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-checks", action="store_true",
                        help="build without the pre-flight gate")
    args = parser.parse_args()

    print("=== BenchForge Standalone Build ===")

    if args.skip_checks:
        print("\n!! pre-flight checks SKIPPED -- do not ship this build")
    elif not preflight():
        print("\n[-] Pre-flight checks failed. Nothing was built.")
        print("    Fix the above, or use --skip-checks for a throwaway build.")
        return 1

    spec_path = os.path.join(SCRIPT_DIR, "benchforge.spec")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           spec_path]

    print("\n--- PyInstaller ---")
    print(" ".join(cmd))
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    if result.returncode != 0:
        print("\n[-] PyInstaller build failed with exit code %d" % result.returncode)
        return result.returncode

    dist = os.path.join(SCRIPT_DIR, "dist", "BenchForge")
    # PyInstaller places collected data under _internal. Keep that copy for
    # runtime lookup, and add a conspicuous top-level LICENSES directory for
    # recipients browsing the portable distribution.
    license_dist = os.path.join(dist, "LICENSES")
    shutil.copytree(os.path.join(SCRIPT_DIR, "LICENSES"), license_dist,
                    dirs_exist_ok=True)
    build_info = [
        'BenchForge build environment',
        'Python==%s' % platform.python_version(),
    ]
    for package in BUILD_PACKAGES:
        build_info.append('%s==%s' % (
            package, importlib.metadata.version(package)))
    build_info_path = os.path.join(dist, 'BUILDINFO.txt')
    with open(build_info_path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write('\n'.join(build_info) + '\n')
    shutil.copy2(os.path.join(SCRIPT_DIR, "LICENSE"),
                 os.path.join(license_dist, "LICENSE"))
    print("\n[+] Build succeeded: %s" % dist)
    print("\n    A successful build does NOT mean the bundle works. Data files")
    print("    can be missing from the spec and fail silently. Verify it:")
    print("        python tools/verify_frozen_build.py")
    return 0


if __name__ == "__main__":
    sys.exit(build())
