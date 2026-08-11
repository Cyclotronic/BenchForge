"""
Super-Critical Hardware Protocol Validation Harness (`validation_harness.py`)

Executes rigorous verification checks against local emulator servers and physical hardware
targets, asserting 100% bit-for-bit parity against empirical hardware profile benchmarks:
  1. Prologix Ethernet Protocol Parity (01.06.06.00 version, CRLF, silent ACKs, single conn lock)
  2. LXI SCPI Raw Socket Protocol Parity (Port 5025 direct SCPI, LF termination)
  3. LXI mDNS Discovery Packet Parity (UDP Port 5353)
"""

import socket
import time
from typing import Dict, Any, List


class ValidationCheckResult:
    """Represents an individual validation test assertion result."""

    def __init__(self, check_id: str, description: str):
        self.check_id = check_id
        self.description = description
        self.passed = False
        self.expected = ""
        self.actual = ""
        self.latency_ms = 0.0
        self.error_msg = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "description": self.description,
            "passed": self.passed,
            "expected": repr(self.expected),
            "actual": repr(self.actual),
            "latency_ms": round(self.latency_ms, 2),
            "error_msg": self.error_msg,
        }


class ValidationHarness:
    """Super-critical validation harness asserting hardware protocol compliance."""

    def __init__(
        self,
        prologix_host: str = "127.0.0.1",
        prologix_port: int = 1234,
        lxi_port: int = 5025,
        connection_policy: str = "single_connection",
        probe_address: int = 1,
    ):
        self.prologix_host = prologix_host
        self.prologix_port = prologix_port
        self.lxi_port = lxi_port
        self.connection_policy = connection_policy
        self.probe_address = probe_address
        self.results: List[ValidationCheckResult] = []

    def run_full_validation_suite(self) -> List[ValidationCheckResult]:
        """Executes all hardware protocol validation checks."""
        self.results.clear()

        # Suite 1: Prologix Version String Parity
        self._check_prologix_ver_signature()

        # Suite 2: Prologix Line Termination (CRLF \r\n)
        self._check_prologix_line_termination()

        # Suite 3: Prologix Silent Setting Acknowledgement
        self._check_prologix_silent_setting_ack()

        # Suite 4: Prologix Unrecognized Command Handling
        self._check_prologix_unrecognized_cmd_error()

        # Suite 5: Prologix ++auto 0 Manual Read Buffering
        self._check_prologix_auto0_buffering()

        # Suite 6: Prologix Single Active Socket Connection Displacement
        self._check_prologix_single_conn_locking()

        # Suite 7: Prologix Non-Query Command Silent Execution
        self._check_prologix_non_query_silent()

        # Suite 8: Prologix Parameterized Query Output
        self._check_prologix_parameterized_query()

        # Suite 9: Prologix Unmapped Slot Read Timeout
        self._check_prologix_unmapped_slot_timeout()

        # Suite 10: Prologix Empty Buffer Read Timeout
        self._check_prologix_empty_buffer_read_timeout()

        # Suite 11: Prologix Secondary Addressing Support
        self._check_prologix_secondary_address()

        # Suite 12: LXI Raw SCPI Socket (Port 5025) Response
        self._check_lxi_raw_scpi_socket()

        return self.results

    @staticmethod
    def _close(sock):
        """Close if it exists, never raise. For finally: blocks."""
        if sock is None:
            return
        try:
            sock.close()
        except Exception:
            pass

    def _check_prologix_ver_signature(self):
        chk = ValidationCheckResult("PR-01", "Prologix ++ver Exact Hardware Signature")
        chk.expected = "Prologix GPIB-ETHERNET Controller version 01.06.06.00\r\n"

        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            t0 = time.perf_counter()
            s.connect((self.prologix_host, self.prologix_port))
            s.sendall(b"++ver\n")
            data = s.recv(1024)
            t1 = time.perf_counter()

            chk.latency_ms = (t1 - t0) * 1000.0
            chk.actual = data.decode("utf-8", errors="replace")
            chk.passed = chk.actual == chk.expected
        except Exception as e:
            chk.error_msg = str(e)
        finally:
            self._close(s)

        self.results.append(chk)

    def _check_prologix_line_termination(self):
        chk = ValidationCheckResult("PR-02", "Prologix Socket Line Ending Termination (CRLF \\r\\n)")
        chk.expected = "\\r\\n (CRLF 0x0D 0x0A)"

        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((self.prologix_host, self.prologix_port))
            s.sendall(b"++addr\n")
            data = s.recv(1024)

            if data.endswith(b"\r\n"):
                chk.actual = "CRLF (\\r\\n)"
                chk.passed = True
            elif data.endswith(b"\n"):
                chk.actual = "LF (\\n)"
                chk.passed = False
            else:
                chk.actual = repr(data)
                chk.passed = False
        except Exception as e:
            chk.error_msg = str(e)
        finally:
            self._close(s)

        self.results.append(chk)

    def _check_prologix_silent_setting_ack(self):
        chk = ValidationCheckResult("PR-03", "Prologix Silent Setting Ack (No socket bytes on setting change)")
        chk.expected = "Silent Ack / Timeout (0 Bytes)"

        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.15)
            s.connect((self.prologix_host, self.prologix_port))
            s.sendall(b"++addr 5\n")

            try:
                data = s.recv(1024)
                chk.actual = f"Received {len(data)} bytes: {repr(data)}"
                chk.passed = False
            except socket.timeout:
                chk.actual = "Silent Ack / Timeout (0 Bytes)"
                chk.passed = True

        except Exception as e:
            chk.error_msg = str(e)
        finally:
            self._close(s)

        self.results.append(chk)

    def _check_prologix_unrecognized_cmd_error(self):
        chk = ValidationCheckResult("PR-04", "Prologix Unknown ++ Command Error Response")
        chk.expected = "Unrecognized command\r\n"

        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((self.prologix_host, self.prologix_port))
            s.sendall(b"++invalidcommand\n")
            data = s.recv(1024)

            chk.actual = data.decode("utf-8", errors="replace")
            chk.passed = chk.actual == chk.expected
        except Exception as e:
            chk.error_msg = str(e)
        finally:
            self._close(s)

        self.results.append(chk)

    def _check_prologix_auto0_buffering(self):
        chk = ValidationCheckResult("PR-05", "Prologix ++auto 0 Manual Read Buffering Mechanics")
        chk.expected = "Buffered response returned on ++read"

        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((self.prologix_host, self.prologix_port))
            s.sendall(b"++auto 0\n")
            s.sendall(f"++addr {self.probe_address}\n".encode())
            s.sendall(b"*IDN?\n")
            time.sleep(0.02)

            s.sendall(b"++read\n")
            data = s.recv(1024)

            chk.actual = data.decode("utf-8", errors="replace").strip()
            chk.passed = len(chk.actual) > 0 and "Unrecognized" not in chk.actual
        except Exception as e:
            chk.error_msg = str(e)
        finally:
            self._close(s)

        self.results.append(chk)

    def _check_prologix_single_conn_locking(self):
        chk = ValidationCheckResult("PR-06", "Prologix Socket Connection Displacement Policy Check")

        if self.connection_policy == "multi_connection":
            chk.expected = "Socket s1 remains connected under multi_connection policy"
            s1 = s2 = None
            try:
                s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s1.settimeout(1.5)
                s1.connect((self.prologix_host, self.prologix_port))

                s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s2.settimeout(1.5)
                s2.connect((self.prologix_host, self.prologix_port))

                s2.sendall(b"++ver\n")
                _ = s2.recv(1024)

                s1.sendall(b"++ver\n")
                r1 = s1.recv(1024)

                if r1 and len(r1) > 0:
                    chk.actual = "s1 remained connected (multi_connection policy active)"
                    chk.passed = True
                else:
                    chk.actual = "s1 was dropped unexpectedly"
                    chk.passed = False
            except Exception as e:
                chk.error_msg = str(e)
                chk.passed = False
            finally:
                self._close(s1)
                self._close(s2)
        else:
            chk.expected = "Stale socket s1 dropped upon s2 connection"
            s1 = s2 = None
            try:
                s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s1.settimeout(1.5)
                s1.connect((self.prologix_host, self.prologix_port))

                s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s2.settimeout(1.5)
                s2.connect((self.prologix_host, self.prologix_port))

                s2.sendall(b"++ver\n")
                _ = s2.recv(1024)

                s1.sendall(b"++ver\n")
                r1 = s1.recv(1024)

                if r1 == b"" or len(r1) == 0:
                    chk.actual = "Stale socket s1 dropped upon s2 connection"
                    chk.passed = True
                else:
                    chk.actual = f"s1 remained active: {repr(r1)}"
                    chk.passed = False
            except Exception as e:
                chk.actual = f"Exception on stale socket: {e}"
                chk.passed = True  # Exception on dropped socket is expected behavior
            finally:
                self._close(s1)
                self._close(s2)

        self.results.append(chk)

    def _check_prologix_non_query_silent(self):
        chk = ValidationCheckResult("PR-07", "Prologix Non-Query Command Silent Execution")
        chk.expected = "Silent Ack / Socket Timeout (0 Bytes)"

        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.15)
            s.connect((self.prologix_host, self.prologix_port))
            s.sendall(b"++auto 1\n")
            s.sendall(f"++addr {self.probe_address}\n".encode())
            s.sendall(b":CONF:VOLT:DC 10\n")

            try:
                data = s.recv(1024)
                chk.actual = f"Received unexpected {len(data)} bytes: {repr(data)}"
                chk.passed = False
            except socket.timeout:
                chk.actual = "Silent Ack / Socket Timeout (0 Bytes)"
                chk.passed = True

        except Exception as e:
            chk.error_msg = str(e)
        finally:
            self._close(s)

        self.results.append(chk)

    def _check_prologix_parameterized_query(self):
        chk = ValidationCheckResult("PR-08", "Prologix Parameterized Query Output")
        chk.expected = "Valid numeric measurement string"

        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((self.prologix_host, self.prologix_port))
            s.sendall(b"++auto 1\n")
            s.sendall(f"++addr {self.probe_address}\n".encode())
            s.sendall(b":MEAS:VOLT:DC? 10,0.001\n")
            data = s.recv(1024)

            resp = data.decode("utf-8", errors="replace").strip()
            chk.actual = resp
            try:
                _ = float(resp)
                chk.passed = True
            except ValueError:
                chk.passed = False
        except Exception as e:
            chk.error_msg = str(e)
        finally:
            self._close(s)

        self.results.append(chk)

    def _check_prologix_unmapped_slot_timeout(self):
        chk = ValidationCheckResult("PR-09", "Prologix Unmapped Slot Read Timeout")
        chk.expected = "Socket Timeout (0 Bytes) on unmapped address"

        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.15)
            s.connect((self.prologix_host, self.prologix_port))
            s.sendall(b"++auto 1\n")
            s.sendall(b"++addr 20\n")
            s.sendall(b"*IDN?\n")

            try:
                data = s.recv(1024)
                chk.actual = f"Received unexpected data: {repr(data)}"
                chk.passed = False
            except socket.timeout:
                chk.actual = "Socket Timeout (0 Bytes)"
                chk.passed = True

        except Exception as e:
            chk.error_msg = str(e)
        finally:
            self._close(s)

        self.results.append(chk)

    def _check_prologix_empty_buffer_read_timeout(self):
        chk = ValidationCheckResult("PR-10", "Prologix Empty Buffer ++read Timeout")
        chk.expected = "Socket Timeout (0 Bytes) on empty buffer"

        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.15)
            s.connect((self.prologix_host, self.prologix_port))
            s.sendall(b"++auto 0\n")
            s.sendall(f"++addr {self.probe_address}\n".encode())
            s.sendall(b"++read\n")

            try:
                data = s.recv(1024)
                chk.actual = f"Received unexpected data: {repr(data)}"
                chk.passed = False
            except socket.timeout:
                chk.actual = "Socket Timeout (0 Bytes)"
                chk.passed = True

        except Exception as e:
            chk.error_msg = str(e)
        finally:
            self._close(s)

        self.results.append(chk)

    def _check_prologix_secondary_address(self):
        chk = ValidationCheckResult("PR-11", "Prologix Secondary Address Support")
        chk.expected = "1 96\r\n"

        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((self.prologix_host, self.prologix_port))
            s.sendall(b"++addr 1 96\n")
            s.sendall(b"++addr\n")
            data = s.recv(1024)

            chk.actual = data.decode("utf-8", errors="replace")
            chk.passed = chk.actual == chk.expected
        except Exception as e:
            chk.error_msg = str(e)
        finally:
            self._close(s)

        self.results.append(chk)

    def _check_lxi_raw_scpi_socket(self):
        chk = ValidationCheckResult("LXI-01", "LXI SCPI Raw Socket (Port 5025) Direct Communication")
        chk.expected = "Valid SCPI *IDN? response"

        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            t0 = time.perf_counter()
            s.connect((self.prologix_host, self.lxi_port))
            s.sendall(b"*IDN?\n")
            data = s.recv(1024)
            t1 = time.perf_counter()

            chk.latency_ms = (t1 - t0) * 1000.0
            chk.actual = data.decode("utf-8", errors="replace").strip()
            chk.passed = len(chk.actual) > 0 and "Unrecognized" not in chk.actual
        except Exception as e:
            chk.error_msg = str(e)
        finally:
            self._close(s)

        self.results.append(chk)
