"""
Measure the E5810A's abort channel, interrupt/SRQ channel and device_docmd.

These three were left as guesses in the emulator -- it returned "operation not
supported" for the interrupt procedures and device_docmd without anyone having
asked the hardware. This replaces the guesses with measurements.

    python tools/capture_e5810_channels.py --host 192.168.1.85

SAFETY. device_docmd can drive bus-level lines. Only READ-style commands are
probed here, and the destructive ones -- Interface Clear, Remote Enable, Pass
Control -- are named in SKIP and never sent, because the bench has instruments
monitoring a reference and a supply that may be driving a load.
"""
import argparse
import json
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.vxi11 import (                                    # noqa: E402
    ABORT_PROG, ABORT_VERS, INTR_PROG, INTR_VERS,
    DEVICE_TCP, VXI11AbortClient, VXI11Client, error_text, getport,
)

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="192.168.1.85")
parser.add_argument("--dut", type=int, default=5)
parser.add_argument("--out", default=None)
args = parser.parse_args()

OUT = args.out or os.path.join(os.path.dirname(__file__), "..", "profiles",
                               "e5810_channels_capture.json")
capture = {"gateway": args.host, "captured": time.strftime("%Y-%m-%d %H:%M:%S")}


def section(title):
    print("\n=== %s ===" % title)


client = VXI11Client(args.host, timeout=15.0)
print("core channel on port %d" % client.port)
link, err = client.create_link("gpib0,%d" % args.dut)
if link is None:
    print("could not link to gpib0,%d: %s" % (args.dut, error_text(err)))
    sys.exit(1)
print("link lid=%d abortPort=%d" % (link.lid, link.abort_port))
capture["link"] = {"lid": link.lid, "abort_port": link.abort_port}

# ---------------------------------------------------------------------------
section("abort channel")
abort = {"advertised_port": link.abort_port,
         "portmapper": getport(args.host, ABORT_PROG, ABORT_VERS)}
print("  advertised abortPort : %d" % link.abort_port)
print("  portmapper GETPORT   : %s"
      % (abort["portmapper"] or "unregistered"))

try:
    ac = VXI11AbortClient(args.host, link.abort_port, timeout=10.0)
    abort["reachable"] = True
    print("  connected to abort port %d" % link.abort_port)

    # First with nothing in flight, to see whether the service answers at all.
    # An abort with no operation to cancel should still produce a reply.
    try:
        t0 = time.time()
        rc = ac.device_abort(link.lid)
        abort["abort_when_idle"] = {"error": rc, "text": error_text(rc),
                                    "ms": round((time.time() - t0) * 1000, 1)}
        print("  abort, nothing busy  : %s  (%.0f ms)"
              % (error_text(rc), abort["abort_when_idle"]["ms"]))
    except Exception as exc:
        abort["abort_when_idle"] = "timeout/exception: %s" % type(exc).__name__
        print("  abort, nothing busy  : %s" % type(exc).__name__)

    # A live abort: start a read that will block, cancel it from here.
    outcome = {}

    def blocking_read():
        start = time.time()
        try:
            e, reason, data = client.device_read(link, io_timeout=8000)
            outcome["error"] = e
            outcome["reason"] = reason
            outcome["data"] = data.decode(errors="replace")
        except Exception as exc:
            outcome["exception"] = "%s: %s" % (type(exc).__name__, exc)
        outcome["elapsed_ms"] = round((time.time() - start) * 1000, 1)

    # Nothing is queued, so this read blocks for its whole io_timeout.
    reader = threading.Thread(target=blocking_read, daemon=True)
    reader.start()
    time.sleep(1.0)

    t0 = time.time()
    rc = ac.device_abort(link.lid)
    abort["device_abort_error"] = rc
    abort["device_abort_ms"] = round((time.time() - t0) * 1000, 1)
    print("  device_abort         : %s  (%.0f ms)"
          % (error_text(rc), abort["device_abort_ms"]))

    reader.join(timeout=12)
    abort["aborted_read"] = outcome
    print("  the blocked read     : %s" % outcome)
    ac.close()
except Exception as exc:
    abort["reachable"] = False
    abort["error"] = "%s: %s" % (type(exc).__name__, exc)
    print("  abort channel FAILED : %s" % exc)

capture["abort_channel"] = abort

# ---------------------------------------------------------------------------
section("interrupt channel / SRQ")
intr = {"portmapper": getport(args.host, INTR_PROG, INTR_VERS)}
print("  portmapper GETPORT   : %s" % (intr["portmapper"] or "unregistered"))

# Stand up a throwaway listener so create_intr_chan has somewhere real to point.
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("0.0.0.0", 0))
listener.listen(2)
local_port = listener.getsockname()[1]

# The address the gateway would have to call back on, from its point of view.
probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
probe.connect((args.host, 1024))
local_ip = probe.getsockname()[0]
probe.close()
host_addr = struct.unpack(">I", socket.inet_aton(local_ip))[0]
print("  offering callback to %s:%d" % (local_ip, local_port))

connected = {"calls": []}


def serve_interrupt_channel():
    """
    A minimal RPC server for the gateway to call back into.

    This has to be a real server, not just an accepting socket. The gateway
    validates the channel by making an RPC call, and an earlier version of this
    capture accepted the connection and closed it -- so create_intr_chan sat
    waiting for a reply that never came and timed out. That timeout was ours,
    not the gateway's.
    """
    listener.settimeout(20.0)
    try:
        conn, addr = listener.accept()
    except Exception:
        return
    connected["peer"] = "%s:%d" % addr
    conn.settimeout(20.0)
    try:
        buffer = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buffer += chunk
            while len(buffer) >= 4:
                (marker,) = struct.unpack(">I", buffer[:4])
                length = marker & 0x7FFFFFFF
                if len(buffer) < 4 + length:
                    break
                payload, buffer = buffer[4:4 + length], buffer[4 + length:]
                if len(payload) < 24:
                    continue
                xid, _mt, _rv, prog, vers, proc = struct.unpack(
                    ">IIIIII", payload[:24])
                connected["calls"].append(
                    {"prog": prog, "vers": vers, "proc": proc,
                     "bytes": len(payload)})
                # Accepted reply, null verf, SUCCESS, no results.
                reply = struct.pack(">III", xid, 1, 0)
                reply += struct.pack(">II", 0, 0)
                reply += struct.pack(">I", 0)
                conn.sendall(struct.pack(">I", 0x80000000 | len(reply)) + reply)
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


acceptor = threading.Thread(target=serve_interrupt_channel, daemon=True)
acceptor.start()

try:
    rc = client.create_intr_chan(host_addr, local_port, INTR_PROG, INTR_VERS,
                                 DEVICE_TCP)
    intr["create_intr_chan"] = {"error": rc, "text": error_text(rc)}
    print("  create_intr_chan     : %s" % error_text(rc))
except Exception as exc:
    intr["create_intr_chan"] = "exception: %s" % type(exc).__name__
    print("  create_intr_chan     : EXCEPTION %s" % exc)

try:
    rc = client.device_enable_srq(link, True, b"BENCHFORGE")
    intr["device_enable_srq"] = {"error": rc, "text": error_text(rc)}
    print("  device_enable_srq    : %s" % error_text(rc))
except Exception as exc:
    intr["device_enable_srq"] = "exception: %s" % type(exc).__name__
    print("  device_enable_srq    : EXCEPTION %s" % exc)

time.sleep(2.0)
intr["callback_connected"] = connected.get("peer")
intr["callback_calls"] = connected["calls"]
print("  gateway called back  : %s" % (connected.get("peer") or "no"))
for call in connected["calls"]:
    print("    prog %d v%d proc %d (%d bytes)"
          % (call["prog"], call["vers"], call["proc"], call["bytes"]))

try:
    rc = client.device_enable_srq(link, False, b"")
    intr["disable_srq"] = {"error": rc, "text": error_text(rc)}
    rc = client.destroy_intr_chan()
    intr["destroy_intr_chan"] = {"error": rc, "text": error_text(rc)}
    print("  destroy_intr_chan    : %s" % error_text(rc))
    rc = client.destroy_intr_chan()
    intr["destroy_twice"] = {"error": rc, "text": error_text(rc)}
    print("  destroy again        : %s" % error_text(rc))
except Exception as exc:
    intr["teardown"] = "exception: %s" % type(exc).__name__
    print("  teardown             : EXCEPTION %s" % exc)

listener.close()
capture["interrupt_channel"] = intr

# ---------------------------------------------------------------------------
section("device_docmd")
# READ-ONLY probes. The destructive bus operations are deliberately absent:
#   Interface Clear resets every instrument's interface,
#   Remote Enable / Pass Control change front-panel state.
SKIP = "Interface Clear, Remote Enable, Pass Control -- not sent"
PROBES = [
    (0x00020000, "Send Command (probed with empty data)"),
    (0x00020001, "Bus Status"),
    (0x00020002, "ATN Control (query form)"),
    (0x0002000A, "Bus Address"),
]
docmd = {"skipped": SKIP, "probes": {}}
print("  skipped: %s" % SKIP)
for cmd, label in PROBES:
    try:
        rc, data = client.device_docmd(link, cmd)
        docmd["probes"]["0x%08X" % cmd] = {
            "label": label, "error": rc, "text": error_text(rc),
            "data_out": data.hex(), "data_len": len(data),
        }
        print("  0x%08X %-32s %-30s data=%r"
              % (cmd, label, error_text(rc), data[:16]))
    except Exception as exc:
        docmd["probes"]["0x%08X" % cmd] = "exception: %s" % type(exc).__name__
        print("  0x%08X %-32s EXCEPTION %s" % (cmd, label, type(exc).__name__))
    time.sleep(0.2)
capture["device_docmd"] = docmd

# ---------------------------------------------------------------------------
client.destroy_link(link)
client.close()

with open(OUT, "w") as f:
    json.dump(capture, f, indent=2)
print("\nwrote %s" % os.path.normpath(OUT))
