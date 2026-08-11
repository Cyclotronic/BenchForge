"""
Release gate: prove the PACKAGED build is still faithful.

Passing tests against the source tree says nothing about the bundle. Data files
can be missing from the spec, or land somewhere the code does not look, and the
failure is silent: `++help` returns an empty string and every other command
still works, so nothing looks wrong until a client diffs it against hardware.

This launches the built executable, talks to it over a socket, and checks the
answers byte for byte against the recorded captures.

    python tools/verify_frozen_build.py
    python tools/verify_frozen_build.py --exe dist/BenchForge/BenchForge.exe

Exit code 0 when the bundle behaves like the source tree, 1 otherwise.
"""
import argparse
import ctypes
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HELP_CAPTURE = os.path.join(ROOT, "core", "prologix_help.txt")

parser = argparse.ArgumentParser()
parser.add_argument("--exe", default=os.path.join(
    ROOT, "dist", "BenchForge", "BenchForge.exe"))
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=1234)
# A freshly written 163-file bundle is slow to start the first time, because
# the virus scanner reads every file. 45 s was not enough on this machine.
parser.add_argument("--boot-timeout", type=float, default=120.0)
parser.add_argument("--copy-local", choices=("auto", "always", "never"),
                    default="auto",
                    help="stage the bundle on local storage before launching")
parser.add_argument("--keep", action="store_true",
                    help="leave the staged copy in place for inspection")
args = parser.parse_args()


def is_network_drive(path):
    """Windows refuses to execute binaries from a network share."""
    if os.name != "nt":
        return False
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive:
        return False
    DRIVE_REMOTE = 4
    return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_REMOTE

failures = []


def check(label, ok, detail=""):
    print("  %-46s %s%s" % (label, "PASS" if ok else "FAIL",
                            "  " + detail if detail else ""))
    if not ok:
        failures.append((label, detail))


print("=== bundle contents ===")
if not os.path.exists(args.exe):
    print("  executable not found: %s" % args.exe)
    print("  build it first:  python build_exe.py")
    sys.exit(1)
print("  exe: %s (%d bytes)" % (args.exe, os.path.getsize(args.exe)))

bundle_dir = os.path.dirname(args.exe)
with open(HELP_CAPTURE, "rb") as handle:
    expected_help = handle.read()
print("  source capture: %d bytes" % len(expected_help))

# PyInstaller puts collected data under _internal/ in recent versions, and
# beside the exe in older ones. Accept either.
found_at = None
for candidate in (os.path.join(bundle_dir, "_internal", "core", "prologix_help.txt"),
                  os.path.join(bundle_dir, "core", "prologix_help.txt")):
    if os.path.exists(candidate):
        found_at = candidate
        break
check("prologix_help.txt present in bundle", found_at is not None,
      found_at or "not found under %s" % bundle_dir)
if found_at:
    with open(found_at, "rb") as handle:
        bundled = handle.read()
    check("bundled capture is byte-identical", bundled == expected_help,
          "%d vs %d bytes" % (len(bundled), len(expected_help)))

# ---------------------------------------------------------------------------
print("\n=== staging ===")
staged_dir = None
if args.copy_local == "always" or (args.copy_local == "auto"
                                   and is_network_drive(args.exe)):
    # This repository lives on a file share, and Windows will not execute a
    # binary from a network drive. Copying to local storage is also a real
    # check in its own right: a PyInstaller bundle has to be relocatable, and
    # this is where a build with an absolute path baked in would fail.
    staged_dir = tempfile.mkdtemp(prefix="benchforge-frozen-")
    target = os.path.join(staged_dir, os.path.basename(bundle_dir))
    print("  %s is not locally executable; staging to %s" % (bundle_dir, target))
    shutil.copytree(bundle_dir, target)
    bundle_dir = target
    args.exe = os.path.join(target, os.path.basename(args.exe))
    print("  staged %d files" % sum(len(f) for _, _, f in os.walk(target)))
else:
    print("  running in place")

print("\n=== launching the packaged app ===")
probe = socket.socket()
probe.settimeout(0.4)
already = probe.connect_ex((args.host, args.port)) == 0
probe.close()
if already:
    print("  something is already listening on %s:%d -- stop it first, or the"
          % (args.host, args.port))
    print("  test would talk to the source build instead of the bundle.")
    sys.exit(1)

# Start from defaults. Without this the packaged app restores whatever mode
# and port the developer last used -- which is how this check first "failed":
# a leftover E5810A persona on a stale test port, not a build defect.
env = dict(os.environ, BENCHFORGE_IGNORE_SETTINGS="1")
# Capture the app's output. A windowed build shows the user nothing when it
# dies, and without this the tool can only report "the connection reset" and
# leave you guessing which side broke.
log_path = os.path.join(tempfile.gettempdir(), "benchforge-frozen-run.log")
log_handle = open(log_path, "wb")
proc = subprocess.Popen([args.exe], cwd=bundle_dir, env=env,
                        stdout=log_handle, stderr=subprocess.STDOUT)
print("  pid %d, waiting for it to bind %s:%d" % (proc.pid, args.host, args.port))
print("  app output -> %s" % log_path)

deadline = time.time() + args.boot_timeout
listening = False
while time.time() < deadline:
    s = socket.socket()
    s.settimeout(0.5)
    if s.connect_ex((args.host, args.port)) == 0:
        s.close()
        listening = True
        break
    s.close()
    time.sleep(0.5)
check("engine auto-started and bound the port", listening,
      "" if listening else "no listener after %.0fs" % args.boot_timeout)

try:
    if listening:
        # ONE connection for every exchange. The emulator reproduces the
        # Prologix single-client policy, so a fresh connect displaces the
        # previous socket -- reconnecting per command gets the earlier one
        # reset mid-read. This tool did exactly that and blamed the build.
        link = socket.socket()
        link.settimeout(4.0)
        link.connect((args.host, args.port))
        link.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        def talk(payload, wait=1.2):
            # Drain anything left from the previous exchange first.
            link.settimeout(0.2)
            try:
                while link.recv(8192):
                    pass
            except Exception:
                pass
            link.settimeout(4.0)

            link.sendall(payload + b"\n")
            time.sleep(wait)
            chunks = []
            while True:
                try:
                    data = link.recv(8192)
                except socket.timeout:
                    break
                if not data:
                    break
                chunks.append(data)
                if len(b"".join(chunks)) > 4000:
                    break
                time.sleep(0.15)
            return b"".join(chunks)

        print("\n=== behaviour of the packaged emulator ===")

        version = talk(b"++ver")
        check("++ver reports the firmware string",
              b"Prologix GPIB-ETHERNET Controller version" in version,
              repr(version[:60]))

        # The whole point of this tool.
        help_reply = talk(b"++help", wait=2.0)
        expected_reply = expected_help.rstrip(b"\r\n") + b"\r\n"
        check("++help returns the full capture",
              len(help_reply) == len(expected_reply),
              "%d bytes, expected %d" % (len(help_reply), len(expected_reply)))
        check("++help is byte-identical to the capture",
              help_reply == expected_reply,
              "" if help_reply == expected_reply else "content differs")

        unknown = talk(b"++invalidcmd")
        check("unknown command answers exactly as hardware",
              unknown == b"Unrecognized command\r\n", repr(unknown[:40]))

        idn = talk(b"++addr 1\n*IDN?\n++read eoi", wait=1.5)
        check("an instrument answers from the bundled bench",
              b"KEITHLEY" in idn or b"Agilent" in idn or b"HEWLETT" in idn,
              repr(idn[:60]))
except ConnectionResetError:
    # The app dropped the connection. Almost always means it died; the
    # captured log is the only place the reason exists.
    check("the app stayed alive through every exchange", False,
          "connection reset -- see %s" % log_path)
finally:
    try:
        link.close()
    except NameError:
        pass
    except Exception:
        pass
    print("\n=== shutting down ===")
    already_dead = proc.poll() is not None
    if already_dead:
        print("  the app had ALREADY exited, rc=%s -- it did not survive the run"
              % proc.returncode)
    proc.terminate()
    try:
        proc.wait(timeout=10)
        if not already_dead:
            print("  exited cleanly")
    except subprocess.TimeoutExpired:
        proc.kill()
        print("  killed")

    try:
        log_handle.close()
        with open(log_path, "rb") as handle:
            captured = handle.read().decode("utf-8", errors="replace").strip()
        if captured:
            print("\n--- app output ---")
            for line in captured.splitlines()[-25:]:
                print("  | %s" % line)
    except Exception:
        pass
    if staged_dir and not args.keep:
        time.sleep(1.0)          # let the bundle's handles close
        shutil.rmtree(staged_dir, ignore_errors=True)
        print("  staged copy removed")
    elif staged_dir:
        print("  staged copy kept at %s" % staged_dir)

print("\n" + "=" * 60)
if failures:
    print("FAILURES: %d" % len(failures))
    for label, detail in failures:
        print("  - %s  %s" % (label, detail))
    sys.exit(1)
print("The packaged build behaves like the source tree.")
