"""
Keysight / Agilent E5810A LAN/GPIB Gateway Network Probe & Validator (`e5810_hardware_tester.py`)

Probes physical Keysight E5810A hardware gateways across HTTP (port 80), Telnet (port 23),
and VXI-11 ONC-RPC (port 111 / 1024) to validate network interfaces and connected instruments.
"""

import socket
import re
import urllib.request
import time
from typing import Dict, Any, List, Optional


class E5810HardwareProbe:
    """Probes a network Keysight / Agilent E5810A LAN/GPIB Gateway."""

    def __init__(self, host: str = "192.168.1.85", timeout: float = 3.0):
        self.host = host
        self.timeout = timeout

    def probe_all_services(self) -> Dict[str, Any]:
        """Executes full diagnostic probe across E5810A web admin, telnet config, and VXI-11."""
        report: Dict[str, Any] = {
            "target": self.host,
            "web_admin": self.probe_http_admin(),
            "telnet_config": self.probe_telnet_config(),
            "vxi11_instruments": self.probe_vxi11_instruments(),
        }
        return report

    def probe_http_admin(self) -> Dict[str, Any]:
        result = {"status": False, "title": "", "mac_address": ""}
        try:
            url = f"http://{self.host}/"
            req = urllib.request.urlopen(url, timeout=self.timeout)
            html = req.read().decode("utf-8", errors="replace")
            result["status"] = req.status == 200

            m = re.search(r"<title>\s*(.*?)\s*</title>", html, re.IGNORECASE | re.DOTALL)
            if m:
                result["title"] = m.group(1).strip()

            mac_m = re.search(r"([0-9A-F]{2}[-:][0-9A-F]{2}[-:][0-9A-F]{2}[-:][0-9A-F]{2}[-:][0-9A-F]{2}[-:][0-9A-F]{2})", html, re.I)
            if mac_m:
                result["mac_address"] = mac_m.group(1)

        except Exception as e:
            result["error"] = str(e)
        return result

    def probe_telnet_config(self) -> Dict[str, Any]:
        result = {"status": False, "serial_num": "", "gpib_name": "gpib0", "gpib_address": 21}
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((self.host, 23))
            result["status"] = True

            s.sendall(b"status\r\n")
            time.sleep(0.1)
            resp = s.recv(2048).decode("utf-8", errors="replace")

            sn_m = re.search(r"serial-num:\s*(\w+)", resp, re.I)
            if sn_m:
                result["serial_num"] = sn_m.group(1)

            name_m = re.search(r"gpib-name:\s*(\w+)", resp, re.I)
            if name_m:
                result["gpib_name"] = name_m.group(1)

            s.close()
        except Exception as e:
            result["error"] = str(e)
        return result

    def probe_vxi11_instruments(self, gpib_addresses: Optional[List[int]] = None) -> Dict[int, Dict[str, Any]]:
        if not gpib_addresses:
            gpib_addresses = [6, 15]

        detected: Dict[int, Dict[str, Any]] = {}
        try:
            import pyvisa
            rm = pyvisa.ResourceManager("@py")

            for addr in gpib_addresses:
                res_str = f"TCPIP0::{self.host}::gpib0,{addr}::INSTR"
                try:
                    t0 = time.perf_counter()
                    inst = rm.open_resource(res_str, timeout=3000)
                    idn = inst.query("*IDN?").strip()
                    fetc = inst.query(":FETC?").strip()
                    t1 = time.perf_counter()
                    lat_ms = (t1 - t0) * 1000.0

                    detected[addr] = {
                        "resource": res_str,
                        "idn": idn,
                        "fetc": fetc,
                        "latency_ms": round(lat_ms, 2),
                    }
                    inst.close()
                except Exception:
                    pass
        except Exception as e:
            print(f"VXI-11 PyVISA Probe Error: {e}")

        return detected
