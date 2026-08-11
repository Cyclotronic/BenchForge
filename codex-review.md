# Codex Code and Windows Distribution Review

Reviewed 2026-08-11 against BenchForge Studio 1.0.0-rc1.

## Addressed in this pass

- Added a packaged license set: BenchForge MIT, GPLv3, LGPLv3, the Python
  Software Foundation license, and third-party/source/relinking notices.
- Added license files to the PyInstaller bundle and made
  `verify_frozen_build.py` fail when any required notice is absent.
- Replaced the former plain-text UDP discovery response with DNS-format mDNS
  and DNS-SD PTR, SRV, TXT, and A records, including cache-flush bits,
  enumeration responses, unsolicited announcements, and goodbye records.
- Made discovery persona-aware. Prologix advertises
  `_prologix-gpib._tcp.local.`, AR488 advertises
  `_ar488-gpib._tcp.local.`, and the E5810/LXI mode advertises `_lxi`,
  `_scpi-raw`, and `_vxi-11` services. VXI-11 is omitted if its listener fails.
- Mode changes now stop and restart listeners and advertisements together.

## Remaining review findings

### P1 — Bound network input is still required

The Prologix and raw-SCPI parsers retain unterminated text indefinitely, and
VXI-11 accepts a very large declared RPC record length. This is low exposure on
the default loopback binding but becomes a denial-of-service risk when the app
is bound to a LAN address. Add maximum line/frame sizes, idle timeouts, and a
diagnostic disconnect.

### P1 — Release CI must execute the frozen application

The release workflow currently checks only that `BenchForge.exe` exists. It
should run `python tools/verify_frozen_build.py --copy-local never` before
compression and upload. This pass strengthened that verifier, but did not alter
the workflow.

### P1 — Define the intended LXI conformance boundary

The DNS-SD records now use the correct DNS wire format and LXI TXT identity
keys. Full LXI Device Specification conformance is broader: `_lxi._tcp` denotes
an HTTP(S) identification endpoint, and modern LXI also requires web/API,
hostname conflict resolution, configuration controls, and other behavior not
implemented by BenchForge. The current `_lxi` SRV record uses the emulated raw
socket endpoint as the discoverable gateway endpoint. Before claiming certified
or full LXI compliance, either add the required HTTP(S) identification service
and conflict-probing state machine or describe this specifically as LXI/VXI-11
transport discovery emulation.

### P2 — Make VXI-11 startup transactional in the server itself

The GUI now calls `stop()` after a failed VXI-11 start, preventing stale
advertisements and releasing partial listeners. The server's `start()` method
should still own rollback: bind mandatory sockets first inside a try block, close
all successfully opened sockets on failure, and set `_running` only after the
mandatory set is live.

### P2 — Pin release inputs

`requirements.txt` and `requirements-dev.txt` use lower bounds. CI requests
Python 3.11 while the inspected local binary contained Python 3.14. Add a hashed
release lock file and record Python, PySide6, Qt, Shiboken, and PyInstaller
versions in release notes. A version lock also makes the Qt third-party license
inventory reproducible.

### P2 — Add visible crash reporting

The application is built with `console=False`; an uncaught startup exception can
still look like no response at all. Install a top-level exception hook that
writes to `%LOCALAPPDATA%\BenchForge\Logs` and shows a small dialog containing
the log path.

### P2 — Sign and identify the Windows application

The inspected executable is not Authenticode signed and has no custom icon.
Sign the final EXE and archive in CI, timestamp the signature, publish a SHA-256,
and add a multi-resolution `.ico`. For enterprise distribution, decide whether
the portable ZIP is sufficient or whether MSIX/MSI installation, shortcuts,
firewall rules, upgrades, and uninstall support are required.

### P2 — Complete a release-specific third-party attribution audit

The primary licenses and Qt for Python attribution/source references now ship.
Once dependency versions are locked, enumerate the exact Qt plugins and embedded
third-party libraries in the bundle and vendor any component-specific permissive
license texts required by that exact Qt release. This should be a release
checklist item rather than relying on a moving documentation URL.

## Verification still required before release

- Clean rebuild from the future release lock file.
- Frozen-bundle verification against that new build.
- Bonjour/LXI Discovery Tool and Keysight Connection Expert interoperability on
  a real Windows LAN, including mode changes and Windows Firewall behavior.
- Hardware A/B checks where the physical Prologix and E5810A are available.
- Authenticode, SmartScreen, and antivirus reputation testing.