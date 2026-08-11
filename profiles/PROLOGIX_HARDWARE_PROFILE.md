# Physical Prologix GPIB-Ethernet Adapter Profile & Specification

This document records the empirical behavior, socket signatures, line endings, timeout defaults, error strings, full manual command matrix, and live physical instrument measurement performance of an official **Prologix GPIB-ETHERNET Controller** (Firmware `01.06.06.00`), probed at `192.168.1.80:1234`.

---

## 📌 1. Hardware Metadata & Identity

| Property | Empirical Value | Notes |
| :--- | :--- | :--- |
| **Device Model** | Prologix GPIB-ETHERNET Controller | Official Prologix Hardware Adapter |
| **Firmware Version** | `01.06.06.00` | Extracted via `++ver` command |
| **Default TCP Port** | `1234` | Standard TCP socket listening port |
| **Default Line Ending** | CRLF (`\r\n` / `0x0D 0x0A`) | Appended to all ASCII command responses |
| **Default `++auto`** | `0` | Manual read mode requiring `++read` |
| **Default `++addr`** | `4` | Saved startup GPIB primary address |
| **Default Read Timeout**| `200 ms` | Default `++read_tmo_ms` |
| **Default `++mode`** | `1` | Controller mode |
| **Default `++eos`** | `3` | EOS mode 3 (No appended termination on instrument bytes) |
| **Default `++eoi`** | `1` | Assert EOI line on last byte |

---

## 📖 2. Complete Prologix User Manual Command Suite Matrix

Every command listed in the official Prologix GPIB-ETHERNET Controller User Manual was tested against `192.168.1.80:1234`:

| Manual Command | Hardware Socket Response | Executed State / Behavior | Parity Status |
| :--- | :--- | :--- | :---: |
| `++ver` | `Prologix GPIB-ETHERNET Controller version 01.06.06.00\r\n` | Firmware query | **MATCH** |
| `++addr [0-30]` | Query: `4\r\n` \| Set: *[Silent Ack]* | Sets active GPIB primary address | **MATCH** |
| `++auto [0\|1]` | Query: `0\r\n` \| Set: *[Silent Ack]* | Toggles auto read mode | **MATCH** |
| `++mode [0\|1]` | Query: `1\r\n` \| Set: *[Silent Ack]* | Toggles Controller/Device mode | **MATCH** |
| `++read_tmo_ms` | Query: `200\r\n` \| Set: *[Silent Ack]* | Sets read timeout in milliseconds | **MATCH** |
| `++eos [0-3]` | Query: `3\r\n` \| Set: *[Silent Ack]* | Sets EOS mode (`0`=CRLF, `1`=CR, `2`=LF, `3`=None) | **MATCH** |
| `++eoi [0\|1]` | Query: `1\r\n` \| Set: *[Silent Ack]* | Toggles EOI line assertion | **MATCH** |
| `++eot_enable` | Query: `0\r\n` \| Set: *[Silent Ack]* | Toggles EOT character appending | **MATCH** |
| `++eot_char` | Query: `0\r\n` \| Set: *[Silent Ack]* | Sets EOT ASCII character | **MATCH** |
| `++savecfg` | Query: `1\r\n` \| Set: *[Silent Ack]* | Saves settings to internal EEPROM | **MATCH** |
| `++srq` | Query: `0\r\n` | Queries SRQ status line | **MATCH** |
| `++clr` | *[Silent Ack]* | Asserts GPIB Selected Device Clear | **MATCH** |
| `++loc` | *[Silent Ack]* | Returns active instrument to Local mode | **MATCH** |
| `++llo` | *[Silent Ack]* | Asserts GPIB Local Lockout | **MATCH** |
| `++trg` | *[Silent Ack]* | Sends Group Execute Trigger | **MATCH** |
| `++ifc` | *[Silent Ack]* | Asserts Interface Clear on GPIB bus | **MATCH** |
| `++rst` | *[Silent Ack]* (Socket reset/disconnect) | Resets network & GPIB controller stack | **MATCH** |
| `++spoll [addr]`| Returns status byte integer or times out | Conducts GPIB serial poll | **MATCH** |

### Invalid Command Handling
Commands not supported by the controller firmware (such as `++invalidcmd`, `++status`, or `++lon`) return:
```text
Unrecognized command\r\n
```

---

---

## 2b. Data-Path Escaping — MEASURED

Probed against the physical controller (firmware `01.06.06.00`) with a Keithley 2010 at GPIB 6.

Characters carrying protocol meaning are escaped with `ESC` (`0x1B`) when a client sends them **as instrument data**:

| Character | Hex | Why it needs escaping |
| :--- | :--- | :--- |
| `ESC` | `0x1B` | The escape prefix itself |
| `+` | `0x2B` | Leads the `++` command introducer |
| `CR` | `0x0D` | Command terminator |
| `LF` | `0x0A` | Command terminator |

### Measured behaviour

| Test | Bytes sent | Hardware result |
| :--- | :--- | :--- |
| ESC prefix stripped | `*ESE <ESC>+37` → `2A 45 53 45 20 1B 2B 33 37` | `*ESE?` returns `37`, `:SYST:ERR?` returns `0,"No error"` |
| Escaped `LF` does **not** terminate | `++ver<ESC><LF>++ver` | `Unrecognized command\r\n` — one line, unrecognisable token |
| Escaped `CR` does **not** terminate | `++ver<ESC><CR>++ver` | `Unrecognized command\r\n` |
| Unescaped `LF` still frames | `++ver\n++ver\n` | **two** version strings |
| Escaped `ESC` | `++ver<ESC><ESC>` | `Unrecognized command\r\n` |

### Consequence for command parsing

The controller separates a command from its argument on **space only**. It does *not* treat an embedded `CR`/`LF` as a separator: after unescaping, `++ver<LF>++ver` is a single unrecognisable command token, not a valid `++ver`. An emulator that splits on generic whitespace will wrongly accept it.

**Why this matters here:** TestController escapes on send — `escape(msg, 27, "+\r\n")` for write-read and `escape(msg, 27, "+")` for write (`dk.hkj.shared.SharedInterface`). A command such as `:SOUR:VOLT +5` therefore arrives ESC-prefixed.

---

## 2c. `++help` — MEASURED, CORRECTS SECTION 2

> [!IMPORTANT]
> Section 2 previously grouped `++help` with the unsupported commands. **The firmware does support it.** Measurement supersedes that.

| Command | Hardware result |
| :--- | :--- |
| `++help` | Full command listing, 1879 bytes, 34 CRLF-terminated lines |
| `++status` | `Unrecognized command\r\n` |
| `++lon` | `Unrecognized command\r\n` |
| `++invalidcmd` | `Unrecognized command\r\n` |

**Firmware quirk worth knowing:** the `++help` listing *advertises* `++status` and `++lon`, but this firmware rejects both — the help text is shared across Prologix models and over-reports for the ETHERNET unit. Do not treat the help output as a capability list.

The listing is captured verbatim in [`core/prologix_help.txt`](../core/prologix_help.txt) and replayed byte for byte.

---

## 2d. Command Parsing & Argument Validation — MEASURED

| Behaviour | Evidence | Notes |
| :--- | :--- | :--- |
| **Command names are case-sensitive** | `++AUTO`, `++Addr`, `++VER` → `Unrecognized command\r\n` | Lowercase only. Do **not** case-fold. |
| **Out-of-range arguments are rejected** | `++addr 31`, `++addr 99`, `++addr -1`, `++addr abc` → `Unrecognized command\r\n` | Not silently ignored. |
| Valid boundary accepted | `++addr 30` → *[Silent Ack]* | Range is 0–30. |
| Timeout range | `++read_tmo_ms 0`, `++read_tmo_ms 99999` → `Unrecognized command\r\n`; `3000` accepted | Range 1–3000. |
| EOS range | `++eos 9` → `Unrecognized command\r\n` | Range 0–3. |
| Serial poll range | `++spoll 99` → `Unrecognized command\r\n` | Range 0–30, secondary 96–126. |
| Bare introducer | `++` → `Unrecognized command\r\n` | |
| AR488-only command | `++default` → `Unrecognized command\r\n` | Prologix does not implement it. |
| Argument separator | **space only** | An embedded CR/LF does not separate; see 2b. |

### Framing (measured)
* Two commands in one TCP packet → both execute.
* A command split across packets → reassembled correctly.
* `CRLF`-terminated commands accepted.
* Leading blank lines ignored.
* 300 trailing spaces after a query → still parsed.

---

## 2e. Data Path & Output Buffer — MEASURED

> [!IMPORTANT]
> **Terminators are not uniform.** The controller terminates its OWN replies with `CRLF`. Instrument data is relayed **byte for byte**, carrying whatever terminator the instrument produced — on this bus, a bare `LF`.

| Case | Bytes returned |
| :--- | :--- |
| `++ver` (controller) | `Prologix GPIB-ETHERNET Controller version 01.06.06.00\r\n` |
| `++spoll` (controller) | `20\r\n` |
| `*IDN?` then `++read eoi` (instrument) | `KEITHLEY INSTRUMENTS INC.,MODEL 2010,0636735,A10  /A02  \n` |

### `++eot_enable` / `++eot_char`
The EOT character is appended **after** the instrument's own data and terminator:

| Setting | Result |
| :--- | :--- |
| `++eot_enable 0` | `...A10  /A02  \n` |
| `++eot_enable 1`, `++eot_char 42` | `...A10  /A02  \n*` |
| `++eot_enable 1`, `++eot_char 35` | `...A10  /A02  \n#` |

### `++read <char>`
Reads up to **and including** the given character, or until timeout. TestController uses this form whenever `readEolEoi` is false (`dk.hkj.shared.SharedInterface.DeviceSettings.readEolChar`, default 10).

| Command | Result |
| :--- | :--- |
| `++read eoi` | full message |
| `++read 10` (LF) | full message (LF is the terminator) |
| `++read 13` (CR) | full message — no CR present, so it times out and returns everything |
| `++read 65` (`A`) | `KEITHLEY INSTRUMENTS INC.,MODEL 2010,0636735,A` |
| `++read 44` (`,`) | `KEITHLEY INSTRUMENTS INC.,` |

### Output buffer holds ONE message
An instrument holds a single pending reply. Issuing a new query while an earlier reply is unread **discards** it — the Keithley 2001M subsequently reports `-410,"Query INTERRUPTED"`. An emulator that queues messages leaves MAV asserted after the client has read everything it asked for.

### Serial poll returns the instrument's status byte
`++spoll` reports the addressed instrument's own status byte, not a bare MAV flag.

| State | Keithley 2010 / 2001M / 2002, Agilent 34411A |
| :--- | :--- |
| Idle | `4\r\n` |
| Reply waiting | `20\r\n` (`0x04 | 0x10 MAV`) |

Serial-polling an address with no instrument returns **nothing at all** (silent timeout).

---

## 🌐 3. Network & Connection Dynamics

### ICMP Ping Behavior
- Network ICMP ping requests (`ping 192.168.1.80`) respond cleanly with ~3.5 ms to 4.6 ms roundtrip latency (`0.0% packet loss`).

### Socket Connection Model & Multi-Client Locking
- **Single Active Connection Model**: Port `1234` supports **one active client TCP connection** at a time.
- **Connection Displacement**: When a new TCP socket client (`Client B`) connects while an existing client (`Client A`) is active:
  1. `Client B` connects successfully and immediately receives full access to the controller.
  2. `Client A`'s socket is closed by the hardware (`recv()` returns `b''`).

---

## 🔬 4. Live Physical Instrument Bus Scan & Performance Profile

> **The instrument-level detail now lives in `profiles/bus_capture.json`**, which
> is regenerated by `tools/ab_instruments.py`. This section records the bus
> layout and the timing figures only; the per-query replies are there.

Scanning GPIB addresses 0 to 30 on the live adapter at `192.168.1.80:1234`
(2026-08-10) detects **seven active physical instruments**:

| Addr | Instrument | Class | TestController driver |
| :--- | :--- | :--- | :--- |
| 1 | Keithley 2002 | DMM | `Keithley 2002` |
| 2 | Keithley 2001M | DMM | `Keithley 2001M` |
| 3 | Keithley 2010 | DMM | `Keithley 2010` |
| 4 | Fluke PM6690 | Counter | `Fluke PM6690` |
| 5 | Agilent 34411A | DMM | `Agilent 34411A` |
| 6 | Agilent 33250A | Function generator | `Agilent 33250A` |
| 7 | HP E3631A | Power supply | **none — see below** |

The E3631A has no TestController driver: `AgilentHP E363xA.TXT` covers the
E3632A, E3633A and E3634A only. It is emulated for bus fidelity but kept out of
the startup bench, because a device name that resolves to no driver leaves the
client unable to load it.

### Scanning safely

Addresses must be read **more than once**. Two instruments sharing an address
both answer, and GPIB data lines are open-collector and active-low, so the wire
carries the bitwise OR of their bytes — plausible-looking ASCII that reads as a
genuine identity. A single-read scan of this bus once mirrored such a collision
into the emulator as a real instrument. `tools/buslib.py` reads three times and
refuses any address that disagrees with itself.

### Timing (Keithley 2010, GPIB 3)

* **Average Latency**: `67.17 ms`
* **Min / Max Latency**: `64.25 ms` / `71.37 ms`
* **Throughput**: `14.89 Queries/Sec`

### Timing (Keithley 2001M, GPIB 2)

* **Average Latency**: `86.47 ms`
* **Min / Max Latency**: `77.87 ms` / `101.26 ms`
* **Throughput**: `11.56 Queries/Sec`

---

## ✅ 5. Unified Hardware Protocol Parity Policy

In `tc_dev_tool`, **EVERY socket connection on EVERY connection policy** enforces 100% real Prologix hardware socket protocol parity:
1. `CRLF` (`\r\n`) line endings are always used on socket outputs.
2. `Prologix GPIB-ETHERNET Controller version 01.06.06.00` is returned on `++ver`.
3. `Unrecognized command\r\n` is returned on unknown commands.
4. Setting commands execute silently.
5. Mode policies only control socket connection multiplexing (`single_connection` vs `multi_connection`).
