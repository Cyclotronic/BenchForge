"""
Drain and report every instrument's error queue.

Run this after a session against real hardware. Two reasons:

  1. It tells you what your own tooling did. An ungated ++read logs
     -420 "Query UNTERMINATED" on any instrument that had nothing to say, and
     enough of those overflow the queue (-350) and destroy real evidence.
  2. It hands the bench back clean, so the next person's queue means something.

Reading the queue empties it -- that is the point. *ESR? is latching and is
likewise cleared by reading. Neither changes a measurement function, a range,
or an output state, so this is safe to run on a live bench.

    python tools/check_errors.py --host 192.168.1.80

Exit code 0 when every queue was already clean, 1 when anything was found.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.buslib import Link, scan_bus

MAX_DRAIN = 40

#: Standard Event Status Register bits (IEEE 488.2).
ESR_BITS = [
    (0x01, "OPC  operation complete"),
    (0x02, "RQC  request control"),
    (0x04, "QYE  query error"),
    (0x08, "DDE  device-dependent error"),
    (0x10, "EXE  execution error"),
    (0x20, "CME  command error"),
    (0x40, "URQ  user request"),
    (0x80, "PON  power-on since last read"),
]

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="192.168.1.80")
parser.add_argument("--port", type=int, default=1234)
parser.add_argument("--addr", type=int, default=None,
                    help="restrict to one GPIB address")
args = parser.parse_args()


def is_empty(reply):
    """'0,"No error"' or '+0,"No error"' means the queue is drained."""
    if not reply:
        return True
    head = reply.split(",")[0].strip().lstrip("+")
    return head in ("0", "") or "no error" in reply.lower()


link = Link((args.host, args.port))
print("Scanning %s:%d ..." % (args.host, args.port))
found, unstable = scan_bus(link)
if args.addr is not None:
    found = {a: i for a, i in found.items() if a == args.addr}

summary = {}
for addr in sorted(found):
    name = found[addr].split(",")[1].strip() if "," in found[addr] else "dev"
    print("\n=== %d  %s ===" % (addr, name))

    esr = link.query_instrument(addr, "*ESR?").decode(errors="replace").strip()
    print("  *ESR? %s" % (esr or "<silent>"))
    try:
        bits = int(esr.lstrip("+"))
        for mask, label in ESR_BITS:
            if bits & mask:
                print("        %s" % label)
    except ValueError:
        pass

    entries = []
    for _ in range(MAX_DRAIN):
        reply = link.query_instrument(
            addr, "SYST:ERR?").decode(errors="replace").strip()
        if is_empty(reply):
            break
        entries.append(reply)
        print("  ERR  %s" % reply)
    if not entries:
        print("  queue clean")
    summary[addr] = (name, esr, entries)

link.restore_baseline()
link.close()

print("\n" + "=" * 72)
print("%-3s %-18s %-8s %s" % ("A", "NAME", "ESR", "ERRORS DRAINED"))
print("-" * 72)
dirty = 0
for addr, (name, esr, entries) in sorted(summary.items()):
    overflow = any("-350" in e for e in entries)
    dirty += len(entries)
    print("%-3d %-18s %-8s %s%s" % (
        addr, name, esr or "-", len(entries) or "none",
        "   <- OVERFLOWED, errors were lost" if overflow else ""))

if unstable:
    print("\nunstable addresses skipped: %s" % unstable)

if dirty:
    print("\n%d errors drained. The queues are clean now." % dirty)
    sys.exit(1)
print("\nEvery queue was already clean.")
