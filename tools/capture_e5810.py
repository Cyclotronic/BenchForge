"""
Capture everything needed to emulate an E5810A LAN/GPIB gateway faithfully.

Records the VXI-11 wire behaviour rather than what a VISA library exposes: link
identifiers and how they are allocated, the abort channel port, the negotiated
maxRecvSize, the `reason` bits that terminate a read, and the error code on
every failure path. Those are the fields an emulator has to reproduce, and a
high-level VISA call shows none of them.

    python tools/capture_e5810.py --host 192.168.1.85

SAFETY. Instrument state is not modified. The only operations that touch an
instrument beyond reading are device_clear, device_trigger and
device_remote/local, and they are confined to a single DMM chosen with --dut,
with device_local always issued last so the front panel is not left locked.
Nothing is sent to any other instrument except *IDN? and a serial poll. Gateway
configuration is never written.

Links are held and reused. Churning them degrades an E5810A progressively until
create_link fails outright, which reads as a flaky bus rather than as our fault.
"""
import argparse
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.vxi11 import (                                    # noqa: E402
    ABORT_PROG, ABORT_VERS, CORE_PROG, CORE_VERS, INTR_PROG, INTR_VERS,
    READ_TERMCHRSET, VXI11Client, error_text, getport, reason_text,
)

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="192.168.1.85")
parser.add_argument("--dut", type=int, default=5,
                    help="GPIB address of a DMM safe to exercise actively")
parser.add_argument("--out", default=None)
args = parser.parse_args()

HOST = args.host
OUT = args.out or os.path.join(os.path.dirname(__file__), "..", "profiles",
                               "e5810_vxi11_capture.json")

capture = {
    "gateway": HOST,
    "captured": time.strftime("%Y-%m-%d %H:%M:%S"),
    "method": "raw VXI-11 / ONC-RPC, instrument state not modified",
}


def section(title):
    print("\n=== %s ===" % title)


def timed(fn, *a, **kw):
    start = time.time()
    result = fn(*a, **kw)
    return result, round((time.time() - start) * 1000, 2)


# ---------------------------------------------------------------------------
section("RPC portmapper")
ports = {}
for name, prog, vers in (("core", CORE_PROG, CORE_VERS),
                         ("abort", ABORT_PROG, ABORT_VERS),
                         ("interrupt", INTR_PROG, INTR_VERS)):
    try:
        port = getport(HOST, prog, vers)
    except Exception as exc:
        port = None
        print("  %-10s prog %-7d FAILED %s" % (name, prog, exc))
        continue
    ports[name] = port
    print("  %-10s prog %-7d v%d -> port %s"
          % (name, prog, vers, port or "unregistered"))
capture["portmapper"] = ports

if not ports.get("core"):
    print("\nNo VXI-11 core channel. Nothing further can be captured.")
    sys.exit(1)

client = VXI11Client(HOST, port=ports["core"], timeout=12.0)
print("  core channel connected on port %d" % client.port)

# ---------------------------------------------------------------------------
section("create_link: fields and allocation")
links = {}
link_records = []
for addr in range(0, 31):
    device = "gpib0,%d" % addr
    (result, ms) = timed(client.create_link, device)
    link, error = result
    if error or link is None:
        continue
    links[addr] = link
    link_records.append({
        "device": device, "lid": link.lid, "abort_port": link.abort_port,
        "max_recv_size": link.max_recv_size, "create_ms": ms,
    })
    print("  %-10s lid=%-6d abortPort=%-6d maxRecvSize=%-7d  %6.1f ms"
          % (device, link.lid, link.abort_port, link.max_recv_size, ms))

capture["links"] = link_records
print("  %d links open simultaneously" % len(links))

if link_records:
    lids = [r["lid"] for r in link_records]
    deltas = {lids[i + 1] - lids[i] for i in range(len(lids) - 1)}
    capture["lid_allocation"] = {
        "first": lids[0], "last": lids[-1],
        "increments": sorted(deltas),
        "note": ("sequential" if deltas == {1} else "non-sequential"),
    }
    print("  lid allocation: %s (increments %s)"
          % (capture["lid_allocation"]["note"], sorted(deltas)))

# ---------------------------------------------------------------------------
section("create_link failure paths")
failures = {}
for device in ("gpib0,29", "gpib0,31", "gpib0,99", "bogus0", "gpib1,5",
               "gpib0", "inst0", ""):
    if device.startswith("gpib0,") and device.split(",")[-1].isdigit() \
            and int(device.split(",")[-1]) in links:
        continue
    try:
        (link, error), ms = timed(client.create_link, device)
    except Exception as exc:
        failures[device] = "exception: %s" % type(exc).__name__
        print("  %-10s EXCEPTION %s" % (device, type(exc).__name__))
        continue
    if link is not None:
        failures[device] = {"error": 0, "lid": link.lid,
                            "abort_port": link.abort_port,
                            "max_recv_size": link.max_recv_size}
        print("  %-10s ACCEPTED lid=%d" % (device, link.lid))
        client.destroy_link(link)
    else:
        failures[device] = {"error": error, "text": error_text(error),
                            "ms": ms}
        print("  %-10s error %-28s %6.1f ms" % (device, error_text(error), ms))
capture["create_link_failures"] = failures

# ---------------------------------------------------------------------------
section("device_write / device_read on GPIB %d" % args.dut)
dut = links.get(args.dut)
rw = {}
if dut is None:
    print("  --dut %d is not on the bus; skipping" % args.dut)
else:
    (err, size), ms = timed(client.device_write, dut, b"*IDN?\n")
    rw["write"] = {"error": err, "text": error_text(err), "size": size,
                   "sent": 6, "ms": ms}
    print("  write   error=%-22s size=%d  %.1f ms" % (error_text(err), size, ms))

    (err, reason, data), ms = timed(client.device_read, dut)
    rw["read"] = {"error": err, "text": error_text(err), "reason": reason,
                  "reason_text": reason_text(reason),
                  "data": data.decode(errors="replace"), "ms": ms}
    print("  read    error=%-22s reason=0x%02X (%s)"
          % (error_text(err), reason, reason_text(reason)))
    print("          data=%r  %.1f ms" % (data, ms))

    # A short requestSize must stop on REQCNT and leave the remainder queued.
    client.device_write(dut, b"*IDN?\n")
    err, reason, first = client.device_read(dut, request_size=10)
    print("  read(10) error=%-22s reason=0x%02X (%s) data=%r"
          % (error_text(err), reason, reason_text(reason), first))
    err2, reason2, rest = client.device_read(dut, request_size=4096)
    print("  read     remainder reason=0x%02X (%s) data=%r"
          % (reason2, reason_text(reason2), rest))
    rw["partial_read"] = {
        "request_size": 10, "first": first.decode(errors="replace"),
        "first_reason": reason, "first_reason_text": reason_text(reason),
        "remainder": rest.decode(errors="replace"),
        "remainder_reason": reason2,
        "remainder_reason_text": reason_text(reason2),
    }

    # Reading with nothing queued: the error and how long the gateway waits.
    for io_timeout in (500, 2000):
        (err, reason, data), ms = timed(
            client.device_read, dut, io_timeout=io_timeout)
        print("  read empty io_timeout=%-5d error=%-22s reason=0x%02X "
              "waited %.0f ms" % (io_timeout, error_text(err), reason, ms))
        rw.setdefault("empty_read", []).append({
            "io_timeout": io_timeout, "error": err, "text": error_text(err),
            "reason": reason, "elapsed_ms": ms,
            "data": data.decode(errors="replace"),
        })

    # termChar handling.
    client.device_write(dut, b"*IDN?\n")
    (err, reason, data), ms = timed(
        client.device_read, dut, flags=READ_TERMCHRSET, term_char=ord(","))
    print("  read termChar=',' error=%-18s reason=0x%02X (%s) data=%r"
          % (error_text(err), reason, reason_text(reason), data))
    rw["term_char"] = {"term_char": ",", "error": err, "reason": reason,
                       "reason_text": reason_text(reason),
                       "data": data.decode(errors="replace")}
    # Drain whatever the terminator left behind.
    try:
        client.device_read(dut, io_timeout=800)
    except Exception:
        pass

    # write without the END flag.
    (err, size), ms = timed(client.device_write, dut, b"*IDN?\n", flags=0)
    print("  write flags=0 (no END) error=%-18s size=%d" % (error_text(err), size))
    rw["write_without_end"] = {"error": err, "text": error_text(err),
                               "size": size}
    try:
        client.device_read(dut, io_timeout=1500)
    except Exception:
        pass

capture["read_write"] = rw

# ---------------------------------------------------------------------------
section("device_readstb: serial poll through the gateway")
stb = {}
for addr, link in sorted(links.items()):
    err_idle, idle = client.device_readstb(link)
    client.device_write(link, b"*IDN?\n")
    time.sleep(0.25)
    err_pend, pending = client.device_readstb(link)
    try:
        client.device_read(link, io_timeout=2000)
    except Exception:
        pass
    err_after, after = client.device_readstb(link)
    stb[addr] = {"idle": idle, "pending": pending, "after_read": after,
                 "errors": [err_idle, err_pend, err_after]}
    print("  gpib0,%-2d idle=%-4d pending=%-4d after_read=%-4d"
          % (addr, idle, pending, after))
capture["serial_poll"] = stb

# ---------------------------------------------------------------------------
section("locking")
lock_info = {}
if dut is not None:
    err = client.device_lock(dut, lock_timeout=1000)
    lock_info["lock"] = {"error": err, "text": error_text(err)}
    print("  device_lock       %s" % error_text(err))

    # A second client must be refused while the lock is held.
    try:
        other = VXI11Client(HOST, port=ports["core"], timeout=6.0,
                            client_id=0x99)
        link2, err2 = other.create_link("gpib0,%d" % args.dut, lock=True,
                                        lock_timeout=500)
        lock_info["second_client_locked"] = {
            "error": err2, "text": error_text(err2),
            "link_created": link2 is not None}
        print("  second client     %s" % error_text(err2))
        if link2 is not None:
            other.destroy_link(link2)
        other.close()
    except Exception as exc:
        lock_info["second_client_locked"] = "exception: %s" % exc
        print("  second client     exception %s" % exc)

    err = client.device_unlock(dut)
    lock_info["unlock"] = {"error": err, "text": error_text(err)}
    print("  device_unlock     %s" % error_text(err))

    err = client.device_unlock(dut)
    lock_info["unlock_when_unlocked"] = {"error": err, "text": error_text(err)}
    print("  unlock again      %s" % error_text(err))
capture["locking"] = lock_info

# ---------------------------------------------------------------------------
section("device_clear / trigger / remote / local on GPIB %d only" % args.dut)
ops = {}
if dut is not None:
    for name, fn in (("device_clear", client.device_clear),
                     ("device_trigger", client.device_trigger),
                     ("device_remote", client.device_remote),
                     ("device_local", client.device_local)):
        try:
            err, ms = timed(fn, dut)
            ops[name] = {"error": err, "text": error_text(err), "ms": ms}
            print("  %-15s %-28s %6.1f ms" % (name, error_text(err), ms))
        except Exception as exc:
            ops[name] = "exception: %s" % type(exc).__name__
            print("  %-15s EXCEPTION %s" % (name, type(exc).__name__))
        time.sleep(0.2)
    # Always finish in local so the front panel is not left locked out.
    try:
        client.device_local(dut)
    except Exception:
        pass
capture["device_ops"] = ops

# ---------------------------------------------------------------------------
section("destroy_link and use-after-destroy")
after = {}
if dut is not None:
    err = client.destroy_link(dut)
    after["destroy"] = {"error": err, "text": error_text(err)}
    print("  destroy_link      %s" % error_text(err))

    err, size = client.device_write(dut, b"*IDN?\n")
    after["write_after_destroy"] = {"error": err, "text": error_text(err)}
    print("  write after       %s" % error_text(err))

    err, reason, _ = client.device_read(dut, io_timeout=800)
    after["read_after_destroy"] = {"error": err, "text": error_text(err)}
    print("  read after        %s" % error_text(err))

    err = client.destroy_link(dut)
    after["destroy_twice"] = {"error": err, "text": error_text(err)}
    print("  destroy again     %s" % error_text(err))
    links.pop(args.dut, None)
capture["after_destroy"] = after

# ---------------------------------------------------------------------------
section("web and telnet identity")
services = {}
try:
    s = socket.socket(); s.settimeout(6.0)
    s.connect((HOST, 80))
    s.sendall(("GET / HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n"
               % HOST).encode())
    body = b""
    while len(body) < 60000:
        try:
            chunk = s.recv(8192)
        except socket.timeout:
            break
        if not chunk:
            break
        body += chunk
    s.close()
    text = body.decode("utf-8", errors="replace")
    services["http"] = {"bytes": len(body), "body": text}
    header = text.split("\r\n\r\n")[0]
    print("  HTTP %d bytes" % len(body))
    for line in header.splitlines()[:6]:
        print("    | %s" % line[:100])
    low = text.lower()
    i = low.find("<title>")
    if i != -1:
        print("    title: %s" % text[i:i + 80].replace("\n", " "))
except Exception as exc:
    services["http"] = "failed: %s" % exc
    print("  HTTP failed: %s" % exc)

try:
    s = socket.socket(); s.settimeout(8.0)
    s.connect((HOST, 23))
    time.sleep(1.5)
    banner = b""
    try:
        banner = s.recv(4096)
    except socket.timeout:
        pass
    services["telnet_banner"] = banner.decode(errors="replace")
    print("  telnet banner: %r" % banner[:200])
    s.close()
except Exception as exc:
    services["telnet_banner"] = "failed: %s" % exc
    print("  telnet failed: %s" % exc)

capture["services"] = services

# ---------------------------------------------------------------------------
for link in links.values():
    try:
        client.destroy_link(link)
    except Exception:
        pass
client.close()

with open(OUT, "w") as f:
    json.dump(capture, f, indent=2)
print("\nwrote %s" % os.path.normpath(OUT))
