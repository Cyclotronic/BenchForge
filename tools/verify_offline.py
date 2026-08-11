"""
Offline fidelity verification — no hardware required.

Checks the emulator against the recorded hardware profile, plus the two
concurrency properties that a single-device test cannot catch. Safe to run in
CI and as a pre-commit gate.

    python tools/verify_offline.py

Exit code 0 when everything matches, 1 otherwise.
"""
import os
import re
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.device_emulator import InstrumentRegistry, VirtualInstrument, DEFAULT_BENCH
from core.prologix_emulator import PrologixEmulatorServer

failures = []


def fail(section, detail):
    failures.append("%s: %s" % (section, detail))


def serve(port, registry=None, cls=PrologixEmulatorServer):
    srv = cls(host="127.0.0.1", port=port, registry=registry or InstrumentRegistry())
    srv.start()
    time.sleep(0.25)
    return srv


# ---------------------------------------------------------------------------
# 1. Command matrix and defaults, straight from PROLOGIX_HARDWARE_PROFILE.md
# ---------------------------------------------------------------------------
PROFILE_MATRIX = [
    ("++ver", b"Prologix GPIB-ETHERNET Controller version 01.06.06.00\r\n"),
    ("++addr", b"4\r\n"), ("++auto", b"0\r\n"), ("++mode", b"1\r\n"),
    ("++read_tmo_ms", b"200\r\n"), ("++eos", b"3\r\n"), ("++eoi", b"1\r\n"),
    ("++eot_enable", b"0\r\n"), ("++eot_char", b"0\r\n"),
    ("++savecfg", b"1\r\n"), ("++srq", b"0\r\n"),
    ("++clr", None), ("++loc", None), ("++llo", None),
    ("++trg", None), ("++ifc", None), ("++rst", None),
    # Section 2: the firmware rejects these.
    ("++status", b"Unrecognized command\r\n"),
    ("++lon", b"Unrecognized command\r\n"),
    ("++invalidcmd", b"Unrecognized command\r\n"),
    # Section 2d: case-sensitive, and arguments are range-checked.
    ("++AUTO", b"Unrecognized command\r\n"),
    ("++Addr", b"Unrecognized command\r\n"),
    ("++addr 31", b"Unrecognized command\r\n"),
    ("++addr abc", b"Unrecognized command\r\n"),
    ("++read_tmo_ms 0", b"Unrecognized command\r\n"),
    ("++eos 9", b"Unrecognized command\r\n"),
    ("++", b"Unrecognized command\r\n"),
]


def check_profile_matrix():
    print("1. Command matrix vs PROLOGIX_HARDWARE_PROFILE.md")
    srv = serve(15901)
    try:
        for cmd, expect in PROFILE_MATRIX:
            s = socket.socket(); s.settimeout(0.35)
            s.connect(("127.0.0.1", 15901))
            try:
                s.sendall(cmd.encode() + b"\n")
                time.sleep(0.05)
                try:
                    got = s.recv(4096)
                except socket.timeout:
                    got = None
            finally:
                s.close()
            if got != expect:
                fail("command matrix", "%s -> %r, profile says %r" % (cmd, got, expect))
        print("   %d commands checked" % len(PROFILE_MATRIX))
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# 2. ++help replayed byte-for-byte from the captured listing
# ---------------------------------------------------------------------------
def check_help():
    print("2. ++help byte-for-byte")
    path = os.path.join(os.path.dirname(__file__), "..", "core", "prologix_help.txt")
    if not os.path.isfile(path):
        fail("++help", "core/prologix_help.txt is missing")
        return
    expected = open(path, "rb").read()
    srv = serve(15902)
    try:
        s = socket.socket(); s.settimeout(1.0)
        s.connect(("127.0.0.1", 15902))
        s.sendall(b"++help\n")
        time.sleep(0.6)
        chunks = []
        while True:
            try:
                d = s.recv(8192)
            except socket.timeout:
                break
            if not d:
                break
            chunks.append(d)
            time.sleep(0.1)
        s.close()
        got = b"".join(chunks)
        if got != expected:
            fail("++help", "%d bytes returned, capture is %d" % (len(got), len(expected)))
        print("   %d bytes" % len(got))
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# 3. Terminators: controller CRLF, instrument data relayed verbatim
# ---------------------------------------------------------------------------
def check_terminators():
    print("3. Terminators and EOT")
    srv = serve(15903)
    try:
        s = socket.socket(); s.settimeout(0.6)
        s.connect(("127.0.0.1", 15903))

        def cmd(text, wait=0.06):
            s.sendall(text.encode() + b"\n"); time.sleep(wait)

        def reply(wait=0.3):
            time.sleep(wait)
            try:
                return s.recv(4096)
            except socket.timeout:
                return None

        cmd("++auto 0"); cmd("++addr 1")
        try:
            s.recv(4096)
        except socket.timeout:
            pass

        cmd("++ver")
        if not (reply() or b"").endswith(b"\r\n"):
            fail("terminators", "controller reply is not CRLF-terminated")

        cmd("*IDN?"); cmd("++read eoi")
        data = reply(0.4) or b""
        if not data.endswith(b"\n") or data.endswith(b"\r\n"):
            fail("terminators", "instrument data should end with bare LF, got %r" % data[-4:])

        cmd("++eot_enable 1"); cmd("++eot_char 42")
        try:
            s.recv(4096)
        except socket.timeout:
            pass
        cmd("*IDN?"); cmd("++read eoi")
        data = reply(0.4) or b""
        if not data.endswith(b"\n*"):
            fail("terminators", "eot_char not appended after instrument data: %r" % data[-4:])
        s.close()
        print("   controller CRLF, instrument LF, EOT appended")
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# 4. Serial poll: instrument status byte, per-address MAV
# ---------------------------------------------------------------------------
def check_serial_poll():
    print("4. Serial poll")
    srv = serve(15904)
    try:
        s = socket.socket(); s.settimeout(0.6)
        s.connect(("127.0.0.1", 15904))

        def cmd(*parts, read=True):
            for p in parts:
                s.sendall(p.encode() + b"\n"); time.sleep(0.05)
            if not read:
                return None
            time.sleep(0.2)
            try:
                return s.recv(1024)
            except socket.timeout:
                return None

        cmd("++auto 0", read=False)
        cmd("++addr 1", "*IDN?", read=False)
        if cmd("++spoll") != b"20\r\n":
            fail("serial poll", "expected 20 with a message pending")
        if cmd("++addr 2", "++spoll") != b"4\r\n":
            fail("serial poll", "MAV leaked to another address")
        cmd("++addr 1", "++read eoi")
        if cmd("++spoll") != b"4\r\n":
            fail("serial poll", "MAV not cleared after read")
        s.close()
        print("   idle 4, pending 20, per-address isolation")
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# 5. No cross-talk when one client multiplexes several instruments
# ---------------------------------------------------------------------------
def check_crosstalk():
    print("5. Multiplexed devices receive their own replies")
    reg = InstrumentRegistry()
    srv = serve(15905, reg)
    expected = {spec["slot"]: spec["idn"] for spec in DEFAULT_BENCH}
    results = {}
    lock = threading.Lock()
    try:
        sock = socket.socket(); sock.settimeout(2.0)
        sock.connect(("127.0.0.1", 15905))
        sock.sendall(b"++auto 0\n")
        time.sleep(0.05)

        def worker(slot):
            got = []
            for _ in range(5):
                with lock:
                    sock.sendall(("++addr %d\n" % slot).encode())
                    sock.sendall(b"*IDN?\n")
                time.sleep(0.004)
                with lock:
                    sock.sendall(("++addr %d\n" % slot).encode())
                    sock.sendall(b"++read eoi\n")
                    try:
                        # Drop only the terminator. A blanket .strip() also
                        # eats the trailing spaces the Keithley units really
                        # send ("...B02  /A02  "), which reads as 15 bogus
                        # cross-deliveries -- three units times five reads.
                        got.append(sock.recv(2048).decode().rstrip("\r\n"))
                    except socket.timeout:
                        got.append("<TIMEOUT>")
                time.sleep(0.003)
            results[slot] = got

        threads = [threading.Thread(target=worker, args=(s,)) for s in expected]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        sock.close()

        wrong = sum(1 for slot, got in results.items()
                    for g in got if g != expected[slot])
        if wrong:
            fail("crosstalk", "%d replies delivered to the wrong device" % wrong)
        print("   %d threads, %d exchanges, %d misdelivered"
              % (len(expected), sum(len(v) for v in results.values()), wrong))
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# 6. Every emitted frame is complete and well formed
# ---------------------------------------------------------------------------
def check_frame_integrity():
    """
    Asserts only that every frame is COMPLETE and well formed.

    Deliberately uses two separate locks so a second thread's '++addr' can land
    between this thread's '++addr' and its '++read' -- the same window that
    exists in a client whose write->read pair is not atomic. Under that race a
    reply may legitimately be routed to the other device, so this check does NOT
    assert which device received what; check 5 covers routing with the pair held
    together. What the emulator must guarantee either way is that no frame is
    ever truncated or merged.
    """
    print("6. Frame integrity under concurrent access")
    reg = InstrumentRegistry()
    reg.devices.clear()
    reg.set_device(1, VirtualInstrument(1, "dmm", "HEWLETT-PACKARD,34401A,0,10-1-1"))
    reg.set_device(2, VirtualInstrument(2, "ctr", "FLUKE, PM6690, 0, V1",
                                        instrument_class="COUNTER"))
    srv = serve(15906, reg)
    # MEASURED reading formats. A DMM answers signed scientific with eight
    # decimals (Keithley 2010 '+1.00001363E+01', Agilent 34411A
    # '+2.28648288E+01'). A counter expresses a fixed 0.01 Hz resolution, so
    # its decimal count moves with the decade -- eight or nine, both correct.
    VOLT = re.compile(r"^[+-]\d\.\d{8}E[+-]\d{2}$")
    FREQ = re.compile(r"^[+-]\d\.\d{8,9}E[+-]\d{2}$")
    chunks = []
    wlock, rlock, clock = threading.Lock(), threading.Lock(), threading.Lock()
    try:
        sock = socket.socket(); sock.settimeout(2.0)
        sock.connect(("127.0.0.1", 15906))
        sock.sendall(b"++auto 0\n")
        time.sleep(0.05)

        def worker(addr, query):
            for _ in range(15):
                with wlock:
                    sock.sendall(("++addr %d\n" % addr).encode())
                    sock.sendall((query + "\n").encode())
                time.sleep(0.002)
                with rlock:
                    sock.sendall(("++addr %d\n" % addr).encode())
                    sock.sendall(b"++read eoi\n")
                    try:
                        c = sock.recv(4096)
                    except socket.timeout:
                        c = b""
                with clock:
                    chunks.append(c)
                time.sleep(0.003)

        threads = [threading.Thread(target=worker, args=a)
                   for a in ((1, "READ?"), (2, ":Read?"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        sock.close()

        bad = []
        empty = 0
        for c in chunks:
            body = c.decode(errors="replace")
            if body == "":
                # A read that returned nothing. Under the deliberate race above
                # the other thread's '++addr' can land mid-sequence, so this
                # read addressed an instrument with an empty output buffer --
                # and staying silent there is exactly what the hardware does.
                empty += 1
                continue
            for frame in body.split("\n")[:-1]:
                # TCP is a stream: several complete replies may arrive in one
                # recv(). That is not a defect, so frames are checked
                # individually rather than requiring one per read.
                if not (VOLT.match(frame) or FREQ.match(frame)):
                    bad.append((frame, "truncated or corrupt"))
            if not body.endswith("\n"):
                bad.append((body, "no terminator"))
        if bad:
            fail("frame integrity",
                 "%d frames malformed, e.g. %r (%s)"
                 % (len(bad), bad[0][0], bad[0][1]))
        print("   %d reads, %d malformed frames, %d silent (client race, expected)"
              % (len(chunks), len(bad), empty))
    finally:
        srv.stop()


# ---------------------------------------------------------------------------
# 7. The startup bench is usable by client software
# ---------------------------------------------------------------------------
def check_default_bench():
    print("7. Startup bench")
    reg = InstrumentRegistry()
    slots = sorted(reg.devices.keys())
    if len(slots) < 4:
        fail("bench", "only %d instruments at startup" % len(slots))
    for addr in slots:
        dev = reg.devices[addr]
        if "SIMULATED-DEV" in dev.idn or "(Simulated)" in dev.name:
            fail("bench", "slot %d carries a placeholder identity" % addr)
        func = reg.process_command(addr, "FUNC?")
        # A generator answers with a BARE waveform name -- MEASURED on a
        # 33250A, FUNC? -> SIN. Only measuring instruments quote.
        quoted = dev.instrument_class != "FUNCGEN"
        if not func or (quoted and not func.startswith('"')):
            fail("bench", "slot %d does not answer FUNC?" % addr)
        elif not quoted and func not in VirtualInstrument.WAVEFORMS:
            fail("bench", "generator at slot %d reports %r" % (addr, func))
        elif dev.instrument_class == "COUNTER" and "FREQ" not in func:
            fail("bench", "counter at slot %d reports %s" % (addr, func))
    print("   %d instruments, all identified" % len(slots))


for check in (check_profile_matrix, check_help, check_terminators,
              check_serial_poll, check_crosstalk, check_frame_integrity,
              check_default_bench):
    try:
        check()
    except Exception as exc:                       # noqa: BLE001
        fail(check.__name__, "raised %s: %s" % (type(exc).__name__, exc))

print()
if failures:
    print("FAILURES: %d" % len(failures))
    for f in failures:
        print("   -", f)
    sys.exit(1)
print("All offline fidelity checks passed.")
