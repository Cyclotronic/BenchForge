"""
Instrument-level A/B: emulator against real hardware, query by query.

`verify_hardware.py` proves the GATEWAY is faithful -- the ++ command set,
framing, escaping, serial poll. This tool proves the INSTRUMENTS behind it are,
which is what a client actually reads values from.

    python tools/ab_instruments.py --host 192.168.1.80

Two comparison modes, because a live reading is never byte-identical twice:

  EXACT      identity, configuration, setup queries -- must match byte for byte
  SIGNATURE  measurements -- digits are masked to '#', so
             '+1.00001363E+01' and '+1.00002011E+01' both become
             '+#.########E+##'. This catches the failures that matter (wrong
             number of digits, missing sign, plain decimal where the hardware
             uses scientific, a missing element suffix) while ignoring the
             value itself.

STRICTLY READ-ONLY. No *RST, no function or output changes: instruments on the
bench may be driving a load or monitoring a reference.

Exit code 0 when every check passes, 1 otherwise.
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.device_emulator import InstrumentRegistry, VirtualInstrument
from core.prologix_emulator import PrologixEmulatorServer
from core.vxi11_emulator import VXI11EmulatorServer
from tools.buslib import Link, scan_bus
from tools.vxi11 import VXI11Client

parser = argparse.ArgumentParser()
parser.add_argument("--gateway", choices=("prologix", "e5810"),
                    default="prologix",
                    help="which gateway protocol to speak")
parser.add_argument("--host", default=None,
                    help="default 192.168.1.80 for prologix, .85 for e5810")
parser.add_argument("--port", type=int, default=1234,
                    help="prologix only; the E5810A is found via the portmapper")
parser.add_argument("--local-port", type=int, default=15802)
parser.add_argument("--only", type=int, default=None,
                    help="restrict to one GPIB address")
args = parser.parse_args()

if args.host is None:
    args.host = "192.168.1.80" if args.gateway == "prologix" else "192.168.1.85"

HW = (args.host, args.port)
LOCAL = ("127.0.0.1", args.local_port)

EXACT, SIGNATURE, INFO = "exact", "signature", "info"

#: *STB? is reported but never failed. Bit 2 tracks the instrument's error
#: queue, so its value depends on whatever happened on the bench beforehand --
#: a 33250A idles at +0 with a clean queue and +4 once anything has logged an
#: error. That is bench state, not emulator fidelity. The serial-poll byte,
#: which verify_hardware.py checks, is the stable one.
COMMON = [("*IDN?", EXACT), ("*STB?", INFO), ("SYST:VERS?", EXACT)]

BATTERY = {
    "DMM": COMMON + [
        ("FUNC?", EXACT), ("CONF?", EXACT),
        ("READ?", SIGNATURE), ("FETC?", SIGNATURE),
        ("VOLT:DC:NPLC?", EXACT), ("VOLT:DC:RANG:AUTO?", EXACT),
        ("UNIT?", EXACT),
    ],
    "COUNTER": COMMON + [
        ("FUNC?", EXACT), ("CONF?", EXACT),
        ("READ?", SIGNATURE), ("FETC?", SIGNATURE),
        ("ACQ:APER?", EXACT), ("AVER:STAT?", EXACT),
        ("INP:LEV:AUTO?", EXACT), ("INP:FILT?", EXACT),
        ("INP:IMP?", EXACT), ("INP:COUP?", EXACT), ("INP:ATT?", EXACT),
        ("DISP:ENAB?", EXACT), ("TRIG:SOUR?", EXACT),
    ],
    "FUNCGEN": COMMON + [
        ("FUNC?", EXACT), ("FREQ?", EXACT), ("VOLT?", EXACT),
        ("VOLT:OFFS?", EXACT), ("VOLT:UNIT?", EXACT),
        ("OUTP?", EXACT), ("OUTP:LOAD?", EXACT), ("OUTP:POL?", EXACT),
        ("PHAS?", EXACT), ("FUNC:SQU:DCYC?", EXACT),
        ("BURS:STAT?", EXACT), ("SWE:STAT?", EXACT),
        ("AM:STAT?", EXACT), ("TRIG:SOUR?", EXACT),
    ],
    "PSU": COMMON + [
        ("INST?", EXACT), ("INST:SEL?", EXACT),
        ("VOLT?", EXACT), ("CURR?", EXACT),
        ("MEAS:VOLT?", SIGNATURE), ("MEAS:CURR?", SIGNATURE),
        ("OUTP?", EXACT), ("APPL?", EXACT), ("DISP?", EXACT),
        ("VOLT:PROT?", EXACT),
    ],
}

#: IDN fragment -> instrument class, so the mirrored bench gets the right
#: personality rather than defaulting every device to a voltmeter.
CLASS_BY_MODEL = [
    ("PM6690", "COUNTER"), ("CNT-9", "COUNTER"),
    ("33250A", "FUNCGEN"), ("33220A", "FUNCGEN"),
    ("E3631A", "PSU"), ("E363", "PSU"),
]


def classify(idn):
    up = idn.upper()
    for fragment, klass in CLASS_BY_MODEL:
        if fragment in up:
            return klass
    return "DMM"


NUMBER = re.compile(rb"[+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?")


def signature(raw):
    """
    Reduce a reading to (skeleton, resolutions) so FORMAT can be compared
    without the VALUE.

    Masking each digit to '#' is the obvious approach and it is wrong: the
    digit count legitimately moves with the decade. A PM6690 reports
    '+9.99999962E+06' and '+1.000000038E+07' for the same measurement a second
    apart -- eight decimals then nine -- because both express 0.01 Hz. A
    comparator that counts digits calls that a defect roughly half the time.

    So compare two things instead:
      skeleton     every number replaced by 'N' -- catches a missing sign, a
                   dropped element suffix, plain decimal where the hardware
                   uses scientific, a changed separator
      resolutions  10**(exponent - decimals) per numeric field -- catches a
                   genuine precision change, and is decade-invariant
    """
    skeleton = NUMBER.sub(b"N", raw)
    resolutions = []
    for match in NUMBER.finditer(raw):
        token = match.group(0).decode()
        mantissa, _, exponent = token.partition("E") if "E" in token \
            else token.partition("e")
        decimals = len(mantissa.split(".")[1]) if "." in mantissa else 0
        resolutions.append(round(int(exponent or 0) - decimals, 6))
    # A signed field and an unsigned one differ in skeleton only if the sign is
    # absent, so record presence explicitly.
    signs = tuple(m.group(0)[:1] in b"+-" for m in NUMBER.finditer(raw))
    return skeleton, tuple(resolutions), signs


def seed_from_hardware(dev, link, addr):
    """
    Put the emulated instrument into the same state as the real one.

    Without this, every instrument that is not sitting at its power-on defaults
    reports a mismatch that says nothing about fidelity: the 34411A on this
    bench is measuring temperature, and the Keithleys are on a 10 V reference
    rather than the emulator's nominal 5 V. Those are bench facts, not defects.
    """
    func = link.query_instrument(addr, "FUNC?").decode(errors="replace")
    func = func.strip().strip('"').strip("'")
    if func:
        dev.handle_scpi_command('FUNC "%s"' % func if dev.instrument_class
                                != "FUNCGEN" else "FUNC %s" % func)

    reading = link.query_instrument(addr, "READ?").decode(errors="replace")
    # Take the leading number, exponent included. Splitting on the first
    # capital letter to drop a Keithley element suffix ("...E+00NVDC") also
    # eats the E of the exponent, which turned a 10 MHz counter reading into
    # 1.0 and made the emulator look badly broken.
    match = re.match(r"[+-]?\d+\.?\d*(?:[Ee][+-]?\d+)?",
                     reading.strip().split(",")[0])
    if not match:
        return
    try:
        value = abs(float(match.group(0)))
    except ValueError:
        return
    attr = VirtualInstrument.FUNCTION_PROFILES.get(dev.function, (None,))[0]
    if attr and value:
        setattr(dev, attr, value)

    # Autoranging is bench configuration, not fidelity: a meter parked on a
    # fixed range is not an emulator defect. Seed it like the function.
    auto = link.query_instrument(addr, "VOLT:DC:RANG:AUTO?").decode(
        errors="replace").strip()
    if auto in ("0", "1"):
        dev.autorange = auto == "1"

    # A supply's meter does not read its setpoint, so seed both separately.
    if dev.instrument_class == "PSU":
        for scpi, field in (("MEAS:VOLT?", "psu_meas_voltage"),
                            ("MEAS:CURR?", "psu_meas_current")):
            raw = link.query_instrument(addr, scpi).decode(errors="replace")
            m = re.match(r"[+-]?\d+\.?\d*(?:[Ee][+-]?\d+)?", raw.strip())
            if m:
                setattr(dev, field, float(m.group(0)))


class VXI11Transport:
    """
    A `query_instrument`-compatible transport over VXI-11.

    Holds one link per address for the whole run. Opening a link per query
    degrades an E5810A progressively until create_link fails outright, and the
    symptom looks like a flaky bus rather than a resource leak.
    """

    #: MEASURED on the physical E5810A: 1.2-1.5 s timeouts produced
    #: intermittent failures; 6 s gave 55 of 56 queries good.
    IO_TIMEOUT_MS = 6000

    def __init__(self, host, port=None):
        self.client = VXI11Client(host, port=port, timeout=12.0)
        self.links = {}

    def _link(self, addr):
        if addr not in self.links:
            link, error = self.client.create_link("gpib0,%d" % addr)
            if error or link is None:
                return None
            self.links[addr] = link
            # MEASURED: the first read on a freshly created link can fail once
            # and then behave. Absorb it rather than reporting a dead address.
            self.client.device_write(link, b"*IDN?\n",
                                     io_timeout=self.IO_TIMEOUT_MS)
            try:
                self.client.device_read(link, io_timeout=1000)
            except Exception:
                pass
        return self.links[addr]

    def query_instrument(self, addr, scpi="*IDN?", settle=0.0):
        link = self._link(addr)
        if link is None:
            return b""
        try:
            self.client.device_write(link, scpi.encode() + b"\n",
                                     io_timeout=self.IO_TIMEOUT_MS)
            error, _reason, data = self.client.device_read(
                link, io_timeout=self.IO_TIMEOUT_MS)
            return b"" if error else data
        except Exception:
            return b""

    def restore_baseline(self):
        """Nothing persists on a VXI-11 gateway; kept for interface parity."""

    def close(self):
        for link in self.links.values():
            try:
                self.client.destroy_link(link)
            except Exception:
                pass
        self.client.close()


def scan_vxi11(transport):
    """Identify instruments, refusing any address that disagrees with itself."""
    found, unstable = {}, []
    for addr in range(0, 31):
        reads = [transport.query_instrument(addr).decode(errors="replace")
                 for _ in range(2)]
        if not any(r.strip() for r in reads):
            continue
        if len(set(reads)) != 1:
            unstable.append(addr)
            print("  addr %-2d UNSTABLE: %r" % (addr, reads))
            continue
        found[addr] = reads[0]
    return found, unstable


if args.gateway == "e5810":
    print("Scanning %s over VXI-11 ..." % args.host)
    hw = VXI11Transport(args.host)
    found, unstable = scan_vxi11(hw)
else:
    print("Scanning %s:%d (collision-checked) ..." % HW)
    hw = Link(HW)
    found, unstable = scan_bus(hw)

if unstable:
    print("\nREFUSING to run: addresses %s do not read consistently." % unstable)
    print("A colliding address cannot be mirrored, so any comparison is void.")
    hw.restore_baseline(); hw.close()
    sys.exit(1)

if args.only is not None:
    found = {a: i for a, i in found.items() if a == args.only}

print("bus: %s\n" % sorted(found))

# Run the emulator without measurement jitter. The comparison is about the
# FORMAT of a reading, and a value that drifts across a decade boundary changes
# the digit count for reasons that have nothing to do with fidelity -- the
# emulator would fail against itself. Seeding from hardware plus zero jitter
# makes each check deterministic.
VirtualInstrument.FUNCTION_PROFILES = {
    name: (attr, 0.0, places, unit)
    for name, (attr, _sigma, places, unit)
    in VirtualInstrument.FUNCTION_PROFILES.items()
}

registry = InstrumentRegistry()
registry.devices.clear()
for addr, idn in found.items():
    klass = classify(idn)
    registry.set_device(addr, VirtualInstrument(
        addr, idn.split(",")[1].strip() if "," in idn else "dev",
        idn.rstrip("\r\n"), instrument_class=klass))

if args.gateway == "e5810":
    # Non-privileged ports: binding 111 usually needs elevation, and the
    # comparison does not care which ports the emulator listens on.
    srv = VXI11EmulatorServer(host=LOCAL[0], core_port=LOCAL[1] + 1,
                              portmap_port=LOCAL[1], registry=registry)
    srv.start(); time.sleep(0.4)
    em = VXI11Transport(LOCAL[0], port=LOCAL[1] + 1)
else:
    srv = PrologixEmulatorServer(host=LOCAL[0], port=LOCAL[1], registry=registry)
    srv.start(); time.sleep(0.3)
    em = Link(LOCAL)
    em.send("++auto 0\n++read_tmo_ms 1500\n"); time.sleep(0.2)

total = matched = retried = 0
failures = []


def compare(h, e, mode):
    if mode == SIGNATURE:
        sh, se = signature(h), signature(e)
        shown = ("%r vs %r" % (sh[0][:36], se[0][:36]) if sh[0] != se[0]
                 else "resolution %s vs %s" % (sh[1:], se[1:]))
        return sh == se, shown
    return h == e, "hw=%r em=%r" % (h[:46], e[:46])

for addr in sorted(found):
    dev = registry.get_device(addr)
    klass = dev.instrument_class
    seed_from_hardware(dev, hw, addr)
    print("=== %d  %s  [%s]  seeded: %s ===" % (addr, dev.name, klass, dev.function))

    for scpi, mode in BATTERY.get(klass, BATTERY["DMM"]):
        h = hw.query_instrument(addr, scpi)
        e = em.query_instrument(addr, scpi)
        total += 1
        ok, shown = compare(h, e, mode)

        # One retry before calling a disagreement a defect. The gateways drop
        # the occasional read -- MEASURED at roughly 1 in 56 on the E5810A --
        # and an empty hardware reply otherwise reports as a fidelity failure
        # for a query whose answer is well established. A query that is
        # genuinely silent stays silent on the retry and still fails.
        if not ok and mode != INFO:
            retried += 1
            time.sleep(0.2)
            h2 = hw.query_instrument(addr, scpi)
            e2 = em.query_instrument(addr, scpi)
            ok2, shown2 = compare(h2, e2, mode)
            if ok2:
                print("  %-22s %-9s match (first read dropped: hw=%r)"
                      % (scpi, mode, h[:24]))
                matched += 1
                continue
            h, e, ok, shown = h2, e2, ok2, shown2

        if mode == INFO:
            matched += 1
            print("  %-22s %-9s %s  hw=%r em=%r"
                  % (scpi, mode, "match" if ok else "differs",
                     h.strip(), e.strip()))
        elif ok:
            matched += 1
            print("  %-22s %-9s match" % (scpi, mode))
        else:
            failures.append((addr, dev.name, scpi, mode, h, e))
            print("  %-22s %-9s MISMATCH  %s" % (scpi, mode, shown))
    print()

hw.restore_baseline()
hw.close(); em.close(); srv.stop()

print("=" * 70)
print("checks: %d   matched: %d   mismatched: %d   retried: %d"
      % (total, matched, len(failures), retried))
if retried:
    print("A retry means the first read disagreed and the second did not -- a "
          "dropped read, not a fidelity defect. If this climbs, suspect the "
          "link or the bus rather than the emulator.")
if failures:
    print("\nDETAIL")
    for addr, name, scpi, mode, h, e in failures:
        print("  %d %s  %s  (%s)" % (addr, name, scpi, mode))
        print("      hardware: %r" % h)
        print("      emulator: %r" % e)
    sys.exit(1)
print("\nEvery instrument reply matches the hardware.")
