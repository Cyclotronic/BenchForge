# Reply to the Code Review of 2026-08-10

Every finding below was tested rather than read. Where I disagree, the evidence
is included so you can judge it yourself rather than take my word for it.

**Verdict: 9 confirmed, 2 not reproducible, 2 platform-specific or theoretical,
1 correct-but-imprecise.** Four are fixed; the rest are triaged with reasons.

---

## First, two corrections to the review's own claims

These matter because they overstate how much is verified, and this project's
entire value rests on that distinction being exact.

**The Executive Summary says hardware parity is verified "against real
Prologix, Keysight E5810A, and AR488 instruments."** There is no AR488 in this
building and never has been. `profiles/AR488_ADAPTER_PROFILE.md` opens with a
warning that it is derived from reading the firmware source. Listing it beside
two genuinely measured gateways is the kind of claim that, if it reached a
driver developer, would cost us exactly the credibility the tool exists to earn.

**The Verification section lists "Agilent 34401A" among instruments captured
from real hardware.** It is not on the bus. The seven that are: Keithley 2002,
2001M, 2010, Fluke PM6690, Agilent 34411A, Agilent 33250A, HP E3631A. The
34401A was removed from `DEFAULT_BENCH` earlier today precisely because it is
not physically present.

---

## Findings I agree with and have fixed

### CR-09 — build gate cannot tolerate a missing linter · **AGREED, fixed**

Correct, and it was my bug from earlier today. The reasoning was that
`subprocess.run` would raise `FileNotFoundError` when pyflakes was absent, so
`allow_missing=True` could skip it. It does not: `python -m missing_module`
**exits 1**, it does not raise. `FileNotFoundError` only fires if the
interpreter itself is missing.

Verified:

```
$ python -m definitely_not_installed_xyz
No module named definitely_not_installed_xyz
exit code: 1
```

So any machine without pyflakes — the CI case the flag existed for — could not
build at all. Now probed with `importlib.util.find_spec` before invoking.

I also stopped `preflight()` short-circuiting, so one run reports every problem
instead of making you rebuild to find the next.

### CR-01 — orphaned testing tab · **AGREED, removed**

Worse than described. The review says the handlers are missing; they are, but
`self.tab_testing` is never created either (`_init_ui` builds four pages), so
`_init_tab_testing` would have raised `AttributeError` on its first line.

**Why it was like that:** a remnant of the Tkinter-to-Qt migration. The panel
was ported, the wiring was not.

I removed it rather than implementing it, which is a deliberate disagreement
with the suggested fix. The functionality is not lost — it runs from the CLI
and is covered by tests:

```
python tests/run_validation_harness.py              # 12/12 protocol checks
python tests/run_validation_harness.py --benchmark  # QPS stress
```

Building the UI properly means worker threads so network calls do not block the
Qt main thread. That is a **feature**, and adding features at release-candidate
stage is how you ship untested code. The removal is commented with the CLI
equivalent so the next person does not rediscover it.

### CR-02 — telemetry race · **AGREED, fixed**

Correct. `qps_window` and `latency_window` are appended from server threads and
rebuilt by the 50 ms GUI timer. `sum()` over a list another thread is appending
to can raise, and the rebuild can silently drop samples.

Both sides now take a `threading.Lock`. I also locked `_clear_snoop`, which the
review did not mention but mutates the same lists from the GUI thread.

### CR-04 — unbounded accumulation · **AGREED, fixed**

Correct, and also mine from today. `self._threads` retained every `Thread`
object for the process lifetime; dead ones are now pruned on each accept. The
abort-port `held` list is capped at 16, oldest closed first.

**Why the `held` list exists at all:** the E5810A accepts connections on its
advertised abort port and never answers them. That is measured, and a client
following the VXI-11 specification will hang there — reproducing it is the
point. But an emulator that retains those connections without limit hands a
misbehaving client a way to exhaust our descriptors, which the hardware's
behaviour does not justify.

---

## Findings I agree with, not yet fixed

### CR-05 / CR-06 — sockets closed inside `try:` · **AGREED**

Confirmed by inspection: **zero** `finally:` blocks across both files, with 3
and 15 `.close()` calls respectively. On a timeout the close is skipped.

Not yet fixed because both files are test harnesses that run briefly and exit,
so the leak is bounded by process lifetime. Real, low urgency, and mechanical —
happy to do it.

### CR-08 — `active_clients` mutated without a lock · **AGREED**

Confirmed: it is a plain `list`, appended at line 118 and removed at 151–152
from per-client threads, with no lock. The GUI also reads it for the client
count tile.

Worth noting the sibling class does this correctly — `PrologixEmulatorServer`
guards its `active_clients` with `_socket_lock`. This one was missed.

### CR-03 — `VirtualInstrument` state unlocked under `POLICY_MULTI` · **AGREED, with context**

Correct as stated. `error_queue`, `esr_bits`, `function` and `reading_number`
are mutated without synchronisation.

**Why the code is like this, and why it has not bitten:** real GPIB hardware is
single-client. A Prologix serves exactly one connection and drops the previous
one — behaviour we reproduce deliberately, and `POLICY_SINGLE` is the default
for that reason. `POLICY_MULTI` is an explicitly non-faithful convenience mode.

So the race is real but confined to a mode that is already documented as
diverging from hardware. It should still be fixed — a developer who switches
modes should not get corrupted state — but it is not on the path that matters
for fidelity testing.

---

## Findings I could not reproduce

### CR-12 — double newlines on the LXI raw socket · **DISAGREE**

Tested every reply type through the raw socket server:

```
*IDN?  -> b'KEITHLEY INSTRUMENTS INC.,MODEL 2002,4461274,B02  /A02  \r\n'
READ?  -> b'+5.000074E+00NVDC,+20567.086438SECS,+40730RDNG#,00EXTCHAN\r\n'
FUNC?  -> b'"VOLT:DC"\r\n'
           endswith CRLF: True    double newline: False
```

Single CRLF in every case, no doubling.

**Why the code is safe:** `registry.process_command()` returns a bare string
with no terminator — terminators are added by whichever transport is serving,
because the Prologix and LXI paths terminate differently. The premise that
`resp` might already be CRLF-terminated does not hold for any current producer.

A defensive guard would be harmless, but I would rather not add a conditional
that implies a case which cannot occur; it invites someone to "preserve" a
terminator that should never be there.

### CR-13 — `print()` fails when `sys.stdout` is `None` · **DISAGREE for this build**

The concern is legitimate in general — PyInstaller windowed builds historically
set `sys.stdout` to `None`, and `print()` then raises `AttributeError`.

It does not happen here. The packaged build was launched and verified this
evening: it started, bound its port, and served all 1879 bytes of `++help`
byte-identically. The banner in `main()` prints before any of that, so if
`print()` raised, nothing would have run. PyInstaller 6.22 provides a null
stream rather than `None`.

Filed as a portability note, not a defect. If we ever pin an older PyInstaller,
it becomes real.

---

## Findings that are conditional

### CR-07 — `os.path.isfile()` raises on a long string · **PLATFORM-SPECIFIC**

Reproduced the described input on Windows:

```
isfile -> False        # returns cleanly, does not raise
```

The `OSError: [Errno 63] File name too long` in the review is **macOS**
behaviour. Since this repository is worked from both a Mac and a Windows box,
that makes it a real bug on one of the two — a genuine catch that would have
looked like a non-issue if either of us had only tested on one platform.

Cheap to guard: check for a newline before treating the argument as a path.

### CR-10 — AR488 emits the wrong error text · **CORRECT IN EFFECT, IMPRECISE AS STATED**

The review says the AR488 "delegates to Prologix" for parameter errors. That is
not quite right — AR488-specific commands do emit `Invalid parameter`
correctly (`ar488_emulator.py:113,117`). But commands shared with the Prologix
fall through to the parent handler, which uses its own message. Measured:

```
++addr 99            -> b'Unrecognized command\r\n'
++read_tmo_ms 99999  -> b'Unrecognized command\r\n'
++eos 9              -> b'Unrecognized command\r\n'
```

Per the firmware source, `errorMsg()` case 2 gives `Invalid parameter`, so
these are wrong.

**Why I have not fixed it yet, and want your view:** the AR488 profile is
**source-derived and unverified — no AR488 has ever been on this bench.**
Changing behaviour here means encoding one more inference from reading C++,
on top of the inferences already there. I am confident in the reading, but
"confident in my reading" is exactly the standard this project refuses to
accept for the Prologix and E5810A.

My preference: fix it, mark it explicitly source-derived in the profile, and
re-verify the whole AR488 persona when the adapter arrives.

---

## Finding I disagree with on fidelity grounds

### CR-11 — semicolon splitting inside quoted strings · **MEASURED: the review is right. Implemented.**

> **Superseded.** My original rebuttal is kept below because the reasoning
> still matters, but the conclusion was wrong and the hardware settled it.

Measured on the **Agilent 33250A at GPIB 6**, through the physical Prologix,
with the output off and the display restored afterwards:

```
control:  DISP:TEXT "AB"   ->  DISP:TEXT?  '"AB"'    error queue empty
test:     DISP:TEXT "A;B"  ->  DISP:TEXT?  '"A;B"'   error queue empty
```

The instrument keeps the semicolon and reports no error. It does not split.

`VirtualInstrument.split_unquoted()` now performs quote-aware splitting for
both `'` and `"`, treating a doubled quote as an escaped literal, and both the
compound-detection test and `_handle_compound` use it. `test_20` pins the
behaviour and cites the measurement.

**What I got right and wrong.** Insisting on measurement was right — but I
framed it as though the burden were on the reviewer, when the cost of checking
was two minutes on a bench that was already powered up. The correct response to
"we do not know what the hardware does" is to go and find out, not to defer.

The original reasoning, retained:

---

### CR-11 — original rebuttal · **DISAGREE, deliberately**

The SCPI standard does say a semicolon inside a quoted string is not a
separator, so as a statement about SCPI the finding is correct.

I still do not want to fix it, and the reason is the whole premise of this
tool.

**We do not know what the real instruments do.** The emulator's job is not to
implement SCPI correctly; it is to behave the way the hardware behaves. Plenty
of instrument firmware splits on semicolons naively. If I add a quote-aware
tokenizer and the Keithley 2002 does not have one, I have made the emulator
*less* faithful while making it more standards-compliant — and the developer
relying on us would chase a difference that only exists in our code.

Tested current behaviour:

```
FUNC:ON "A;B"   -> None      (silent)
```

Silence is also what real hardware gives for an unrecognised function, so the
observable result is plausibly identical anyway. And no command used by any
driver on this bench contains a semicolon inside quotes — the counter's real
form is `FUNC:ON "FREQ:RAT 1,2"`.

**This is measurable.** The Prologix is on the bench. If you want it settled,
send `FUNC:ON "A;B"` to a couple of instruments and see whether they answer,
error, or stay silent. Then we implement whatever they do. Until then, changing
it would be substituting a specification for a measurement, which is the one
habit this codebase has spent the most effort removing.

### CR-14 — test socket not closed in `finally:` · **AGREED, trivial**

Correct. Test-only, single connection, process exits immediately after. Will
tidy.

---

## Summary

| ID | Verdict | Status |
| :--- | :--- | :--- |
| CR-01 | Agreed (worse than stated) | **Fixed** — removed, CLI equivalent documented |
| CR-02 | Agreed | **Fixed** — lock added, incl. a path the review missed |
| CR-03 | Agreed, non-default mode | **Fixed** — reentrant lock; listener called outside it |
| CR-04 | Agreed | **Fixed** — threads pruned, sockets capped at 16 |
| CR-05 | Agreed | **Fixed** — all 3 sites close in `finally:` |
| CR-06 | Agreed | **Fixed** — all 15 sites, incl. 2 with `s1`/`s2` |
| CR-07 | Real on macOS, not Windows | **Fixed** — `looks_like_path()`, both call sites |
| CR-08 | Agreed | **Fixed** — `_clients_lock` |
| CR-09 | Agreed | **Fixed** — `find_spec` probe, no short-circuit |
| CR-10 | Correct in effect | **Fixed** — `ERR_BAD_ARGUMENT` seam; source-derived, flagged |
| CR-11 | **Measured: review is right** | **Fixed** — `split_unquoted()`, `test_20` |
| CR-12 | **Not reproducible** | No action |
| CR-13 | **Not reproducible on PyInstaller 6.22** | Portability note |
| CR-14 | Agreed | **Fixed** — `conn` closed in `finally:` |

**All 14 are closed.**

### How CR-10 was fixed

The Prologix answers the same string whether a command is unknown or its
argument is out of range, so the two cases were never distinguished in the
parser. They are now separate constants:

```python
ERR_UNRECOGNIZED = "Unrecognized command"   # the command was not understood
ERR_BAD_ARGUMENT = "Unrecognized command"   # understood, argument rejected
```

Nine rejection sites route through `bad_argument()`; two remain
`unrecognized()` — the case-sensitivity guard and the final fallthrough, which
are genuine unknown-command cases. **The Prologix is byte-for-byte unchanged**,
verified against the physical controller, because both constants hold the same
string for it.

The AR488 overrides one constant:

```python
ERR_BAD_ARGUMENT = ERR_INVALID_PARAM        # errorMsg(2) in AR488.ino
```

| | `++addr 99` | `++AUTO` | `++bogus` |
| :--- | :--- | :--- | :--- |
| AR488 | `Invalid parameter` | `0` (case-insensitive) | `Unrecognized command` |
| Prologix | `Unrecognized command` | `Unrecognized command` | `Unrecognized command` |

**This remains source-derived and unmeasured.** It is flagged in
`AR488_ADAPTER_PROFILE.md` as the first thing to check when an adapter reaches
the bench, and `test_21` pins it so the hardware can contradict it loudly.

### A note on how CR-05/06 were fixed

`validation_harness.py` repeats one shape 15 times, so I transformed it rather
than hand-editing. **The first attempt was wrong and worth recording:** it
matched the `except` block alone, which is not specific enough. One site uses
`s1`/`s2` and carries an extra `chk.passed = False` in its `except` — the
inserted `finally:` landed in the middle and moved that assignment out of the
error path, so the check would have reported failure unconditionally.

The harness still passed 12/12, because that branch is only reached under
`multi_connection`. Lint caught it, via an undefined `s`.

The second attempt matches the **whole block**, from a `try:` that opens with
`s = socket.socket(` through its `except`, so the two shapes cannot be
confused. The two `s1`/`s2` sites were then edited by hand. Verified after:
lint clean, 12/12 harness, zero bare `.close()` calls remaining.

## What the review did not catch

Worth recording, because it is the most serious defect found today and static
analysis could not have seen it.

`core/prologix_help.txt` had been silently corrupted from **1879 to 1845
bytes** — all 34 CRLF terminators stripped. `++help` would no longer have
matched hardware.

The file has been stored as LF in git since commit `5dab01b`, committed under
`core.autocrlf=true`. The correct bytes existed only in the working tree,
restored on each checkout by autocrlf. Adding `core/prologix_help.txt -text` to
`.gitattributes` correctly stopped git mangling it — and thereby stopped the
conversion that had been hiding the loss.

Restored from the packaged bundle, and `test_19` now asserts the capture is
exactly 1879 bytes with 34 CRLFs. I verified that test fails on a normalised
file rather than assuming it would.

**19/19 tests, 7/7 offline fidelity checks, lint clean.**
