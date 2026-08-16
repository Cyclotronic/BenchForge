# TestController validation harness

Tooling and captured evidence for validating TestController against BenchForge's
Prologix emulator. Built to check the fixes in TestController 3.49 against the
issues raised in [`docs/TESTCONTROLLER_OBSERVATIONS.md`](../../docs/TESTCONTROLLER_OBSERVATIONS.md);
the findings are written up in
[`docs/TESTCONTROLLER_3.49_VALIDATION.md`](../../docs/TESTCONTROLLER_3.49_VALIDATION.md).

## What is here

| Path | Purpose |
| :--- | :--- |
| `bench_emulator.py` | Instrumented emulator. Runs the default 5-device bench, logs every frame and every connection event, prints a report on exit. `--gateway prologix` (default) or `--gateway e5810`. |
| `analyze_tc_log.py` | Cross-checks a TestController debug log against the emulator's frame log. Reads plain or `.gz`. |
| `config/` | Interface and script fragments for pointing TestController at the emulator. |
| `captures/2026-08-14/` | Raw evidence behind the 3.49 validation, gzipped. |
| `../../tools/patch_testcontroller_e5810.py` | Builds a patched jar for the E5810 exercise below. |

## What is deliberately not here

The TestController jars, the extracted class trees and the decompiled source are
**not** stored in this repository. The static findings in the validation document
describe behaviour and cite class, method and line references so the developer can
open their own source; reproducing that source here would be a separate act from
reading it for interoperability, and not one we have any business doing. This
follows the same policy as the original observations document.

To repeat the static comparison you need your own copies of the two jars and a
Java decompiler. The method was:

```bash
java -jar cfr.jar old.jar --jarfilter 'dk.hkj.(comm|shared).*' --outputdir old-src
java -jar cfr.jar new.jar --jarfilter 'dk.hkj.(comm|shared).*' --outputdir new-src
diff -ru old-src new-src
```

A release is a full recompile, so every class differs byte-wise; only the
decompiled source diff is meaningful.

## Re-running a validation

**1. Start the instrumented emulator.** It listens on 1235 by default here so it
does not collide with a BenchForge Studio instance already on 1234.

```bash
python tests/tc_validation/bench_emulator.py --label mytest --port 1235 --host 127.0.0.1
```

Add `--seconds N` to stop and report automatically; otherwise Ctrl+C ends it.
The bench is `InstrumentRegistry`'s default, which matches the reference device
set in `config/settingsLoad.reference.txt`:

```
A:1  Keithley 2002     A:2  Keithley 2001M    A:3  Keithley 2010
A:4  Fluke PM6690      A:5  Agilent 34411A
```

**2. Point TestController at it.** Copy your `Settings` directory somewhere
scratch, drop in `config/settingsGPIB.emulator.txt` as `settingsGPIB.txt`, and
copy `config/log5hz.txt` in as a startup script. Then launch with an isolated
`configDir` so your live configuration is untouched:

```bash
java -jar TestController.jar debug configDir=/path/to/scratch/cfg script=log5hz.txt > tc-mytest.log 2>&1
```

> **Do not put `#DELAY` in a startup script.** It blocks the event thread and
> freezes the UI for its full duration, which matters as soon as you need to
> click anything. `log5hz.txt` starts logging and exits for that reason.

**3. Drive it.** Reconnects must be done by hand — TestController does not allow a
reconnect while logging is running, so the sequence is *stop logging → reconnect →
restart logging*. This matters: on affected builds the device threads die at
startup, and the reconnect is what brings them up so they produce readings to
check.

**4. Analyze.**

```bash
python tests/tc_validation/analyze_tc_log.py --tc-log tc-mytest.log --tx mytest-tx.jsonl
```

Exit status is non-zero if anything was found.

## What the analyzer checks

- **Corrupted reads.** Every payload TestController reports receiving must
  *exactly* equal a frame the emulator sent. A payload that is a suffix of a sent
  frame lost its leading bytes; a payload matching nothing sent covers bytes
  dropped mid-value. Both were seen on 3.41, and both matter, because a corrupted
  numeric reading still parses and is charted as real data rather than raising an
  error.
- **`NullPointerException`** with the first stack frame, for the `writeWithDelay`
  close-path fault.
- **Device thread start/stop pairs**, for threads that exit before identifying.

A note on interpreting the connection count that `bench_emulator.py` reports: it
is *not* a reliable discriminator. We initially expected the broken build to open
one socket per device thread; it does not. Judge builds by the corruption count
and thread survival instead.

## The E5810 exercise

Separate from the Prologix work above, and testing a different claim: that
TestController's Keysight E5810 path fails for one reason only — it asks the
gateway for the device string `inst0`, which the gateway refuses, while the GPIB
address it should have sent goes into the portmapper's `GETPORT` filter argument
where every portmapper discards it. The analysis is in
[`docs/E5810A_PROTOCOL_GUIDE.md`](../../docs/E5810A_PROTOCOL_GUIDE.md) §4–5.

**1. Start the gateway emulator.** It refuses `inst0` with error 3 and accepts
`gpib0,0`–`gpib0,31`, both MEASURED against the physical unit.

```bash
python tests/tc_validation/bench_emulator.py --gateway e5810 --host 127.0.0.1 --label e5810-unpatched
```

Ports 111 and 1024 are not negotiable for a TestController run:
`LXIInterfaceMulti` always asks the portmapper on 111 and uses whatever core port
it returns, and there is no setting for either. `--portmap-port`/`--core-port`
exist for driving the emulator from a test client, not from TestController.

**2. Point TestController at it.** Same isolated-`configDir` method as above,
but with `config/settingsGPIB.e5810.txt` as `settingsGPIB.txt` and
`config/settingsLoad.e5810.txt` as `settingsLoad.txt` — the devices move from
`A:1`–`A:5` to `E:1`–`E:5`. The interface type is written by hand because
`PopupGpibConfig` does not offer "Keysight E5810" in its dropdown, though
`SharedInterfaceList` parses it.

**3. Run the negative control first, with the unpatched jar.** Expect the
emulator to report every `create_link` refused, and the addresses to show up in
the `GETPORT` argument rather than in any device string:

```
  Device strings requested : ['inst0']
  Addresses in GETPORT arg : [1, 2, 3, 4, 5]
  Addresses in device str  : (none)
```

**4. Build and run the patched jar.**

```bash
python tools/patch_testcontroller_e5810.py --check
python tools/patch_testcontroller_e5810.py
```

Six anchored edits across two classes, recompiled with ECJ against your own jar
so no JDK is needed; the original jar is never written to. Expect the two
address rows to swap over, and `analyze_tc_log.py` to work unchanged on the
resulting `-tx.jsonl`.

That flip is the whole result. It is one bit of evidence, but it is the bit the
guide's §4 predicts, and the negative control costs one extra run.

> The patched build is for validation, not for adoption. It derives the device
> name from `scpiPort` instead of threading one through, hardcodes the SICL
> interface name `gpib0`, logs `create_link`'s error code without acting on it,
> and does not support GPIB address 0. The patcher's docstring says why for each.

## Archived captures

`captures/2026-08-14/` holds the evidence for the 3.49 validation. Both builds ran
the identical manual procedure described above.

| Prefix | Build | What it was |
| :--- | :--- | :--- |
| `pos41` | 3.41 | The matched control. 6 corrupted reads in 3820. |
| `pos49` | 3.49 | The matched run. 0 corrupted in 4625; one NPE on reconnect. |
| `3.41` | 3.41 | First control, scripted. All 5 threads died at startup; no reads. |
| `3.49` | 3.49 | Scripted session, 1015 reads, clean. |
| `rc` | 3.49 | Scripted session with two `#RECONNECT` cycles, 890 reads, clean. |

`tc-*.log` is TestController's debug console; `*-tx.jsonl` is the emulator's frame
log; `emu-*.out` is the emulator's console and end-of-run report.

The headline numbers regenerate directly from the archive:

```bash
cd tests/tc_validation
python analyze_tc_log.py --tc-log captures/2026-08-14/tc-pos41.log.gz \
                         --tx     captures/2026-08-14/pos41-tx.jsonl.gz
python analyze_tc_log.py --tc-log captures/2026-08-14/tc-pos49.log.gz \
                         --tx     captures/2026-08-14/pos49-tx.jsonl.gz
```

## Known gaps

- No run against the physical Prologix gateway; all captures are loopback, where
  latencies are lower and far more uniform than a real network.
- `writeReadBin()` is never exercised — the workload issues no binary reads.
- The application-shutdown path is untested; every TestController process here was
  terminated programmatically. The close-path NPE was caught via reconnect instead.
- The E5810 exercise has been verified end-to-end only against a synthetic client
  replaying TestController's RPC sequence, and the patcher has been verified to
  build a jar that compiles and carries the new device string. Neither the
  unpatched nor the patched jar has yet been run against the emulator, and
  nothing has been run against the physical gateway.
- Finding F4 — the RPC socket wait being shorter than the VXI-11 `io_timeout` it
  wraps — cannot be settled on loopback, where the emulator's timeout overhead is
  simulated rather than physical.
