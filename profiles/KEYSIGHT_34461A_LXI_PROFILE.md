# Keysight 34461A Truevolt LXI Digital Multimeter Hardware Profile & Specification

This document records the empirical network signatures, open ports, HiSLIP protocol responses, SCPI raw socket behavior, line endings, and live measurement transaction timing measured on an official physical **Keysight 34461A Truevolt 6½ Digit LXI DMM** (Serial `MY53206545`), probed at `192.168.1.82`.

---

## 📌 1. Hardware Metadata & Identity

| Property | Empirical Hardware Value | Notes |
| :--- | :--- | :--- |
| **Manufacturer** | Keysight Technologies | Official Benchmark LXI DMM |
| **Model Number** | `34461A` | Truevolt 6½ Digit Multimeter |
| **Serial Number** | `MY53206545` | Extracted via `*IDN?` |
| **Firmware Version** | `A.03.03-02.40-03.03-00.52-01-01` | System Firmware Version |
| **Options (`*OPT?`)** | `0,0,0` | Standard Base Configuration |
| **Default Line Ending** | `LF` (`\n` / `0x0A`) | Appended to all ASCII SCPI responses |
| **System Error State** | `+0,"No error"` | Clean boot state |
| **Open Network Ports** | `80` (HTTP), `111` (VXI-11), `4880` (HiSLIP), `5024` (Telnet SCPI), `5025` (Raw SCPI) | Measured via Port Probe |

---

## 🌐 2. Open Network Services & Protocol Architecture

### A. SCPI Raw Socket Service (Port 5025)
- **TCP Port**: `5025`
- **VISA Resource Syntax**: `TCPIP0::192.168.1.82::5025::SOCKET`
- **Line Ending**: Sent and received with `\n` (LF).
- **Performance**: Sustained query latency **15.3 ms to 17.4 ms** (~28.7 QPS).

### B. HiSLIP High-Speed LAN Instrument Protocol (Port 4880)
- **TCP Port**: `4880`
- **VISA Resource Syntax**: `TCPIP0::192.168.1.82::hislip0::INSTR`
- **Performance**: Sustained query latency **16.64 ms average** (**60.08 QPS**).

### C. VXI-11 ONC-RPC Instrument Gateway (Port 111)
- **TCP Port**: `111`
- **VISA Resource Syntax**: `TCPIP0::192.168.1.82::INSTR`

---

## 🔬 3. Live Measurement & Performance Profile

Probing live voltage readings on `192.168.1.82`:

* **`*IDN?` Signature**: `Keysight Technologies,34461A,MY53206545,A.03.03-02.40-03.03-00.52-01-01`
* **Live Query Reading (`:FETC?`)**: `+1.00001428E+01` VDC (10.00014 V DC)
* **Transaction Timing Breakdown**:
  * **HiSLIP Query Latency**: **`16.64 ms` average** (**60.08 QPS**)
  * **SCPI Raw Socket Latency**: **`34.80 ms` average** (**28.73 QPS**)
  * **VXI-11 RPC Initial Connect**: `681.0 ms`

---

## ✅ 4. Summary & Emulation Objectives for `tc_dev_tool`

This profile establishes the gold standard for LXI DMM emulation:
1. **SCPI Raw Socket (Port 5025)**: Direct SCPI stream processing using `\n` line termination.
2. **HiSLIP (Port 4880)**: High-speed TCP streaming for multi-threaded TestController device drivers.
