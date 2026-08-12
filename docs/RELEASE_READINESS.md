# Windows Build & Release Readiness Review

**Version 1.0.0** · reviewed 2026-08-10

Scope: packaging, the build gate, and what a binary handed to an external
developer needs. Everything below was checked against an actual build, not
inferred from the spec.

---

## Fixed

### The executable carried no identity at all

`BenchForge.exe` had **no Product Name, no File Version, no Company** — every
field in the Windows Properties dialog was blank. A tester holding two builds
could not tell them apart, which matters most for exactly the audience this is
aimed at.

The spec now generates Windows version metadata, parsed from `core/version.py`
so it cannot drift from the application:

```
ProductName     BenchForge Studio
FileDescription Universal Bench Instrument & Gateway Emulator
FileVersion     1.0.0
CompanyName     BenchForge
LegalCopyright  MIT Licence
```

The version is also in the **window title** now, because the app is packaged
windowed — the startup banner is never seen, so a screenshot in a bug report
was the one place the build number could realistically appear, and it was
missing.

### UPX compression was on

Packed executables are a well-documented false-positive trigger for antivirus
engines. This binary gets downloaded by an external developer; a quarantine on
first contact costs more than the few MB saves. **Disabled.**

### 40 MB of Qt the application never loads

`excludes=` in a PyInstaller spec filters Python **modules**, not the shared
libraries they would have loaded. `PySide6.QtQuick` and `PySide6.QtQml` were
correctly excluded — only `QtCore`, `QtGui` and `QtWidgets` `.pyd` files were
bundled — yet `Qt6Quick.dll`, `Qt6Qml.dll`, `Qt6Pdf.dll` and friends came along
regardless. Filtering `a.binaries` is the only way to drop them.

```
110.8 MB  ->  93.4 MB
```

Verified with `tools/verify_frozen_build.py`: the app still starts, builds its
window, binds its port and serves `++help` byte-identically. Removing shared
libraries is precisely the change that breaks a path tests do not cover, so it
was checked against the packaged build rather than the source tree.

### Test fixtures were shipped in the release

`core/sample_devices` was in `datas`. Nothing in `core/` reads it — only the
test suite does. Removed from the bundle.

### The build gate's most valuable check could silently vanish

`pyflakes` was used by `build_exe.py` but absent from `requirements-dev.txt`.
The gate skips a missing linter by design, so a fresh clone would have built
without the one check that caught a missing `import os` — a fault that passed
the entire test suite *and* produced a clean build, and would have shipped an
executable that died silently at launch.

---

## Deliberately kept

### opengl32sw.dll (19.7 MB, the single largest file)

Qt's software OpenGL fallback, and the obvious next target for size. **Kept.**

It is what allows Qt to render over Remote Desktop and inside VMs. A developer
running the emulator on a headless lab box is a likely user, and trading 19.7 MB
for "fails to start over RDP" is a bad deal. Size is not the objective;
a binary that runs everywhere is.

---

## Release follow-ups

### Application icon · *resolved*

The executable and running application now use the BenchForge icon. The frozen
bundle verifier checks both the PNG and ICO assets before release.

### The binary is unsigned · *decide before distributing*

Windows SmartScreen will warn on an unsigned executable downloaded from the
internet, and some corporate policies block it outright. Options, in increasing
order of cost: distribute as a zip with a published SHA-256; buy a code-signing
certificate; or accept the warning and document it in the release notes so it
does not read as a red flag.

**This is worth a decision rather than a default**, because the first thing the
recipient sees should not be a security warning.

### Release dependencies · *resolved*

Development requirements retain compatible lower bounds. Official candidates
use exact versions from `requirements-release.txt`, including Python 3.14.6 in
the workflow. Every bundle contains `BUILDINFO.txt` with the resolved runtime
and packaging versions.

### Windowed crash diagnostics · *resolved*

`console=False` no longer makes an unhandled failure silent. The exception hook
is installed before Qt and GUI imports, writes a traceback under
`%LOCALAPPDATA%\BenchForge\logs`, and shows the user that path. Crash reports
remain local unless the user chooses to share one.

---

## Release checklist

```bash
python build_exe.py                    # lint, tests, offline checks, then build
python tools/verify_frozen_build.py    # verify the BUNDLE, not the source tree
python tools/verify_hardware.py  --host <prologix>   # if hardware is available
python tools/ab_instruments.py   --host <prologix>
```

The network safety envelopes are verified against loopback emulator instances.
Do **not** send oversized or connection-exhaustion probes to production lab
hardware. If physical-controller boundary characterization is desired, schedule
it for a maintenance window when a controller reset and temporary loss of bench
connectivity cannot disrupt users.

Then, by hand:

- [ ] Launch the packaged app and confirm the title bar shows the expected version
- [ ] Check Properties → Details on the exe
- [ ] Confirm `docs/RELEASE_NOTES.md` matches what is actually in the build
- [ ] Push the matching version tag and download its candidate workflow artifact
- [ ] Complete UAT against that exact ZIP and record the candidate run ID
- [ ] Run **Publish Verified Release** with the approved run ID and tag; do not rebuild

> A network drive will not execute the binary. `H:` here is one, so the bundle
> must be copied to local storage before it will run — the verifier stages it
> automatically, but a human double-clicking it will not.
