# Windows Build & Release Readiness Review

**Version 1.0.0-rc1** · reviewed 2026-08-10

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
FileVersion     1.0.0-rc1
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

## Open, with recommendations

### No application icon · *cosmetic, but visible*

There is no `.ico` in the repository, so the exe carries PyInstaller's default.
For a tool being handed to another developer this is the most visible remaining
rough edge. Needs an asset before it can be fixed — a 256×256 `.ico` dropped in
and referenced as `icon=` in the spec.

### The binary is unsigned · *decide before distributing*

Windows SmartScreen will warn on an unsigned executable downloaded from the
internet, and some corporate policies block it outright. Options, in increasing
order of cost: distribute as a zip with a published SHA-256; buy a code-signing
certificate; or accept the warning and document it in the release notes so it
does not read as a red flag.

**This is worth a decision rather than a default**, because the first thing the
recipient sees should not be a security warning.

### Dependencies are unpinned · *reproducibility*

`PySide6>=6.5.0` means two builds a month apart can bundle different Qt
versions. Fine for development, not ideal for a release you may need to
reproduce when someone reports a fault. Consider pinning exact versions in a
`requirements-release.txt` and recording the built-against versions in the
release notes.

### A windowed build fails silently · *partially mitigated*

`console=False` means an unhandled exception at startup shows the user nothing
at all. This bit us: a missing import produced a process that launched, stayed
alive and never bound its port, with no error anywhere.

Mitigated by the pre-flight gate and `verify_frozen_build.py`. Not eliminated.
The real fix is a crash log — wrap `main()` and write any traceback beside the
executable — so a tester can send a file instead of "it didn't start".

---

## Release checklist

```bash
python build_exe.py                    # lint, tests, offline checks, then build
python tools/verify_frozen_build.py    # verify the BUNDLE, not the source tree
python tools/verify_hardware.py  --host <prologix>   # if hardware is available
python tools/ab_instruments.py   --host <prologix>
```

Then, by hand:

- [ ] Launch the packaged app and confirm the title bar shows the expected version
- [ ] Check Properties → Details on the exe
- [ ] Confirm `docs/RELEASE_NOTES.md` matches what is actually in the build
- [ ] Zip the `dist/BenchForge` folder and publish the SHA-256 alongside it

> A network drive will not execute the binary. `H:` here is one, so the bundle
> must be copied to local storage before it will run — the verifier stages it
> automatically, but a human double-clicking it will not.
