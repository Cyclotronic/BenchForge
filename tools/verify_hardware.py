"""
Byte-for-byte diff of the emulator against a physical Prologix controller.

Requires a real Prologix GPIB-ETHERNET adapter on the network. Every reply is
compared byte for byte; any difference is a fidelity defect in the emulator.

    python tools/verify_hardware.py --host 192.168.1.80

The controller's saved settings are read first and restored on exit, because
++savecfg persists them to EEPROM.

Exit code 0 when identical, 1 otherwise.
"""
import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.device_emulator import InstrumentRegistry, VirtualInstrument
from core.prologix_emulator import PrologixEmulatorServer
from tools.buslib import Link as BusLink, scan_bus

ESC = b"\x1b"

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="192.168.1.80")
parser.add_argument("--port", type=int, default=1234)
parser.add_argument("--dut", type=int, default=6,
                    help="GPIB address of an instrument to exercise")
args = parser.parse_args()

HW = (args.host, args.port)
LOCAL_PORT = 15801


class Link:
    """
    One persistent connection per endpoint.

    A Prologix serves a single client and drops the previous socket whenever a
    new one arrives. Opening a fresh connection per test case therefore churns
    the adapter hard -- dozens of connect/displace cycles in a few seconds --
    and can leave it refusing connections until it is power cycled. Hold one
    connection open instead, and drain between cases.
    """

    def __init__(self, target):
        self.sock = socket.socket()
        self.sock.settimeout(1.5)
        self.sock.connect(target)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def drain(self):
        self.sock.settimeout(0.2)
        try:
            while self.sock.recv(8192):
                pass
        except Exception:
            pass
        self.sock.settimeout(1.5)

    def talk(self, payload, wait=0.45, settle=0.12):
        self.drain()
        self.sock.sendall(payload + b"\n")
        time.sleep(wait)
        chunks = []
        while True:
            try:
                d = self.sock.recv(8192)
            except socket.timeout:
                break
            if not d:
                break
            chunks.append(d)
            if len(b"".join(chunks)) > 4000:
                break
            time.sleep(settle)
        return b"".join(chunks)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# Mirror the real bus so instrument replies are comparable. The scan itself --
# including the collision check that refuses an address answering two different
# ways -- lives in buslib, shared with ab_instruments.py.
def build_registry():
    reg = InstrumentRegistry()
    reg.devices.clear()
    scan = BusLink(HW)
    try:
        found, unstable = scan_bus(scan)
    finally:
        # The scan changes ++auto and ++read_tmo_ms, and ++savecfg is 1 on the
        # reference unit, so leaving them changed rewrites the adapter's EEPROM.
        scan.restore_baseline()
        scan.close()
    for addr, idn in found.items():
        reg.set_device(addr, VirtualInstrument(
            addr, idn.split(",")[1] if "," in idn else "dev", idn.rstrip("\n")))
    return reg, unstable


print("Scanning %s:%d (3 reads per address) ..." % HW)
registry, unstable = build_registry()
print("instruments mirrored:", sorted(registry.devices))
if unstable:
    print("addresses REFUSED (unstable, see above):", unstable)
    print("Fix the bus before trusting this run -- a colliding address cannot")
    print("be mirrored, so any comparison against it is meaningless.")
if args.dut not in registry.devices:
    print("WARNING: --dut %d is not on the bus; instrument checks will be thin"
          % args.dut)

srv = PrologixEmulatorServer(host="127.0.0.1", port=LOCAL_PORT, registry=registry)
srv.start(); time.sleep(0.3)
LOCAL = ("127.0.0.1", LOCAL_PORT)

CASES = [
    # Command matrix
    ("++ver", b"++ver"), ("++help", b"++help"),
    ("++status", b"++status"), ("++lon", b"++lon"),
    ("++invalidcmd", b"++invalidcmd"),
    # Case sensitivity
    ("++AUTO", b"++AUTO"), ("++Addr", b"++Addr"), ("++VER", b"++VER"),
    # Argument validation
    ("++addr 99", b"++addr 99"), ("++addr -1", b"++addr -1"),
    ("++addr abc", b"++addr abc"), ("++addr 31", b"++addr 31"),
    ("++read_tmo_ms 0", b"++read_tmo_ms 0"),
    ("++read_tmo_ms 99999", b"++read_tmo_ms 99999"),
    ("++eos 9", b"++eos 9"), ("++spoll 99", b"++spoll 99"),
    ("++ (bare)", b"++"), ("++default", b"++default"),
    # ESC escaping on the data path
    ("escaped LF", b"++ver" + ESC + b"\n" + b"++ver"),
    ("escaped CR", b"++ver" + ESC + b"\r" + b"++ver"),
    ("escaped ESC", b"++ver" + ESC + ESC),
]

print("\n%-24s %s" % ("CASE", "RESULT"))
print("-" * 60)
failures = []
hw_link = Link(HW)
em_link = Link(LOCAL)
for label, payload in CASES:
    hw = hw_link.talk(payload)
    em = em_link.talk(payload)
    if hw == em:
        print("%-24s match (%d bytes)" % (label, len(hw)))
    else:
        failures.append((label, hw, em))
        print("%-24s MISMATCH" % label)
        print("%26s hw=%r" % ("", hw[:100]))
        print("%26s em=%r" % ("", em[:100]))

hw_link.close()
em_link.close()
srv.stop()

print("\nMISMATCHES vs physical controller: %d" % len(failures))
if failures:
    sys.exit(1)
print("Emulator is byte-identical to the hardware on every case checked.")
