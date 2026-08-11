# Keysight / Agilent E5810A LAN/GPIB Gateway Hardware Profile & Specification

This document records the empirical network signatures, open ports, web admin headers, telnet configuration parameters, VXI-11 ONC-RPC protocols, and live instrument performance measured on an official physical **Agilent E5810A LAN/GPIB Gateway** (Serial `MY43000991`, MAC `00:30:D3:07:A4:C6`), probed at `192.168.1.85`.

---

## 📌 1. Hardware Metadata & Identity

| Property | Empirical Hardware Value | Notes |
| :--- | :--- | :--- |
| **Model Name** | Agilent E5810A LAN/GPIB Gateway | Official Keysight/Agilent Hardware |
| **Serial Number** | `MY43000991` | Extracted from Telnet Port 23 |
| **MAC Address** | `00:30:D3:07:A4:C6` | Extracted from HTTP Web Admin Header |
| **GPIB SICL Name** | `gpib0` | VISA / SICL Interface Name |
| **GPIB Controller Address** | `21` | Hardware Gateway Controller Address |
| **GPIB Logical Unit (LU)** | `7` | SICL Logical Unit Number |
| **RS-232 Port Name** | `COM1` | 9600 Baud 8N1 |
| **Open Network Ports** | `23` (Telnet), `80` (HTTP), `111` (VXI-11 RPC), `1024` (RPC Service) | Measured via Port Probe |

---

## 🌐 2. Open Network Services & Protocol Architecture

### A. HTTP Web Admin (Port 80)
- **URL**: `http://192.168.1.85/`
- **Page Title**: `Agilent E5810 (00-30-D3-07-A4-C6)`
- Serves web management interface displaying gateway status, IP configuration, and connected instrument list.

### B. Telnet Configuration Service (Port 23)
- **Banner on Connect**: `Welcome to the E5810 LAN/GPIB Gateway Configuration Utility.`
- **Supported Commands**: `?`, `status`, `reboot`, `exit`, `config`.
- **Parameter Readback**: Reports MAC address, serial number, IP address, DHCP status, GPIB interface name (`gpib0`), and RS-232 parameters.

### C. VXI-11 ONC-RPC Instrument Gateway (Port 111 & Port 1024)
- **RPC Program Number**: `0x0607AF` (395183, VXI-11 Core v1)
- **VISA Resource Syntax**: `TCPIP0::192.168.1.85::gpib0,<addr>::INSTR`
- Translates network VXI-11 ONC-RPC requests (`create_link`, `device_write`, `device_read`, `destroy_link`) directly to GPIB bus cycles.

---

## 🔬 3. Live Physical Instrument VXI-11 Measurement & Performance Profile

### Bus contents, MEASURED 2026-08-10 after the bus was moved from the Prologix

All seven instruments answer through `TCPIP0::192.168.1.85::gpib0,<addr>::INSTR`.
Bytes are shown verbatim from `read_raw()`:

| Addr | `*IDN?` (raw) |
| :--- | :--- |
| 1 | `KEITHLEY INSTRUMENTS INC.,MODEL 2002,4461274,B02  /A02  \n` |
| 2 | `KEITHLEY INSTRUMENTS INC.,MODEL 2001M,1150952,B16  /A02  \n` |
| 3 | `KEITHLEY INSTRUMENTS INC.,MODEL 2010,0636735,A10  /A02  \n` |
| 4 | `FLUKE, PM6690, 979819, V1.32 26 May 2022 09:54\n` |
| 5 | `Agilent Technologies,34411A,MY48005929,2.43-2.40-0.09-46-09\n` |
| 6 | `Agilent Technologies,33250A,0,2.04-1.01-2.00-03-2\n` |
| 7 | `HEWLETT-PACKARD,E3631A,0,2.1-5.0-1.0\n` |

**The gateway relays instrument bytes verbatim, exactly as the Prologix does.**
LF only, no CR, and the Keithley units' two trailing spaces survive intact. The
emulator's existing relay behaviour is therefore correct for both gateways.

> Always read with `read_raw()`. PyVISA's `query()` strips by default, and a
> `.strip()` anywhere in the path silently destroys those trailing spaces.

### Talking to it without provoking false failures

| Rule | Why |
| :--- | :--- |
| **One link per instrument, held open** | Opening a link per query degrades progressively — later addresses start failing and eventually `create_link` returns error 3. It reads as a flaky bus; it is a resource leak. The VXI-11 equivalent of reconnecting to a Prologix per test case. |
| **Timeout ≥ 6 s** | At 1.2–1.5 s, addresses failed intermittently. At 6 s, 55 of 56 queries succeeded across all seven instruments. |
| **~250 ms between queries** | Same measurement run; tighter pacing was where the intermittent failures appeared. |
| **Discard the first query on a new link** | The one failure in that run was the opening query on a freshly created link, followed by seven clean reads. Treat it as warm-up, not as an absent instrument. |

### A wedged gateway looks like a network problem

After the GPIB bus was hot-plugged, the E5810A accepted TCP connections on
23/80/111/1024 but served **zero bytes** to a well-formed HTTP request and
exposed no instruments over VXI-11. It then stopped answering entirely. A power
cycle restored it completely. Hot-plugging the bus can wedge the controller
while leaving the network stack half-alive — check the web page returns content
before concluding anything about the bus.

> Note on diagnosis: on Windows, `ping` reports `Received = n, Lost = 0` even
> when every reply is a `Destination host unreachable` message from the router.
> Read the reply lines, not the summary.

### Timing (earlier measurement, addresses as they were then)

* **Keithley 2010** — handshake `52.29 ms`, sustained `26.57 ms`, `37.64 QPS`
* **Keithley 2001M** — sustained `84.60 ms`, `11.82 QPS`

---

## 🔌 3b. VXI-11 Wire Behaviour — MEASURED, the emulation specification

Captured with `tools/capture_e5810.py`, which speaks raw ONC-RPC so every field
is visible. Full record in `profiles/e5810_vxi11_capture.json`.

### Portmapper (UDP/TCP 111)

| Program | Number | Registered port |
| :--- | :--- | :--- |
| VXI-11 Core | 395183 v1 | **1024** |
| VXI-11 Abort | 395184 v1 | **not registered** |
| VXI-11 Interrupt | 395185 v1 | **not registered** |

The abort and interrupt programs are absent from the portmapper, yet
`create_link` still hands back `abortPort = 975`. An emulator must reproduce
both halves of that: answer `GETPORT` for 395183 only, and still advertise an
abort port in the link reply.

### create_link

| Field | Value |
| :--- | :--- |
| `abortPort` | `975`, constant for every link |
| `maxRecvSize` | `16384`, constant |
| `lid` | large, opaque, **non-sequential** 32-bit values |
| Latency | 1.8 – 2.6 ms |

Link identifiers look like heap pointers — they hover around 29–33 million and
mostly *decrease* by ~1856 per link. Do not emulate small sequential integers:
use large opaque values. 31 links were held open simultaneously without trouble.

**Presence is not checked at link time.** `create_link` succeeds for every
address 0–31 whether or not an instrument is there, and for the bare interface
name `gpib0`. A missing instrument is only discovered when a read times out.
This is fundamentally unlike the Prologix, where addressing and reading are the
same act.

| Device string | Result |
| :--- | :--- |
| `gpib0,0` … `gpib0,31` | accepted, always |
| `gpib0` | accepted |
| `gpib0,99`, `gpib1,5`, `bogus0`, `inst0`, `""` | error **3** *device not accessible* |

Note `inst0` is rejected — this gateway exposes no such logical device.

### device_read: the `reason` bits

All three termination causes confirmed against the 34411A:

| Condition | `reason` | Data returned |
| :--- | :--- | :--- |
| Normal read | `0x04` **END** | full message including its `\n` |
| `requestSize = 10` | `0x01` **REQCNT** | `Agilent Te` — remainder stays queued |
| `termChar = ','`, flag `0x80` | `0x02` **CHR** | `Agilent Technologies,` |

After a REQCNT read the next read returns the remainder and ends on END, so the
instrument's output buffer survives a partial read intact.

### Reading with nothing queued

| `io_timeout` requested | Error | Actually waited |
| :--- | :--- | :--- |
| 500 ms | **15** *I/O timeout* | 666 ms |
| 2000 ms | **15** *I/O timeout* | 2167 ms |

A consistent **~166 ms of overhead** on top of the requested timeout. `reason`
is `0x00` and no data is returned. This is the E5810A's equivalent of the
Prologix silent `++read`, and it is where the `-420 Query UNTERMINATED`
condition lives.

### device_readstb — serial poll

Byte-for-byte agreement with the Prologix `++spoll` measurements, confirming the
status-byte model is **gateway-independent**:

| Addr | Instrument | idle | pending |
| :--- | :--- | :--- | :--- |
| 1–4 | Keithley 2002 / 2001M / 2010, PM6690 | 4 | 20 |
| 5, 7 | 34411A, E3631A | 0 | 16 |
| absent addresses | — | 0 | 0 |

The 0-versus-4 split moves with the instrument's error queue, exactly as
recorded in `bus_capture.json`: the 34411A idled at 4 on the Prologix while its
queue was dirty and idles at 0 now that it has been drained. Bit 2 is the error
queue, not a per-model constant.

**A serial poll of an absent address returns `0` with no error** — the gateway
does not fail the request.

### Locking

| Operation | Result |
| :--- | :--- |
| `device_lock` | error 0 |
| second client `create_link` with lock while held | error **11** *device locked by another link*, no link created |
| `device_unlock` | error 0 |
| `device_unlock` again | error **12** *no lock held by this link* |

### Use after destroy_link — a genuine quirk

| Operation on a destroyed link | Error |
| :--- | :--- |
| `device_write` | **15** *I/O timeout* |
| `device_read` | **15** *I/O timeout* |
| `destroy_link` again | **4** *invalid link identifier* |

Write and read on a dead link report a timeout rather than the invalid-link
error most implementations return, while `destroy_link` on the same dead link
correctly reports error 4. Emulating error 4 across the board would be the
obvious guess and would be wrong.

### device_clear / trigger / remote / local

All return error 0, in 1.4 – 2.4 ms.

### Abort channel, interrupt/SRQ channel, device_docmd

Measured with `tools/capture_e5810_channels.py`; raw record in
`profiles/e5810_channels_capture.json`.

**The abort port is NOT constant.** Three consecutive runs against the same
unit advertised **975, 1005 and 1002** — it is allocated per boot, near the
core channel. An earlier version of this profile recorded 975 as a constant on
the strength of a single capture, and the emulator shipped that fixed value.

| | Result |
| :--- | :--- |
| `create_intr_chan` | **error 0 — supported** |
| `device_enable_srq` | **error 0 — supported** |
| `destroy_intr_chan` | **error 0**, then **6** *channel not established* |
| Callback connection | gateway connects out from `<gateway>:1025+` during `create_intr_chan` and **holds it open** |
| `device_docmd` | **error 8** *operation not supported*, for Send Command, Bus Status, ATN Control and Bus Address |

The interrupt channel requires the client to run an RPC server: the gateway
becomes the caller and invokes `device_intr_srq` (program 395185, procedure 30)
carrying back the opaque handle the client supplied to `device_enable_srq`.

> A client that accepts the callback connection and immediately closes it gets
> no channel — `create_intr_chan` then blocks until the client's own timeout.
> Our first capture did exactly that and misreported it as the gateway hanging.

### The abort channel is not implemented by this gateway — CONFIRMED

Established by three independent probes, with a control proving the RPC client
itself is sound (on the core port, `NULL` returns SUCCESS and a bogus program
returns `PROG_UNAVAIL`).

**1. The advertised abort port is not an RPC server.**

| Call to the advertised port | Result |
| :--- | :--- |
| `NULL` (proc 0), program 395184 | timeout |
| `device_abort` (proc 1) | timeout |
| Deliberately bogus program 999999 | timeout |
| Bare TCP connect, then garbage | accepted, held open, never a byte back |

Anything speaking RPC answers `PROG_UNAVAIL` to a program it does not serve.
This answers nothing. UDP to the same port draws an ICMP port-unreachable.

**2. Program 395184 is served on the CORE port instead** — and it is a stub.

| `device_abort` argument | Result |
| :--- | :--- |
| A valid link id | error **4**, *invalid link identifier* |
| `0xDEADBEEF` | error **4** — *identical* |

It does not distinguish a real link from garbage.

**3. It has no effect.** With a `device_read` genuinely blocked on an empty
address, `device_abort` returned error 4 in 3 ms and the read still ran its
**full 10 s** `io_timeout`.

> The emulator reproduces all of this: it advertises an abort port, accepts
> connections there and stays silent, and answers 395184 on the core port with
> error 4 for any argument. Implementing a *working* abort would let a client
> pass against BenchForge and hang against the hardware — the exact failure the
> tool exists to prevent.

**Abort port allocation.** Observed values across runs: 975, 1005, 1002, 999,
984 — constant within one core-channel connection, different between them. The
emulator allocates once per run, which is a deliberate simplification of a
per-connection value.

### HTTP (port 80)

```
HTTP/1.1 200 OK
DATE: THU JAN 01 00:41:54 1970
Cache-Control: no-cache
Connection: close
Content-Type: text/html
Connection: close
```

1457 bytes, title `Agilent E5810 (00-30-D3-07-A4-C6)`. Two details to reproduce:
the `DATE` header counts from the Unix epoch because the gateway has no clock —
it is effectively an uptime — and `Connection: close` is emitted **twice**.

### Telnet (port 23)

Opens with the telnet negotiation bytes `FF FB 01` (IAC WILL ECHO) before any
text, then:

```
Welcome to the E5810 LAN/GPIB Gateway Configuration Utility.
  Controls GPIB and RS-232 interfaces via the LAN

Commands
  ?                View Available Commands
  exit, quit       Exit WITHOUT Saving Configuration Changes (see reboot)
  reboot           Save Configuration Changes and Restart E5810
  status           View the LAN/GPIB Gateway Connection Status
```

---

## 🎯 4. Architectural Summary & Emulation Objectives for `tc_dev_tool`

To emulate Keysight E5810A / LXI systems alongside Prologix:
1. **Port 1234**: Prologix Ethernet Adapter Emulator (100% verified).
2. **Port 5025**: Raw SCPI LXI Socket Listener.
3. **Port 111 / 1024**: VXI-11 ONC-RPC Gateway Emulator.
4. **Port 80**: E5810A Web Admin Status Page Mock.
