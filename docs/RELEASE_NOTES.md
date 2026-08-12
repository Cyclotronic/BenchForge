# BenchForge Studio — Release Notes

**Version 1.0.0-rc1** · hardware profiles verified 2026-08-10

---

## Read this first if you are testing against TestController

Three behaviours will look like defects and are not. They are the emulator
being faithful to hardware we measured.

### 1. TestController cannot connect to the E5810A persona

It will fail. So will a real E5810A.

TestController's `LXIInterface.open()` creates its VXI-11 link with the device
string `"inst0"` (hardcoded at `LXIInterface.java:62`). A physical Agilent
E5810A **rejects `inst0` with error 3, "device not accessible"** — it addresses
instruments as `gpib0,<primary address>`, and exposes no logical device by that
name. We searched TestController's source for `gpib0` and found no occurrences.

BenchForge refuses `inst0` exactly as the hardware does, and the Debug Log
explains why rather than returning a bare error code. There is deliberately no
lenient mode: a client that passes here and hangs on the bench would be worse
than useless.

Full write-up, with the caveats, in
[`TESTCONTROLLER_OBSERVATIONS.md`](TESTCONTROLLER_OBSERVATIONS.md).

> The E5810A's SICL interface name is configurable and defaults to `gpib0`.
> Renaming it to `inst0` may well make the existing client code work. We have
> not tested that.

### 2. `device_abort` does nothing

The E5810A advertises an abort port that accepts TCP connections and never
speaks — not even `PROG_UNAVAIL` to a bogus program. Program 395184 is answered
on the *core* port instead, as a stub returning error 4 for every link
identifier, valid or garbage. Aborting a genuinely blocked read has no effect;
the read still runs its full `io_timeout`.

Measured three ways with a control proving the client sound. BenchForge
reproduces all of it.

### 3. WARN lines in the Debug Log are not emulator faults

They are SCPI errors a real instrument would have queued in response to your
client's traffic:

| Code | Raised when |
| :--- | :--- |
| `-420 Query UNTERMINATED` | a read found nothing queued |
| `-410 Query INTERRUPTED` | a query arrived over an unread reply |
| `-113 Undefined header` | a query named a node the instrument lacks |

All three look like plain silence on the wire, which is what makes them
expensive to diagnose. That is why the log exists.

---

## What is verified, and how

| Area | Status |
| :--- | :--- |
| Prologix `++` command set, framing, escaping, terminators | **byte-identical** to a physical GPIB-ETHERNET, firmware 01.06.06.00 |
| Prologix instrument replies | **86/86** checks against 7 physical instruments |
| E5810A VXI-11 protocol | link fields, read `reason` bits, error paths, timeouts **measured and reproduced** |
| E5810A instrument replies | **86/86** checks over real VXI-11 |
| Serial-poll status bytes | agree across **both** gateways |
| **AR488** | **SOURCE-DERIVED ONLY — no hardware has been on the bench** |

Reproduce any of it:

```bash
python -m unittest discover -s tests      # 26 tests, no physical hardware
python tools/verify_offline.py            # 7 fidelity checks, no hardware
python tools/verify_hardware.py --host <prologix>
python tools/ab_instruments.py --host <prologix>
python tools/ab_instruments.py --gateway e5810 --host <e5810a>
```

---

## What is in this release

**Two gateway emulators verified against hardware.** Prologix Ethernet, and a
real VXI-11/ONC-RPC E5810A — portmapper, core channel, interrupt channel and
the abort stub, not a raw-socket stand-in.

**Instrument personalities**: DMM, counter, function generator and power
supply, with vendor-correct reply formats. Keithley and Agilent disagree on how
`FUNC?` spells DC volts, on whether integers carry a sign, and on whether a
reading is a bare number or carries its elements — all reproduced.

**SCPI error queue** per instrument, with `*ESR?` latching, feeding a live
Debug Log split from the raw traffic feed. Both panes export.

**A verification toolchain** in `tools/`, documented in its own README,
including the two hardware A/B harnesses and an error-queue drainer.

---

## Known limitations

- **AR488 is unverified.** The profile carries a warning at the top. Treat that
  persona as a best-effort reading of the firmware source until an adapter is
  on the bench.
- **Port 111 usually needs elevation.** The E5810A persona binds the RPC
  portmapper on 111. Without privileges it logs an ERROR line and falls back to
  the raw SCPI socket. Run elevated for full VXI-11.
- **`device_docmd` returns "operation not supported"** — measured; this gateway
  does not expose bus-level operations that way.
- **The abort port is allocated once per run.** The hardware allocates it per
  core-channel connection (975, 1005, 1002, 999 and 984 observed). A deliberate
  simplification.
- **The HP E3631A has no TestController driver.** `AgilentHP E363xA.TXT` covers
  the E3632A/E3633A/E3634A only. It is emulated for bus fidelity but kept out of
  the startup bench, and the library warns before assigning it.
- **Unrecognised *set* commands do not raise `-113`.** They are
  indistinguishable from valid ones the emulator accepts silently, so raising
  there would invent failures.

---

## Reporting a problem

Export both panes from the Traffic page — **Export traffic…** and
**Export log…** — and send them together. The traffic file is what went over
the wire; the log is what the emulator thought about it. A defect is usually
obvious from the pair and very hard to find from either alone.

If the claim is that BenchForge differs from real hardware, the deciding
evidence is a run of `tools/ab_instruments.py` against that hardware. It
compares byte for byte and does not take anyone's word for it.
