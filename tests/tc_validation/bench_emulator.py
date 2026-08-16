"""
Instrumented gateway emulator for validating TestController.

Two gateways, selected with --gateway:

  prologix (default)
    Runs BenchForge's Prologix emulator with the default 5-device bench (which
    matches TestController's configured addresses 1-5 exactly) and records
    every connection event and every frame the emulator emits.

    The point of interest is the connection count. In 3.41,
    SharedInterfacePrologixEthernet.neededCommInterface() unconditionally
    replaced this.ci with a brand new SocketInterface on every call, so each
    device thread opened its own TCP connection and clobbered the shared
    field. In 3.49 that assignment is guarded by `if (this.ci == null)`, so
    five device threads should produce exactly one connection.

  e5810
    Runs the VXI-11 gateway emulator, which refuses the device string
    TestController hardcodes. The point of interest is create_link: an
    unpatched build asks for 'inst0' and is refused with error 3, a patched
    build asks for 'gpib0,<address>' and links. Requires ports 111 and 1024,
    so on Windows run it from an elevated shell or remap with
    --portmap-port/--core-port.

    This mode is the oracle for docs/E5810A_PROTOCOL_GUIDE.md section 4-5 and
    for tools/patch_testcontroller_e5810.py. The negative control is free: run
    it once against the unpatched jar first.

Usage:
    python bench_emulator.py --label 3.49
    python bench_emulator.py --gateway e5810 --label e5810-unpatched
    (Ctrl+C to stop and print the report)

Writes <label>-tx.jsonl in both modes, consumed by analyze_tc_log.py for the
truncation cross-check.
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
from core.vxi11_emulator import VXI11EmulatorServer


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


class VXI11Recorder(Recorder):
    """
    Recorder for the E5810 gateway.

    Connection counting is not the discriminator here -- the 3.49 exercise
    already showed it discriminates nothing -- so this watches the two places
    the GPIB address can appear: the portmapper's GETPORT filter argument,
    where TestController currently puts it and where it is discarded, and the
    create_link device string, where it belongs.
    """

    def __init__(self, label, outdir):
        super().__init__(label, outdir)
        self.getports = []      # (t, prog, requested_port, returned_port)
        self.links = []         # (t, device_string, error, detail)
        self.destroys = []      # (t, device_string)

    def on_diag(self, rec):
        ev = rec.get("event") or ""
        low = ev.lower()
        with self.lock:
            if low.startswith("portmap getport"):
                self.getports.append((self.el(), rec.get("getport_prog"),
                                      rec.get("getport_port"),
                                      rec.get("getport_result")))
                print(f"[{self.el():8.3f}] GETPORT   prog={rec.get('getport_prog')} "
                      f"port-arg={rec.get('getport_port')} -> {rec.get('getport_result')}")
                return
            if low.startswith("link created"):
                name = ev.split(":", 1)[1].strip()
                self.links.append((self.el(), name, 0, rec.get("detail", "")))
                print(f"[{self.el():8.3f}] LINK OK   {name}   {rec.get('detail','')}")
                return
            if low.startswith("create_link refused"):
                # The event carries the name repr'd; strip the quoting.
                name = ev.split(":", 1)[1].strip().strip("'\"")
                code = rec.get("code")
                self.links.append((self.el(), name, code, rec.get("detail", "")))
                print(f"[{self.el():8.3f}] LINK FAIL {name}   error {code}")
                return
            if low.startswith("link destroyed"):
                self.destroys.append((self.el(), ev.split(":", 1)[1].strip()))
                return
            if rec.get("level") == "WARN":
                self.warnings.append((self.el(), ev, rec.get("detail")))
                print(f"[{self.el():8.3f}] WARN      {ev}: {rec.get('detail')}")

    def report(self):
        with self.lock:
            ok = [l for l in self.links if l[2] == 0]
            bad = [l for l in self.links if l[2] != 0]
            names = sorted({l[1] for l in self.links})

            # Where did the GPIB address actually travel? Nonzero GETPORT port
            # arguments are addresses that reached the gateway and were thrown
            # away; addresses in accepted device strings are addresses that
            # reached an instrument.
            in_getport = sorted({p for _, _, p, _ in self.getports if p})
            in_device = sorted({int(n.split(",")[1]) for _, n, e, _ in self.links
                                if e == 0 and "," in n and
                                n.split(",")[1].isdigit()})

            print("\n" + "=" * 78)
            print(f"  BenchForge E5810 <-> TestController {self.label}  link report")
            print("=" * 78)
            print(f"  GETPORT calls            : {len(self.getports)}")
            print(f"  create_link attempts     : {len(self.links)}")
            print(f"    linked                 : {len(ok)}")
            print(f"    refused                : {len(bad)}")
            print(f"  destroy_link             : {len(self.destroys)}")
            print(f"  Frames emulator received : {len(self.rx_frames)}")
            print(f"  Frames emulator sent     : {len(self.tx_frames)}")
            print(f"  Protocol warnings        : {len(self.warnings)}")

            print(f"\n  Device strings requested : {names or '(none)'}")
            print(f"  Addresses in GETPORT arg : {in_getport or '(none)'}")
            print(f"  Addresses in device str  : {in_device or '(none)'}")

            if self.links:
                print("\n  create_link timeline:")
                for t, name, err, detail in self.links:
                    status = "ok" if err == 0 else f"error {err}"
                    print(f"    +{t:7.3f}s  {name:<16} {status}")

            if self.warnings:
                print("\n  Warnings:")
                for t, e, d in self.warnings:
                    print(f"    +{t:7.3f}s  {e}: {d}")

            addrs = sorted({f["addr"] for f in self.tx_frames
                            if f["addr"] is not None})
            print(f"\n  Addresses the emulator answered: {addrs}")

            print("\n  VERDICT (create_link device string):")
            if not self.links:
                print("    INCONCLUSIVE - TestController never called create_link.")
                if self.getports:
                    print("    It did reach the portmapper, so it is talking to this")
                    print("    emulator but did not get as far as opening a link.")
                else:
                    print("    Nothing arrived at all. Check the address and ports.")
            elif ok and not bad:
                print(f"    PASS - every create_link linked ({len(ok)}/{len(self.links)}).")
                if in_device:
                    print("    The GPIB address is carried in the device string, which is")
                    print("    the fix. Addresses linked: " + str(in_device))
                else:
                    print("    NOTE: linked on the bare interface name only, with no")
                    print("    address in any device string. Instruments are not addressed.")
            elif bad and not ok:
                print(f"    FAIL - every create_link was refused ({len(bad)}/{len(self.links)}).")
                if any(n == "inst0" for _, n, _, _ in self.links):
                    print("    'inst0' is the unpatched literal at LXIInterface.java:62.")
                    print("    This is the expected negative control, not a harness fault.")
                if in_getport and not in_device:
                    print(f"    The addresses {in_getport} arrived in the GETPORT port")
                    print("    argument and were discarded there. That is finding F1.")
            else:
                print(f"    MIXED - {len(ok)} linked, {len(bad)} refused. Timeline above.")

            print(f"\n  TX/RX log: {self.tx_path}")
            print("=" * 78)
        self.tx_file.close()


def run_prologix(args, registry):
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
    describe_bench(registry)
    print("\n  Point TestController's PrologixEthernet interface at this host,")
    print("  connect all five devices, let it stream, then Ctrl+C here.")
    print("=" * 78 + "\n")
    return server, rec


def run_e5810(args, registry):
    server = VXI11EmulatorServer(host=args.host, core_port=args.core_port,
                                 portmap_port=args.portmap_port,
                                 registry=registry)
    rec = VXI11Recorder(args.label, args.outdir)
    server.add_diagnostic_callback(rec.on_diag)
    server.add_packet_callback(rec.on_packet)
    server.start()

    print("=" * 78)
    print(f"  Instrumented E5810 gateway emulator - label '{args.label}'")
    print(f"  Portmapper {args.host}:{args.portmap_port}   "
          f"core channel {args.host}:{args.core_port}")
    print(f"  Accepts '{VXI11EmulatorServer.INTERFACE}' and "
          f"'{VXI11EmulatorServer.INTERFACE},0'.."
          f"'{VXI11EmulatorServer.INTERFACE},{VXI11EmulatorServer.MAX_GPIB_ADDRESS}'; "
          "refuses 'inst0' with error 3.")
    describe_bench(registry)
    print("\n  Add this line to TestController's settingsGPIB.txt, adjusting the")
    print("  address, then configure devices as E:1 .. E:5 --")
    print(f"    Keysight E5810|E|{args.host if args.host != '0.0.0.0' else '127.0.0.1'}||")
    print("  Run the unpatched jar first for the negative control, then the")
    print("  patched one. Ctrl+C here for the report.")
    print("=" * 78 + "\n")
    return server, rec


def describe_bench(registry):
    print("  Bench:")
    for a in sorted(registry.devices):
        d = registry.devices[a]
        print(f"    A:{a:<3} {d.name:<16} {d.idn}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="prologix",
                    choices=["prologix", "e5810"],
                    help="which gateway to emulate (default: prologix)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=1234,
                    help="Prologix TCP port (prologix mode only)")
    ap.add_argument("--portmap-port", type=int, default=111,
                    help="portmapper port (e5810 mode only)")
    ap.add_argument("--core-port", type=int, default=1024,
                    help="VXI-11 core channel port (e5810 mode only)")
    ap.add_argument("--label", default="run")
    ap.add_argument("--policy", default="single_connection",
                    choices=["single_connection", "multi_connection"],
                    help="socket policy (prologix mode only)")
    ap.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--seconds", type=float, default=0,
                    help="stop and report after N seconds (0 = run until Ctrl+C)")
    args = ap.parse_args()

    registry = InstrumentRegistry()   # DEFAULT_BENCH = addresses 1..5
    if args.gateway == "e5810":
        server, rec = run_e5810(args, registry)
    else:
        server, rec = run_prologix(args, registry)

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
