# ⚡ BenchForge Studio & Lab Automation Engineering Notes (`eng-notes.md`)

This document aggregates all empirical physical hardware profiles, network probing signatures, socket behavioral analysis, multi-protocol specifications, validation harness assertions, and system architecture details for **BenchForge Studio**.

---

## 📂 1. Workspace & Repository Structure

> **This share is mounted from more than one machine.** The same directories
> resolve differently depending on where you are, so the mount points are
> recorded once here and **every other reference in this document, and in the
> codebase, is repository-relative**. If you find an absolute path anywhere
> else, it is a bug — it will not resolve on the other machine.
>
> | Host | Workspace root |
> | :--- | :--- |
> | macOS | `/Volumes/pub-1/AG/` |
> | Windows | `H:\AG\` |
>
> Line endings and encoding are pinned by `.gitattributes` (LF everywhere, UTF-8
> without BOM) so the two hosts do not fight over whole-file diffs. Note the one
> deliberate exception: `core/prologix_help.txt` is marked `-text` because it is
> a byte-exact hardware capture whose CRLF terminators are real data.

* **Active Primary Project Directory**: `<workspace>/BenchForge`
* **Parallel Gateway Directory**: `<workspace>/VMSG`

```text
/Volumes/pub-1/AG/
├── BenchForge/          <-- Standalone Universal Bench Instrument & Gateway Emulator Suite
│   ├── benchforge.py    <-- Desktop GUI Entry Point
│   ├── core/            <-- App Core Modules Subfolder
│   │   ├── gui_qt.py            <-- PySide6 desktop UI
│   │   ├── theme.py             <-- Fluent design tokens & stylesheet builder
│   │   ├── prologix_emulator.py
│   │   ├── vxi11_lxi_emulator.py
│   │   ├── device_emulator.py
│   │   ├── validation_harness.py
│   │   ├── tc_parser.py
│   │   └── performance_tester.py
│   ├── tests/           <-- Test Suite & Hardware Testers Subfolder
│   │   ├── test_benchforge.py
│   │   └── e5810_hardware_tester.py
│   ├── build_exe.py     <-- PyInstaller Compilation Pipeline
│   ├── benchforge.spec  <-- PyInstaller Specification File
│   ├── requirements.txt <-- Dependencies (PyVISA, PyVISA-py, PyInstaller)
│   ├── LICENSE          <-- Open-Source MIT License
│   ├── README.md        <-- GitHub Landing Page
│   └── tests/           <-- Integration Test Suite (test_benchforge.py)
└── VMSG/                <-- VISA Mapping TCP/IP Socket Gateway (v1.2.0)
```

---

## 🔬 2. Empirical Physical Hardware Signatures & Probing Reference

### A. Real Prologix GPIB-ETHERNET Controller (`192.168.1.85:1234`)
Probed against physical Prologix hardware adapter connected to Keithley 2010 (Address 6) and Keithley 2001M (Address 15):

* **Exact `++ver` Version Query Response**:
  ```text
  Prologix GPIB-ETHERNET Controller version 01.06.06.00\r\n
  ```
* **Socket Line Termination**: Strictly `CRLF` (`\r\n` / `0x0D 0x0A`).
* **Unknown Command Error Format**:
  ```text
  Unrecognized command\r\n
  ```
* **Silent Setting Acknowledgements**: Setting commands (`++addr 5`, `++auto 1`, `++eos 0`) execute silently with **zero bytes returned** over the TCP socket.
* **Single Connection Displacement Locking**: Connecting a 2nd TCP socket client immediately drops/closes the 1st active client socket (`recv()` returns `b''`).
* **Physical Instrument Performance**:
  * **Keithley 2010 DMM** (Addr 6): `KEITHLEY INSTRUMENTS INC.,MODEL 2010,0636735,A10  /A02` — Latency: `67.17 ms` avg (`14.89 QPS`).
  * **Keithley 2001M DMM** (Addr 15): `KEITHLEY INSTRUMENTS INC.,MODEL 2001M,1150952,B16  /A02` — Latency: `86.47 ms` avg (`11.56 QPS`).

### B. Keysight / Agilent E5810A LAN/GPIB Gateway (`192.168.1.85`)
* **Hardware Serial Number**: `MY43000991` | **MAC Address**: `00:30:D3:07:A4:C6`
* **SICL Interface Name**: `gpib0` | **GPIB Controller Address**: `21`
* **Open Ports**:
  * `Port 80`: HTTP Web Management Admin (`Agilent E5810 (00-30-D3-07-A4-C6)`).
  * `Port 23`: Telnet Configuration Terminal (`Welcome to the E5810...`).
  * `Port 111 & Port 1024`: VXI-11 ONC-RPC Gateway (`0x0607AF` / 395183).
* **VXI-11 Performance**: Keithley 2010 via VXI-11 (`TCPIP0::192.168.1.85::gpib0,6::INSTR`) = **`26.57 ms` average** (**`37.64 QPS`**).

### C. Keysight 34461A Truevolt 6½ Digit LXI DMM (`192.168.1.82`)
* **`*IDN?` Signature**: `Keysight Technologies,34461A,MY53206545,A.03.03-02.40-03.03-00.52-01-01`
* **Open Ports**: `80` (HTTP), `111` (VXI-11), `4880` (HiSLIP), `5024` (Telnet SCPI), `5025` (SCPI Raw Socket).
* **Multi-Protocol Latency & Throughput**:
  * **HiSLIP Protocol (Port 4880)**: **`16.64 ms` average** (**`60.08 QPS`**)
  * **SCPI Raw Socket (Port 5025)**: **`34.80 ms` average** (**`28.73 QPS`**)
  * **VXI-11 RPC Initial Connect**: `681.0 ms`

### D. Siglent SDM3065X 6½ Digit LXI DMM (`192.168.1.83`)
* **`*IDN?` Signature**: `Siglent Technologies,SDM3065X,SDM36HCC800071,3.02.01.13`
* **Open Ports**: `111` (VXI-11), `5024` (Telnet SCPI), `5025` (SCPI Raw Socket).
* **VXI-11 Performance**: **`9.57 ms` average** (**`104.44 QPS`**).

---

## ⚡ 3. Multi-Protocol Emulator Architecture (`BenchForge`)

### 1. Prologix Ethernet Server (`prologix_emulator.py`)
* Listens on TCP Port 1234 (default).
* Implements standard Prologix command matrix: `++addr`, `++auto`, `++ver`, `++mode`, `++read`, `++read_tmo_ms`, `++eos`, `++eoi`, `++eot_enable`, `++eot_char`, `++savecfg`, `++srq`, `++clr`, `++loc`, `++llo`, `++trg`, `++ifc`, `++rst`, `++spoll`.
* Enforces `single_connection` displacement locking and `multi_connection` multiplexing modes with 100% strict response syntax.

### 2. LXI SCPI Raw Socket & mDNS Discovery Server (`vxi11_lxi_emulator.py`)
* **LXI SCPI Raw Socket Server (TCP Port 5025)**: Handles direct SCPI text commands (`*IDN?`, `:READ?`, `:FETC?`, `:CONF:VOLT:DC 10`) without `++` prefixes.
* **LXI mDNS Discovery Responder (UDP Port 5353)**: Broadcasts `_lxi._tcp.local` and `_scpi-raw._tcp.local` service records so TestController and VISA discovery tools auto-detect BenchForge on the network.

### 3. Super-Critical Validation Harness (`validation_harness.py`)
Automated protocol assertion engine verifying:
* `[PR-01]`: Prologix `++ver` exact hardware signature (`01.06.06.00`).
* `[PR-02]`: Prologix socket line ending termination (`\r\n` CRLF).
* `[PR-03]`: Prologix silent setting ACK (0 bytes on socket).
* `[PR-04]`: Prologix unknown command error response (`Unrecognized command\r\n`).
* `[PR-05]`: Prologix `++auto 0` manual read buffering mechanics.
* `[PR-06]`: Prologix socket connection displacement policy check (single_connection vs multi_connection).
* `[PR-07]`: Prologix non-query command silent execution (0 bytes / timeout on set commands).
* `[PR-08]`: Prologix parameterized query measurement output (valid numeric response).
* `[PR-09]`: Prologix unmapped slot read timeout (0 bytes on empty address).
* `[PR-10]`: Prologix empty buffer `++read` timeout (0 bytes on unread buffer).
* `[PR-11]`: Prologix secondary addressing support (`++addr 1 96`).
* `[LXI-01]`: LXI SCPI Raw Socket (Port 5025) direct SCPI communication.

### 4. TestController Tooling (`tc_parser.py`)
* Parses TestController `Devices/*.txt` definitions.
* Generates recommended `settingsGPIB.txt` and `settingsLoad.txt` configuration blocks for all interface modes (`PrologixEthernet`, `AR488Lan`, `Kofen`, `KeysightE5810`).

---

## 🧪 4. Testing & Build Procedures

### Running Desktop GUI
```bash
cd /Volumes/pub-1/AG/BenchForge
python benchforge.py
```

### Running Test Suite
```bash
cd /Volumes/pub-1/AG/BenchForge
python -m unittest discover -s tests
```

### Running Protocol Validation Harness
```bash
python -c "import sys; sys.path.insert(0, 'core'); from validation_harness import ValidationHarness; h = ValidationHarness(); print(h.run_full_validation_suite())"
```

### Building Standalone Executable
```bash
python build_exe.py
```
*(Outputs standalone `BenchForge` executable under `dist/BenchForge`)*

---

## 🚀 5. GitHub Release Readiness Checklist

* [x] **`.gitignore`**: Ignores `__pycache__/`, `*.pyc`, `build/`, `dist/`, `.DS_Store`, `.venv/`, `.pytest_cache/`.
* [x] **`requirements.txt`**: Added `pyvisa>=1.13.0`, `pyvisa-py>=0.7.0`, `pyinstaller>=5.0.0`.
* [x] **`LICENSE`**: Added standard **MIT License**.
* [x] **`README.md`**: Comprehensive GitHub landing page with badging, features, hardware profile links, setup, and build commands.
* [x] **Zero Code Duplication / Clean Separation**: 100% independent standalone directory (`/Volumes/pub-1/AG/BenchForge`).

---

## 🔮 6. v2 Upcoming Features Roadmap

* [ ] **Custom TestController Driver Importing**: Parsing third-party `.txt` TestController device definition files for custom user-defined command mapping (deferred from v1 to prioritize core hardware protocol fidelity and prebuilt virtual instrument library).

