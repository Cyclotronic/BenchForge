# Observations from emulator development — offered for what they're worth

**Context:** BenchForge is a GPIB gateway emulator. It exists so that instrument driver work can be done without hardware on the desk, and it is validated by diffing it byte-for-byte against a physical Prologix GPIB-ETHERNET controller (firmware `01.06.06.00`).

While getting the emulator to behave faithfully, we ran TestController against it a great many times and read the decompiled source to understand what a client legitimately expects. In the course of that we noticed a few things that *might* be worth a glance. They are offered in exactly that spirit.

---

## Please read this part first

We want to be upfront about our standing here:

- **We do not know this codebase.** We have read fragments of decompiled source to answer specific questions about what a client sends. We have no view of the design intent, the history, or the constraints behind any of it.
- **Most of what looked like a TestController problem turned out to be ours.** Over the course of this work we found and fixed the following in our own emulator, every one of which initially presented as "TestController is misbehaving":
  - `++spoll` not implemented at all, so read loops stalled
  - `FUNC?` returning a generic acknowledgement instead of a function name
  - `FUNC?` returning `VOLT:DC` where the instrument family reports `VOLT`
  - a counter reporting itself as a voltmeter
  - a single controller-side response queue instead of per-instrument output buffers, which cross-delivered replies between devices
  - CRLF appended to instrument data, where real hardware relays the instrument's own LF untouched
  - accepting `++AUTO` and out-of-range arguments that the real controller rejects
  
  That record should be weighed when reading what follows. Our prior has been wrong far more often than it has been right.
- **The items below are inference from reading code plus byte-level observation.** We have not attached a debugger to TestController, and we have not reproduced any of these inside TestController itself. It is entirely possible we have misread the decompiled output, missed a lock held at a higher level, or misunderstood the threading model.
- **Nothing here is urgent from our side.** The emulator is not blocked. We mention them only because they were visible from an unusual angle.

If any of this is already known, already fixed, or simply wrong, please disregard it with our apologies for the noise.

---

## Observation 1 — receive buffer may be cleared while another thread is reading it

This is the one we have the most evidence for, and the one we'd most gently suggest a look at.

### What we saw

Running five devices over one `PrologixEthernet` interface, values occasionally arrive with leading characters missing:

```
127.0.0.1: Rx: <000001E+07>        30 30 30 30 30 31 45 2B 30 37 0A
PM6690: Rx as numbers <1.0E7>

127.0.0.1: Rx: <9.999999984E+06>   39 2E 39 39 39 39 39 39 39 38 34 45 2B 30 36 0A
PM6690: Rx as numbers <9999999.984>
```

The emulator sent `+1.000001E+07` and `+9.999999984E+06`. The received values are those strings with the first few characters removed — `+1.` and `+` respectively. The remainder is intact and in order, and the value still parses as a number, so it is accepted and displayed as a real reading rather than raising an error.

### What we checked on our side first

We reproduced the same concurrency pattern against the emulator and captured every byte it emitted:

```
total bytes    : 650
messages (LF)  : 50
malformed      : 0
partial reads  : 0
cross-delivered: 0
```

Each response was a single complete `sendall()` of a well-formed message. We could not make the emulator emit a short frame.

### The code we think may be involved

In `dk.hkj.comm.SocketInterface`:

```java
protected ByteBuffer bb = new ByteBuffer();        // line 27, instance state

public void flush() {                              // line 99  — no `synchronized`
    this.bb.clear();
    ...
}

public synchronized String read(int timeout) {     // line 295 — `synchronized`
    ...
    this.bb.append((char)c);                       // accumulates until CR/LF
    ...
}
```

And in `dk.hkj.shared.SharedInterfacePrologixUSB`:

```java
public byte[] writeReadBin(...) {                  // line 69  — no `synchronized`
    ...
    this.ci.flush();
    ...
}
public synchronized boolean write(...)             // line 80  — `synchronized`
public synchronized void writeControl(...)         // line 87  — `synchronized`
public synchronized String read(...)               // line 101 — `synchronized`
```

`read()` is synchronized and `flush()` is not, but both operate on the same `bb`. `writeReadBin()` is not synchronized and calls `flush()` on entry, while its three sibling methods are synchronized.

If a second device thread enters `writeReadBin()` while the first is part-way through accumulating a message, `bb.clear()` would discard the bytes gathered so far. The first thread would then continue appending and return the tail. That is the shape of what we observe.

### What we are unsure about

- Whether some outer lock we haven't found already serialises these paths.
- Whether the omitted `synchronized` on `flush()` and `writeReadBin()` is deliberate — there may well be a reason.
- Whether the truncation has a different cause entirely and the buffer sharing is a red herring.

### A cheap way to test it, if you think it's worth the time

Adding `synchronized` to `SocketInterface.flush()` and re-running a multi-device session would confirm or eliminate the theory in one go, without committing to any wider change.

---

## Observation 2 — `NullPointerException` during interface shutdown

### What we saw

On closing a session:

```
Thread for PM6690
java.lang.NullPointerException
    at dk.hkj.shared.SharedInterface.writeWithDelay(SharedInterface.java:116)
    at dk.hkj.shared.SharedInterfacePrologixUSB.write(SharedInterfacePrologixUSB.java:76)
    at dk.hkj.comm.GpibInterface.write(GpibInterface.java:64)
    at dk.hkj.main.SCPICommand.writeReadInternal(SCPICommand.java:385)
    at dk.hkj.main.SCPICommand.writeRead(SCPICommand.java:408)
    at dk.hkj.main.DeviceInterface.doCommand(DeviceInterface.java:85)
    at dk.hkj.devices.DeviceSCPI.close(DeviceSCPI.java:157)
    at dk.hkj.main.InterfaceThreads$DeviceThread.run(InterfaceThreads.java:1751)
```

`DeviceSCPI.close()` issues the driver's `#finalCmd` (`*RST` for the PM6690), which reaches `writeWithDelay` after `ci` appears to have been nulled — presumably by another device thread closing the shared interface first.

### Why we mention it

It is harmless in the sense that everything is shutting down anyway. We raise it only because it is the same shape as Observation 1 — several device threads sharing one interface object with per-method rather than per-sequence locking — so if that turns out to be worth addressing, this may fall out of the same change.

---

## Observation 3 — device threads sometimes stop before identifying

### What we saw

At startup, with five devices configured on one interface:

```
Start thread for: PrologixEthernet A:1  - Agilent 34401A
Start thread for: PrologixEthernet A:2  - Fluke PM6690
Start thread for: PrologixEthernet A:15 - Keithley 2001M
Start thread for: PrologixEthernet A:6  - Keithley 2010
Start thread for: PrologixEthernet A:22 - Keysight 34461A
Stopping thread for: PrologixEthernet A:15 - Keithley 2001M
Stopping thread for: PrologixEthernet A:22 - Keysight 34461A
Stopping thread for: PrologixEthernet A:6  - Keithley 2010
```

Three threads stop before any bytes are sent on their behalf. The two that survive then identify and stream normally. A manual reconnect brings all five up.

### What we cannot say

We have no visibility into why those threads stop, and we have not ruled out our own emulator as the cause — for instance if we were slow to answer during the initial burst. We mention it only as a companion data point to the two above, since all three involve several device threads contending for one interface at once.

We are happy to re-run this with any additional logging that would help, or to introduce a deliberate delay on our side to test the timing hypothesis.

---

## Observation 4 — the E5810 interface may not be able to address GPIB instruments

This one we are reasonably confident about, because the hardware behaviour is
measured rather than inferred. We raise it gently all the same.

### What the code does

`dk.hkj.comm.LXIInterface.open()` creates the VXI-11 link:

```java
public synchronized void open() {
    int port = this.rpc.portmapGetport(395183, 1, 6, this.scpiPort);   // line 55
    this.lxirpc = new RPC(this.address, port);
    this.lxirpc.defineCall(395183, 1, 10);                             // create_link
    int clientId = 10;
    this.lxirpc.addParam(clientId);
    this.lxirpc.addParam(0);
    this.lxirpc.addParam(0);
    this.lxirpc.addParam("inst0");                                     // line 62
    ...
}
```

`SharedInterfaceKeysightE5810` reaches this through `LXIInterfaceMulti`, which
stores the GPIB address as `this.port` and passes it to `LXIInterface.setPort()`.
That value becomes `scpiPort`, and its only uses appear to be:

- the fourth argument to `portmapGetport(395183, 1, 6, scpiPort)` — the `port`
  field of an ONC-RPC `PMAPPROC_GETPORT` call, which the portmapper ignores on a
  lookup, and
- a key in `LXIInterfaceMulti`'s own `HashMap`.

The device string passed to `create_link` is the literal `"inst0"` in every
case. We searched the decompiled source for `gpib0` and found no occurrences at
all; `inst0` appears exactly twice, hardcoded, in `LXIInterface.java:62` and
`LXI.java:33`.

### What the hardware does

Measured against a physical Agilent E5810A (MAC `00:30:D3:07:A4:C6`), driving
raw ONC-RPC so the error codes are visible:

| `create_link` device string | Result |
| :--- | :--- |
| `gpib0,0` … `gpib0,31` | accepted, for every address |
| `gpib0` | accepted |
| `inst0` | **error 3, "device not accessible"** |
| `bogus0`, `gpib1,5`, `gpib0,99` | error 3 |

The gateway addresses instruments as `gpib0,<primary address>`. It exposes no
logical device named `inst0`.

### Why we think the two do not meet

If the above is right, a link to `"inst0"` is refused by the gateway, so no
instrument is ever addressed and the configured GPIB address never reaches the
bus. `"inst0"` is the correct device string for a *direct* LXI instrument — a
34461A, say — so the base `LXIInterface` looks right for that case. It is the
GPIB gateway case that seems not to have been given its own device string.

### What we are unsure about

- **The E5810A's SICL interface name is configurable.** It is `gpib0` by
  default on this unit. We have not tried renaming it to `inst0`, which might
  well make the existing code work. If that is the intended setup, this whole
  observation is just a documentation matter and we apologise for the noise.
- We have not run TestController against a physical E5810A ourselves; this is
  code reading plus a hardware measurement, not a reproduction.
- We may simply have missed an override. We looked for one and did not find it,
  but absence of evidence in decompiled output is weak evidence.

### What it means for the emulator

We intend BenchForge's E5810A emulation to reject `inst0` exactly as the
hardware does, because the entire value of the tool is that it behaves like the
real gateway. That does mean TestController will fail to connect to our emulated
E5810A in the same way it would fail against the real one — which we think is
the useful outcome, but we wanted to say so plainly rather than have it look
like an emulator defect.

If it would help, we can add an opt-in lenient mode that accepts `inst0` and
maps it to a configured default address, purely so development can continue
while the question is settled.

---

## A note on SRQ

We looked, because it affects what we build: we found no use of VXI-11
`device_readstb` (13), `device_trigger` (14), `device_lock`/`unlock` (18/19),
`device_enable_srq` (20) or `create_intr_chan` (25) anywhere in the source.
`LXIInterface` declares and uses five procedures only — `create_link` (10),
`device_write` (11), `device_read` (12), `device_clear` (15) and
`destroy_link` (23).

So service requests appear not to be part of the LXI path at all, and we have
scoped our emulator's interrupt-channel support accordingly. If that is wrong
and SRQ-driven reads are planned, we would rather know early.

---

## What we ruled out on the emulator side

So that none of the above sends anyone down a path we have already walked — the emulator has been verified against a physical Prologix controller and matches it byte-for-byte on:

| Area | Status |
| :--- | :--- |
| Full documented command matrix | 0 mismatches |
| Commands the firmware rejects (`++status`, `++lon`, `++invalidcmd`) | 0 mismatches |
| `++help` output | byte-identical, 1879 bytes |
| ESC escaping of `+`, CR, LF on the data path | 0 mismatches |
| Command case sensitivity | 0 mismatches |
| Argument range validation | 0 mismatches |
| Line framing (split packets, multiple per packet, CRLF, blank lines) | 0 mismatches |
| Terminators — CRLF for controller replies, instrument data relayed verbatim | 0 mismatches |
| `++eot_enable` / `++eot_char` | 0 mismatches |
| `++read <char>` partial reads | 0 mismatches |
| `++spoll` status byte, idle and with data pending | 0 mismatches |
| Single-connection displacement | matches |
| Per-instrument output buffers under multiplexing | 50/50 correct deliveries |

Instrument-side responses are taken from live captures of the physical instruments on the bus (Keithley 2010 / 2001M / 2002, Agilent 34411A, Fluke PM6690, Keysight 34461A) rather than invented.

---

## Offer

If any of this is worth pursuing, we can supply:

- the full byte-level session logs behind each observation
- the reproduction scripts, which drive the emulator through TestController's exact command sequence
- a build of the emulator with configurable response delay, if timing turns out to matter
- any additional instrumentation on our side that would help isolate a question

And if we have simply misread the code, we would genuinely appreciate being told so — it improves the emulator's fidelity, which is the whole point of the exercise.

Thank you for TestController, and for making the device driver format open enough that an emulator could be built against it at all.
