# TestController 3.49 — validation of the Prologix fixes

**Date:** 14 August 2026
**Builds compared:** TestController 3.41 (baseline the observations were written against) → 3.49 (build received 14 Aug 2026)
**Method:** static diff of decompiled source, plus three live sessions against BenchForge's Prologix emulator.

This is a follow-up to [`TESTCONTROLLER_OBSERVATIONS.md`](TESTCONTROLLER_OBSERVATIONS.md). The same caution applies: we do not know this codebase, and where we guessed at a mechanism we say so, including where we guessed wrong.

---

## Summary

| Observation | Status in 3.49 |
| :--- | :--- |
| 1 — receive buffer cleared while another thread reads | **Fixed.** 0 corrupted reads in 6530, against 6 in 3820 on 3.41 under the same procedure. |
| 2 — `NullPointerException` during interface shutdown | **Not fixed.** Reproduced on 3.49 with an unchanged stack trace, triggered by *reconnect* rather than only by shutdown. |
| 3 — device threads stop before identifying | **Fixed.** 5/5 threads survived in 3.49; 5/5 died in the 3.41 control. |
| 4 — E5810 cannot address GPIB instruments (`inst0`) | **Not addressed.** `LXIInterface.java:62` still passes the literal `inst0`. |

The root cause of 1 and 3 was not what we proposed. We suggested the unsynchronised `flush()`; the actual fix was elsewhere and is better. See below.

Unlike our first report, Observation 1 here rests on a **positive control**: we reproduced the corruption on 3.41 with the same harness before measuring 3.49, so the test is known to detect the fault it reports absent.

---

## What changed in the code

Decompiled both jars with CFR 0.152 and diffed the source. Every class differs byte-wise because the release is a full recompile, so the byte diff is meaningless; the source diff is not. Across `dk.hkj.comm` and `dk.hkj.shared` the **complete** change set is four files.

### 1. The interface object was being replaced on every call

In `SharedInterfacePrologixEthernet`, and identically in `SharedInterfaceAR488Lan` and `SharedInterfaceKofen`:

```java
// 3.41
this.ci = new SocketInterface(this.address, this.getPort());
this.ci.debugLog = InterfaceThreads.debugAll;

// 3.49
if (this.ci == null) {
    this.ci = new SocketInterface(this.address, this.getPort());
    this.ci.debugLog = InterfaceThreads.debugAll;
}
```

In 3.41 every call to `neededCommInterface()` discarded the shared `SocketInterface` and built a new one. With five device threads on one interface, each thread's view of `ci` could be replaced underneath it at any moment.

This single change accounts for Observations 1 and 3 far better than our buffer-locking theory did. We were looking at the right object and the wrong mechanism: the `ByteBuffer` was not being cleared by a concurrent `flush()`, it was being thrown away wholesale along with the interface that owned it.

### 2. Locking moved from per-method to per-sequence

In `SharedInterface`:

| Method | 3.41 | 3.49 |
| :--- | :--- | :--- |
| `writeRead(int, String, int)` | — | **`synchronized`** |
| `reset()` | — | **`synchronized`** |
| `getDeviceSettings(int)` | — | **`synchronized`** |
| `open(int)` | `synchronized` | — |
| `isData()` | `synchronized` | — |

`writeRead()` becoming synchronized makes `flush()` → `write()` → `read()` atomic against other device threads on the same interface, which is the per-sequence locking the observations asked about. Dropping it from `open()` looks deliberate — that method can sleep up to two seconds on a serial port, and holding the monitor across it would stall every other device thread.

### 3. Incidental changes elsewhere

- `Main.VERSION` `3.41` → `3.49`.
- `InterfaceThreads`: the `*IDN?` null and short-answer checks merged into one branch, which now prints `No asnwer` (sic). Useful — it makes a failed identify visible in the log.
- `DeviceInterface`: interface name list now sorted case-insensitively.

### What did not change

- `SocketInterface.flush()` is still not `synchronized`. On the `writeRead()` path it is now covered by the caller's monitor, so this is consistent.
- **`writeReadBin()` is still not `synchronized`** — in neither `SharedInterface` nor `SharedInterfacePrologixUSB` — and both still call `flush()` on entry. This is the one method our original note singled out, and it remains the odd one out among its neighbours. Our test workload issues no binary reads, so we did not exercise it. We mention it only because it is the same shape as the bug that was fixed.
- `writeWithDelay()` still dereferences `ci` with no null check (`SharedInterface.java:116`, the frame in the Observation 2 stack trace).
- `LXIInterface` is byte-for-byte unchanged. `inst0` is still hardcoded at line 62, and `gpib0` still appears nowhere.

---

## How it was tested

BenchForge's Prologix emulator, single-connection policy (as the physical controller behaves), listening on `127.0.0.1:1235`. Five virtual instruments on A:1–A:5, matching the configured bench:

```
A:1  Keithley 2002     A:2  Keithley 2001M    A:3  Keithley 2010
A:4  Fluke PM6690      A:5  Agilent 34411A
```

TestController was launched with an isolated `configDir` so the live configuration was untouched, with a one-line startup script (`#LOG 0.2`) that begins logging all five devices at 5 Hz and then exits. Reconnects were performed by hand in the UI, since TestController does not permit a reconnect while logging is running.

The emulator recorded every frame it sent. The check for Observation 1 is a cross-check rather than an eyeball: **every payload TestController reports receiving must exactly equal a frame the emulator actually sent.** Anything else is a corrupted read — whether it lost its leading bytes, as in the original report, or dropped a byte from the middle, which we also saw.

---

## Results — the matched pair

These two runs are the substance of the report. Both used the identical manual
procedure: start the application, stop logging, reconnect, restart logging, then
leave it streaming at 5 Hz. The 3.41 reconnect is necessary because its device
threads all die at startup; performing the same steps on 3.49 keeps the
comparison procedure-for-procedure rather than scripted-against-manual.

| | **3.41** | **3.49** |
| :--- | ---: | ---: |
| Reads cross-checked against the wire | 3820 | 4625 |
| **Corrupted reads** | **6** | **0** |
| — leading bytes lost | 5 | 0 |
| — byte dropped mid-value | 1 | 0 |
| Corruption rate | **0.157 %** | **0 %** |
| Device threads started / stopped | 10 / 5 | 10 / 0 |
| Devices streaming after reconnect | 5 | 5 |
| `NullPointerException` | 0 | **1** |

Every corrupted value still parsed as a number, so all six were accepted and
charted as genuine readings. One example, byte-for-byte:

```
emulator sent : +4.99963E+00NVDC,+20586.204733SECS,+41045RDNG#,00EXTCHAN
TC received   : +.99963E+00NVDC,+20586.204733SECS,+41045RDNG#,00EXTCHAN
                 ^ the '4' is gone
```

Hex confirms it: `2B 2E 39` on arrival where the wire carried `2B 34 2E 39`. A
4.99963 V reading was logged as 0.99963 V, silently. The other five lost the
leading `+`, which is the pattern of the original report.

Adding the two earlier scripted 3.49 sessions (1015 and 890 reads, both clean)
gives **6530 reads on 3.49 with zero corruption**. If 3.49 still failed at
3.41's measured rate, the expected number of corrupted reads across that sample
is 10.3, and the probability of observing none is about 3.5 × 10⁻⁵.

That is strong evidence, not proof. These are timing-dependent races and the
sample is one machine, one afternoon, against an emulator on loopback.

Every figure above can be regenerated from the archived captures — see
[`tests/tc_validation/`](../tests/tc_validation/README.md).

### Supporting run — 3.41 control at startup

| Metric | Value |
| :--- | :--- |
| Device threads started | 5 |
| Device threads stopped before identifying | **5** |
| Frames TestController sent | 2 (`++auto 0`, `++mode 1`) |
| Replies emulator sent | 0 |
| Devices identified | 0 |

The control reproduces Observation 3 in a more severe form than originally reported — there, three of five threads died and two survived to stream; here all five died and no `*IDN?` was ever sent. The interface was initialised once and then nothing further happened.

### Supporting run — 3.49 scripted session

| Metric | Value |
| :--- | :--- |
| Device threads started | 5 |
| Device threads stopped | **0** |
| Session length | 63.2 s |
| Commands received by emulator | 3139 |
| Replies sent by emulator | 1015 |
| **Rx payloads matching a sent frame exactly** | **1015 / 1015** |
| Rx payloads that were a suffix of a frame (truncation) | **0** |
| Rx payloads with no matching frame | 0 |
| `NullPointerException` | 0 |
| Protocol warnings from the emulator | 0 |

Replies were distributed evenly across the bus, with no starvation and no cross-delivery:

| Address | Device | Commands | Replies |
| :--- | :--- | ---: | ---: |
| A:1 | Keithley 2002 | 637 | 203 |
| A:2 | Keithley 2001M | 636 | 203 |
| A:3 | Keithley 2010 | 637 | 203 |
| A:4 | Fluke PM6690 | 624 | 203 |
| A:5 | Agilent 34411A | 605 | 203 |

### Supporting run — 3.49 reconnect stress

Same workload with two `#RECONNECT` cycles while logging at 5 Hz, to drive `DeviceSCPI.close()` — the Observation 2 path — while other device threads were active.

| Metric | Value |
| :--- | :--- |
| **Rx payloads matching exactly** | **890 / 890** |
| Truncated reads | 0 |
| Device threads stopped | 0 |
| Exceptions of any kind | 0 |
| `*RST` (the PM6690's `#finalCmd`) observed | 8 — the close path did run |

---

## Observation 2 is still present

This one we did reproduce, on 3.49, during the manual reconnect of the matched
run. The stack is the same as the one we sent originally, allowing for the line
numbers having moved:

```
java.lang.NullPointerException
    at dk.hkj.shared.SharedInterface.writeWithDelay(SharedInterface.java:116)
    at dk.hkj.shared.SharedInterfacePrologixUSB.write(SharedInterfacePrologixUSB.java:76)
    at dk.hkj.comm.GpibInterface.write(GpibInterface.java:64)
    at dk.hkj.main.SCPICommand.writeReadInternal(SCPICommand.java:385)
    at dk.hkj.main.SCPICommand.writeRead(SCPICommand.java:408)
    at dk.hkj.main.DeviceInterface.doCommand(DeviceInterface.java:86)
    at dk.hkj.devices.DeviceSCPI.close(DeviceSCPI.java:157)
    at dk.hkj.main.InterfaceThreads$DeviceThread.run(InterfaceThreads.java:1771)
```

This is consistent with the source: `reset()` gained `synchronized` and still
sets `ci` to null, while `writeWithDelay()` still dereferences `ci` with no null
check. The locking change narrows the window; it does not remove it.

Two things are worth adding to what we said the first time:

- **The trigger is reconnect, not only shutdown.** We assumed this only mattered
  while the application was closing, and therefore that it was harmless. It also
  fires on a mid-session reconnect, which is a normal operation.
- **It is intermittent.** It appeared once in this session, on one of four
  reconnect cycles across our 3.49 runs, and not at all in the 3.41 control's
  two reconnects. We are not suggesting 3.49 made it worse — we have no evidence
  either way, and the original report was against 3.41.

All five devices recovered fully afterwards and streamed normally, so the effect
still appears benign. We mention the change of trigger only because it makes the
condition easier to reach than we implied.

---

## What we could not establish

We would rather list these than let the table above look stronger than it is.

- **Observation 2 was not tested through a real application exit.** We reproduced it on reconnect, which is the same `DeviceSCPI.close()` path, but every TestController process in this exercise was terminated programmatically rather than closed from the File menu. The shutdown case specifically remains unobserved on 3.49.
- **Observation 4 was not exercised at all.** It is unchanged in the source, and these runs used the Prologix path, not the LXI one.
- **One machine, one afternoon, loopback.** These are timing-dependent races. The matched pair is a single trial each, on an emulator over `127.0.0.1`, where latencies are far lower and far more uniform than a real gateway on a real network. The 3.41 control failing harder at startup than the original report did — five threads dead rather than three — is itself a reminder of how much the timing moves.
- **`writeReadBin()` is untested.** The workload contains no binary reads.
- **We have not run 3.49 against the physical gateway.** The original Observation 1 was captured against the emulator, so this is the matching venue, but hardware confirmation would still be worth having.

## Where we were wrong

Our Observation 1 proposed that `SocketInterface.flush()` lacking `synchronized` allowed one thread to clear `bb` while another accumulated into it, and suggested adding `synchronized` there as a cheap test. That was not the bug, and that fix alone would not have addressed it.

We also expected the 3.41 fault to show up as *multiple TCP connections* — one per device thread, each displacing the last on single-connection hardware. It does not. 3.41 opened three connections across the whole session and 3.49 opened two, so connection count is not a useful discriminator. The only trace of duplicate interface creation we can see on the wire is a pair of 3.41 connections one millisecond apart at reconnect, the first displaced immediately:

```
[ 57.126] CONNECT    127.0.0.1:59940
[ 57.126] DISCONNECT 127.0.0.1:59940
[ 57.127] CONNECT    127.0.0.1:59941
```

We built the first version of our harness around connection counting and it would have told us nothing. What actually distinguishes the builds is thread survival and the byte-level cross-check.

---

## Bottom line

**Observations 1 and 3 are fixed.** Under an identical manual procedure, 3.41 lost 6 readings out of 3820 to silent corruption and 3.49 lost none out of 4625, with a further 1905 clean reads from two scripted sessions. Five devices on one `PrologixEthernet` interface now start, identify, reconnect and stream without losing threads or bytes. The fix addresses a cause we had not found, and our proposed cause was wrong.

**Observation 2 is not fixed**, and is easier to reach than we said — a reconnect will do it, not just a shutdown.

**Observation 4 is untouched**, and `writeReadBin()` still stands outside the locking that its neighbours now have.

Thank you for the quick turnaround on this.
