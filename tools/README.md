# Verification tools

BenchForge's value depends entirely on being faithful to the gateway it emulates. These are the checks that keep it honest. They exist so nobody has to re-derive them from a chat log or a hunch.

| Tool | Hardware needed | What it proves |
| :--- | :--- | :--- |
| `verify_offline.py` | none | The emulator matches the recorded hardware profile, and holds its concurrency guarantees. Safe for CI. |
| `verify_hardware.py` | a physical Prologix | The **gateway** is byte-identical to the real controller — the `++` command set, framing, escaping, serial poll. |
| `ab_instruments.py` | a physical Prologix + instruments | The **instruments** behind it are faithful, query by query. This is what a client actually reads values from. |

| `check_errors.py` | any gateway + instruments | Drains and reports every instrument's SCPI error queue. Run it after a session. |
| `capture_e5810.py` | a physical E5810A | Records the VXI-11 wire behaviour: link fields, `reason` bits, error paths, timeouts. |
| `capture_e5810_channels.py` | a physical E5810A | Records the abort channel, interrupt/SRQ channel and `device_docmd`. |
| `verify_frozen_build.py` | a built exe | **Release gate.** Proves the PACKAGED build behaves like the source tree. |
| `make_screenshot.py` | none | Regenerates the README screenshot from the current UI. |

### make_screenshot.py

```bash
python tools/make_screenshot.py
```

Run it after any visible UI change. A stale screenshot is a documentation bug
nothing fails on, so it goes unnoticed — the README carried a pre-split-view
image for a full day of work on that exact page.

It populates both panes with a realistic exchange rather than photographing an
empty grid, and starts from defaults so it cannot disturb a saved session.

> Do not add `--offscreen` for the real screenshot. Offscreen renders the
> layout correctly but resolves no fonts, so every glyph becomes a tofu box.
> The flag exists only for checking structure on a machine with no display.

### verify_frozen_build.py — run this before shipping

```bash
python build_exe.py                    # gated: lint, tests, offline checks
python tools/verify_frozen_build.py    # then verify the BUNDLE
```

A successful PyInstaller build proves nothing about the bundle. Data files can be
absent from the spec, or land where the code does not look, and the failure is
silent: `++help` returns an empty string while every other command still works.
This launches the real executable, talks to it over a socket, and checks
`++help` is byte-identical to the 1879-byte capture.

Two things it handles that are easy to get wrong:

- **Network drives.** Windows will not execute a binary from one, and this repo
  lives on a share. The bundle is staged to local storage first — which also
  proves it is relocatable, as a PyInstaller bundle must be.
- **Leftover settings.** The app restores the last mode and port used. A gate
  that inherits a developer's session passes or fails for reasons unrelated to
  the build, so the launch sets `BENCHFORGE_IGNORE_SETTINGS=1`, which starts
  from defaults **and writes nothing back**.

It refuses to run if something already holds port 1234, rather than cheerfully
testing the source build and reporting a pass.

`buslib.py` and `vxi11.py` are shared plumbing, not tools: the single-connection
`Link`, the collision-checked bus scan, the controller baseline both tools
restore, and a minimal ONC-RPC/VXI-11 client that exposes the wire where PyVISA
hides it.

### Choosing a gateway

`ab_instruments.py` speaks either protocol:

```bash
python tools/ab_instruments.py                              # Prologix, .80
python tools/ab_instruments.py --gateway e5810              # VXI-11, .85
python tools/ab_instruments.py --gateway e5810 --only 5     # one address
```

Over VXI-11 it holds one link per instrument at a 6 s timeout and absorbs the
first read after a link is created — all three measured necessities, documented
in the E5810A profile.

`python -m unittest discover -s tests` covers the same ground as `verify_offline.py` in unit form; the tool is for a fast end-to-end answer.

---

## verify_offline.py

```bash
python tools/verify_offline.py
```

Seven checks:

1. **Command matrix** — every command in `PROLOGIX_HARDWARE_PROFILE.md` §2, including the ones the firmware *rejects* (`++status`, `++lon`), the case-sensitivity rules (§2d) and argument range validation.
2. **`++help`** — replayed byte-for-byte from `core/prologix_help.txt`, the verbatim capture.
3. **Terminators** — controller replies CRLF, instrument data relayed with its own LF, `++eot_char` appended after it (§2e).
4. **Serial poll** — instrument status byte (idle 4, pending 20), MAV isolated per address.
5. **Cross-talk** — several instruments multiplexed over one connection each receive their own reply.
6. **Frame integrity** — no frame is ever truncated or merged, even while a client races its own write→read pair.
7. **Startup bench** — at least four instruments, no placeholder identities, names that resolve to a driver.

### Reading check 6's "silent" count

Check 6 deliberately uses two separate locks so another thread's `++addr` can land mid-sequence — the same window that exists in a client whose write→read pair is not atomic. Under that race a read can address an instrument whose buffer is already drained, and the emulator correctly returns nothing. Those are counted separately as `silent (client race, expected)` and are **not** failures. Only a truncated or merged frame fails.

---

## verify_hardware.py

```bash
python tools/verify_hardware.py --host 192.168.1.80
```

Scans the bus, mirrors the instruments it finds into the emulator, then sends the same 21 payloads to both and compares byte for byte — command matrix, case sensitivity, argument validation, and ESC escaping on the data path.

### Two things this tool learned the hard way

**Hold one connection.** A Prologix serves a single client and drops the previous socket when a new one arrives. An earlier version opened a fresh connection per test case, which meant ~40 connect/displace cycles in a few seconds and left the adapter refusing connections until it was power cycled. `Link` now holds one connection per endpoint and drains between cases. Do not go back to per-case connections.

**Drain before each address probe.** Without it, a slow reply from the previous address arrives during the next one and gets attributed to it — silently mirroring a bus that does not exist. An early run "found" instruments at 1, 2, 3, 6, 7 when the bus actually held 6, 15, 17, 28.

### Refusing a colliding address

The scan reads each address three times and will not mirror one that answers
differently each time. If the disagreement is a strict bit-superset it reports a
**two-talker collision**: GPIB data lines are open-collector and active-low, so
two instruments addressed to talk at once put the bitwise OR of their bytes on
the wire. The result is still plausible ASCII.

This is not hypothetical. A single-read scan once mirrored a collision into the
emulator as a real instrument identity, and it took a bit-level analysis to
notice — the first read happened to land on an alignment that looked fine.

---

## ab_instruments.py

```bash
python tools/ab_instruments.py --host 192.168.1.80
```

Sends a class-appropriate battery — DMM, counter, function generator, supply —
to both the real instrument and its emulated twin, and compares the replies.
Strictly read-only: no `*RST`, no function or output changes, because a supply
or generator on the bench may be driving something.

### Why it does not compare readings byte for byte

A live reading is never identical twice, so the tool compares in three modes:

- **exact** — identity, configuration, setup queries. Must match byte for byte.
- **signature** — measurements. Compares the *skeleton* (every number replaced
  by `N`, catching a missing sign, a dropped element suffix, or plain decimal
  where the hardware uses scientific) and the *implied resolution* of each
  numeric field.
- **info** — reported, never failed. `*STB?` lives here: bit 2 tracks the
  instrument's error queue, so its value reflects whatever happened on the
  bench beforehand rather than emulator fidelity.

Masking digits to `#` is the obvious way to compare a number's format, and it is
wrong. A PM6690 reports `+9.99999962E+06` and `+1.000000038E+07` for the same
measurement a second apart — eight decimals then nine — because both express
0.01 Hz. Counting digits calls that a defect about half the time. Comparing the
resolution is decade-invariant.

For the same reason the tool seeds each emulated instrument from its real
counterpart (function, then nominal value) and runs with measurement jitter
disabled. Without that, an instrument sitting in a non-default mode — the 34411A
on this bench is measuring temperature — reports a mismatch that says nothing
about fidelity.

---

### Reads are gated on MAV, and must stay that way

`buslib.Link.query_instrument` asks the status byte whether a reply is waiting
before it issues `++read`. This is not an optimisation.

An ungated read costs **two** error-queue slots on any query the instrument does
not implement: `-113 "Undefined header"` for the query, then
`-420 "Query UNTERMINATED"` because the read addressed a device with nothing to
say. Running a full probe battery that way overflowed the error queues on four
of the seven instruments on this bench — `-350 "Queue overflow"` — which
destroys whatever real errors were in them. If you are using these tools to
investigate a client's behaviour, that is the evidence you came for.

Separately, discarding a socket buffer without consuming the instrument's reply
leaves it addressed to talk, and the next query logs `-410 "Query INTERRUPTED"`.

Check the queues after a session:

```bash
python tools/check_errors.py --host 192.168.1.80
```

---

### Restoring the controller afterwards

`++savecfg` is `1` on this unit, so any setting you change persists to EEPROM. Note the values before you start and put them back:

```
++addr        ++auto        ++read_tmo_ms
++eos         ++eoi         ++eot_enable      ++eot_char
```

---

## Protocol warnings in the app

The emulator raises three SCPI errors against the addressed virtual instrument
and surfaces them live on the Traffic page:

| Code | Raised when | Why it is hard to see otherwise |
| :--- | :--- | :--- |
| `-420 Query UNTERMINATED` | a read finds nothing queued | the wire shows only silence |
| `-410 Query INTERRUPTED` | a query arrives over an unread reply | the earlier reply just vanishes |
| `-113 Undefined header` | a query names a node the instrument lacks | the instrument answers nothing |

`SYST:ERR?` drains one entry per query, `*ESR?` latches the matching bits until
read, and `*CLS`/`*RST` clear both — so a client can discover these the same way
it would against hardware, or the developer can just watch the panel.

The text is SCPI-99 standard, not vendor-specific, and that costs no fidelity:
all three strings came back byte-identical from the Keithley, Agilent, HP and
Fluke instruments on this bench. Only `-213 "Init ignored"` and
`-350 "Queue overflow"` varied by vendor, and neither is raised.

---

## When to run what

- **Before committing** — `verify_offline.py` plus the unit tests.
- **After touching `prologix_emulator.py` or `device_emulator.py`** — both tools, if hardware is available.
- **After a firmware change on the adapter, or on a different unit** — `verify_hardware.py`, then update `PROLOGIX_HARDWARE_PROFILE.md` with anything that moved. The profile is the specification; the hardware is the authority.

## What is still unverified

`profiles/AR488_ADAPTER_PROFILE.md` is **source-derived, not measured** — no AR488 has been on the bench. The E5810A gateway mode is in the same position. Neither has a hardware verification tool yet, because neither has hardware to verify against.
