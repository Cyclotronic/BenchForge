"""
Instrumented Prologix emulator for validating TestController 3.49.

Runs BenchForge's Prologix emulator with the default 5-device bench (which
matches TestController's configured addresses 1-5 exactly) and records every
connection event and every frame the emulator emits.

The point of interest is the connection count. In 3.41,
SharedInterfacePrologixEthernet.neededCommInterface() unconditionally replaced
this.ci with a brand new SocketInterface on every call, so each device thread
opened its own TCP connection and clobbered the shared field. In 3.49 that
assignment is guarded by `if (this.ci == null)`, so five device threads should
produce exactly one connection.

Usage:
    python bench_emulator.py --label 3.49
    (Ctrl+C to stop and print the report)

Writes <label>-tx.jsonl, consumed by analyze_tc_log.py for the truncation
cross-check.
"""

import argparse
import json
import os
import sys
import threading
import time

# Repository root, two levels up from tests/tc_validation/.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    os.pardir, os.pardir))
sys.path.insert(0, REPO)

from core.device_emulator import InstrumentRegistry
from core.prologix_emulator import PrologixEmulatorServer


class Recorder:
    def __init__(self, label, outdir):
        self.label = label
        self.lock = threading.Lock()
        self.connections = []      # (t, client_id)
        self.disconnections = []   # (t, detail)
        self.displacements = 0
        self.warnings = []
        self.tx_frames = []        # dicts
        self.rx_frames = []
        self.active = set()
        self.t0 = time.time()
        self.tx_path = os.path.join(outdir, f"{label}-tx.jsonl")
        self.tx_file = open(self.tx_path, "w", encoding="utf-8")

    def el(self):
        return round(time.time() - self.t0, 3)

    def on_diag(self, rec):
        ev = (rec.get("event") or "").lower()
        with self.lock:
            if "client connected" in ev:
                cid = rec.get("detail", "")
                if self.active:
                    # A second live connection means the previous socket was
                    # displaced by the single-connection policy, exactly as the
                    # physical controller does.
                    self.displacements += 1
                self.active.add(cid)
                self.connections.append((self.el(), cid))
                print(f"[{self.el():8.3f}] CONNECT   {cid}   (total={len(self.connections)}, live={len(self.active)})")
            elif "disconnect" in ev or "client closed" in ev or "connection closed" in ev:
                cid = rec.get("detail", "")
                self.active.discard(cid)
                self.disconnections.append((self.el(), cid))
                print(f"[{self.el():8.3f}] DISCONNECT {cid}")
            elif rec.get("level") == "WARN":
                self.warnings.append((self.el(), rec.get("event"), rec.get("detail")))
                print(f"[{self.el():8.3f}] WARN      {rec.get('event')}: {rec.get('detail')}")

    def on_packet(self, ev):
        raw = ev.get("raw_bytes") or b""
        rec = {
            "t": self.el(),
            "dir": ev.get("direction"),
            "addr": ev.get("address"),
            "client": ev.get("client"),
            "text": ev.get("text"),
            "hex": raw.hex(),
        }
        with self.lock:
            if str(ev.get("direction", "")).upper().startswith("TX") or \
               "out" in str(ev.get("direction", "")).lower():
                self.tx_frames.append(rec)
            else:
                self.rx_frames.append(rec)
            self.tx_file.write(json.dumps(rec) + "\n")
            self.tx_file.flush()

    def report(self):
        with self.lock:
            n = len(self.connections)
            print("\n" + "=" * 78)
            print(f"  BenchForge <-> TestController {self.label}  connection report")
            print("=" * 78)
            print(f"  TCP connections accepted : {n}")
            print(f"  Socket displacements     : {self.displacements}")
            print(f"  Frames emulator received : {len(self.rx_frames)}")
            print(f"  Frames emulator sent     : {len(self.tx_frames)}")
            print(f"  Protocol warnings        : {len(self.warnings)}")
            print()
            print("  Connection timeline:")
            for t, cid in self.connections:
                print(f"    +{t:7.3f}s  {cid}")
            if self.warnings:
                print("\n  Warnings:")
                for t, e, d in self.warnings:
                    print(f"    +{t:7.3f}s  {e}: {d}")

            addrs = sorted({f["addr"] for f in self.tx_frames if f["addr"] is not None})
            print(f"\n  Addresses the emulator answered: {addrs}")

            print("\n  VERDICT (neededCommInterface reuse):")
            if n == 0:
                print("    INCONCLUSIVE - TestController never connected.")
            elif n == 1:
                print("    PASS - one connection for all device threads.")
                print("    The shared SocketInterface was created once and reused.")
            else:
                print(f"    FAIL/SUSPECT - {n} connections for one interface.")
                print("    Each device thread appears to have built its own SocketInterface,")
                print("    which is the 3.41 behaviour the 3.49 null-guard was meant to fix.")
            print(f"\n  TX/RX log: {self.tx_path}")
            print("=" * 78)
        self.tx_file.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--label", default="run")
    ap.add_argument("--policy", default="single_connection",
                    choices=["single_connection", "multi_connection"])
    ap.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--seconds", type=float, default=0,
                    help="stop and report after N seconds (0 = run until Ctrl+C)")
    args = ap.parse_args()

    registry = InstrumentRegistry()   # DEFAULT_BENCH = addresses 1..5
    server = PrologixEmulatorServer(host=args.host, port=args.port,
                                    registry=registry,
                                    connection_policy=args.policy)
    rec = Recorder(args.label, args.outdir)
    server.add_diagnostic_callback(rec.on_diag)
    server.add_packet_callback(rec.on_packet)

    server.start()
    print("=" * 78)
    print(f"  Instrumented Prologix emulator - label '{args.label}'")
    print(f"  Listening {args.host}:{args.port}   policy={args.policy}")
    print("  Bench:")
    for a in sorted(registry.devices):
        d = registry.devices[a]
        print(f"    A:{a:<3} {d.name:<16} {d.idn}")
    print("\n  Point TestController's PrologixEthernet interface at this host,")
    print("  connect all five devices, let it stream, then Ctrl+C here.")
    print("=" * 78 + "\n")

    deadline = (time.time() + args.seconds) if args.seconds else None
    try:
        while deadline is None or time.time() < deadline:
            time.sleep(0.5)
        print("\n\nTime limit reached, stopping...")
    except KeyboardInterrupt:
        print("\n\nStopping...")
    finally:
        try:
            server.stop()
        except Exception:
            pass
        rec.report()


if __name__ == "__main__":
    main()
