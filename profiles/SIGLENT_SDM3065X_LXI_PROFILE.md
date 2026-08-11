# Siglent SDM3065X 6½ Digit LXI Multimeter Hardware Profile & Specification

This document records the empirical network signatures, open ports, VXI-11 ONC-RPC protocol performance, SCPI response syntax, and live measurement transaction timing measured on an official physical **Siglent SDM3065X 6½ Digit Dual-Display LXI DMM** (Serial `SDM36HCC800071`), probed at `192.168.1.83`.

---

## 📌 1. Hardware Metadata & Identity

| Property | Empirical Hardware Value | Notes |
| :--- | :--- | :--- |
| **Manufacturer** | Siglent Technologies | Popular Bench LXI DMM |
| **Model Number** | `SDM3065X` | 6½ Digit Dual-Display Multimeter |
| **Serial Number** | `SDM36HCC800071` | Extracted via `*IDN?` |
| **Firmware Version** | `3.02.01.13` | Main System Firmware Version |
| **Default Line Ending** | `LF` (`\n` / `0x0A`) | Appended to all ASCII SCPI responses |
| **System Error State** | `+0,"No error"` | Clean boot state |
| **Open Network Ports** | `111` (VXI-11 ONC-RPC), `5024` (Telnet SCPI), `5025` (Raw SCPI) | Measured via Port Probe |

---

## 🌐 2. Open Network Services & Protocol Architecture

### A. VXI-11 ONC-RPC Instrument Gateway (Port 111)
- **TCP Port**: `111`
- **VISA Resource Syntax**: `TCPIP0::192.168.1.83::INSTR`
- **Performance**: Sustained query latency **`9.57 ms` average** (**`104.44 Queries/Second`**).

### B. SCPI Raw Socket Service (Port 5025)
- **TCP Port**: `5025`
- **VISA Resource Syntax**: `TCPIP0::192.168.1.83::5025::SOCKET`

### C. Telnet SCPI Service (Port 5024)
- **TCP Port**: `5024`

---

## 🔬 3. Live Measurement & Performance Profile

Probing live measurements on `192.168.1.83`:

* **`*IDN?` Signature**: `Siglent Technologies,SDM3065X,SDM36HCC800071,3.02.01.13`
* **VXI-11 Query Performance**:
  * **Average Latency**: **`9.57 ms`**
  * **Min / Max Latency**: `7.96 ms` / `11.64 ms`
  * **Sustained Throughput**: **`104.44 Queries/Second`** (QPS)

---

## ✅ 4. Summary & Emulation Objectives for `tc_dev_tool`

The Siglent SDM3065X represents high-speed modern LXI bench instruments:
1. **VXI-11 Engine**: Sub-10ms ultra-fast query execution.
2. **Standard SCPI Commands**: Full compatibility with TestController Siglent SDM3065X driver definitions.
