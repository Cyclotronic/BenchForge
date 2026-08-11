"""
Shared plumbing for the hardware verification tools.

Everything here was learned the hard way against a physical Prologix
GPIB-ETHERNET adapter. The comments say which lessons, because each one cost a
debugging session and none of them is obvious from the protocol documentation.
"""
import socket
import time

#: Controller settings this project treats as the baseline, from
#: profiles/PROLOGIX_HARDWARE_PROFILE.md. ++savecfg is 1 on the reference unit,
#: so anything a tool changes persists to EEPROM and must be put back.
BASELINE = {
    "addr": 4, "auto": 0, "read_tmo_ms": 200,
    "eos": 3, "eoi": 1, "eot_enable": 0, "eot_char": 0,
}


class Link:
    """
    One persistent connection to a gateway.

    A Prologix serves a single client and drops the previous socket when a new
    one arrives. An earlier tool opened a fresh connection per test case, which
    meant dozens of connect/displace cycles in a few seconds. Hold one
    connection and drain between cases instead.
    """

    def __init__(self, target, timeout=1.5, connect_timeout=8.0, retries=3):
        self.target = target
        self.sock = None
        # The adapter can refuse or stall a connect for a few seconds after a
        # previous client disconnects, so the connect gets a longer budget than
        # ordinary reads and a couple of retries.
        last = None
        for attempt in range(retries):
            try:
                sock = socket.socket()
                sock.settimeout(connect_timeout)
                sock.connect(target)
                self.sock = sock
                break
            except OSError as exc:
                last = exc
                try:
                    sock.close()
                except Exception:
                    pass
                time.sleep(1.5 * (attempt + 1))
        if self.sock is None:
            raise OSError("could not connect to %s:%d after %d attempts: %s"
                          % (target[0], target[1], retries, last))
        self.sock.settimeout(timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._timeout = timeout

    def drain(self, wait=0.25):
        """
        Empty the socket.

        Without this, a slow reply from the previous exchange is still in
        flight and gets attributed to the next one -- which silently mirrors a
        bus that does not exist.
        """
        self.sock.settimeout(wait)
        try:
            while self.sock.recv(8192):
                pass
        except Exception:
            pass
        self.sock.settimeout(self._timeout)

    def send(self, payload):
        self.sock.sendall(payload if isinstance(payload, bytes)
                          else payload.encode())

    def talk(self, payload, wait=0.45, settle=0.12, limit=8192):
        """Send one payload and collect the whole reply, terminator included."""
        self.drain()
        self.send(payload if isinstance(payload, bytes)
                  else payload.encode())
        self.send(b"\n")
        time.sleep(wait)
        chunks = []
        while True:
            try:
                d = self.sock.recv(limit)
            except socket.timeout:
                break
            if not d:
                break
            chunks.append(d)
            if len(b"".join(chunks)) > 4000:
                break
            time.sleep(settle)
        return b"".join(chunks)

    def mav(self, addr, timeout=1.0, interval=0.12):
        """
        Wait for the addressed instrument to raise MAV (0x10) in its status
        byte. Returns True if a reply is waiting, False if it never appears.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.drain(0.05)
            self.send("++spoll %d\n" % addr)
            time.sleep(interval)
            try:
                reply = self.sock.recv(256).decode(errors="replace").strip()
            except socket.timeout:
                continue
            try:
                if int(reply.lstrip("+")) & 0x10:
                    return True
            except ValueError:
                continue
        return False

    def query_instrument(self, addr, scpi="*IDN?", settle=0.9, gated=True):
        """
        Address an instrument, send a query, and read the raw reply.

        `gated` asks the status byte whether a reply is actually waiting before
        issuing ++read. This matters: an ungated read on an instrument that has
        nothing to say addresses it to talk anyway, which the instrument logs as
        -420 "Query UNTERMINATED". Probing a node the instrument does not have
        therefore costs TWO error-queue slots, one for the -113 and one for our
        own read. Doing that across a full battery overflowed the queues on four
        of the seven instruments on this bench and destroyed whatever real
        errors were in them.

        Gating also avoids -410 "Query INTERRUPTED": we never leave an
        instrument addressed to talk with a reply we then abandon.
        """
        self.drain()
        self.send("++addr %d\n" % addr); time.sleep(0.06)
        self.send(scpi + "\n"); time.sleep(0.10)
        if gated and not self.mav(addr):
            return b""
        self.send(b"++read eoi\n"); time.sleep(settle)
        try:
            return self.sock.recv(8192)
        except socket.timeout:
            return b""

    def restore_baseline(self):
        """Put the controller's persisted settings back as we found them."""
        for key, value in BASELINE.items():
            self.send("++%s %s\n" % (key, value))
            time.sleep(0.08)
        self.drain()

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def wire_or(a, b):
    """
    True when a and b differ only by bits being SET.

    GPIB data lines are open-collector and active-low: a device asserts a line
    by pulling it down, so two instruments addressed to talk at once put the
    bitwise OR of their bytes on the wire. The result is still plausible ASCII,
    which is what makes it dangerous -- it reads as a real identity.
    """
    if a == b:
        return False
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    return all((ord(x) & ~ord(y)) == 0 for x, y in zip(lo, hi))


def scan_bus(link, reads=3, verbose=True):
    """
    Identify every instrument on the bus, refusing any address that will not
    answer the same way twice.

    Returns (found, unstable): a dict of address -> raw *IDN? text, and a list
    of addresses that disagreed with themselves.
    """
    link.send("++auto 0\n++read_tmo_ms 1500\n"); time.sleep(0.2)
    found, unstable = {}, []

    for addr in range(0, 31):
        samples = [link.query_instrument(addr).decode(errors="replace")
                   for _ in range(reads)]
        if not any(s.strip() for s in samples):
            continue

        if len(set(samples)) != 1:
            pairs = [(x, y) for i, x in enumerate(samples)
                     for y in samples[i + 1:]]
            collided = any(wire_or(x, y) for x, y in pairs if x and y)
            unstable.append(addr)
            if verbose:
                print("  addr %-2d UNSTABLE -- %s" % (
                    addr, "two talkers at this address (wire-OR collision)"
                    if collided else "inconsistent reads"))
                for s in samples:
                    print("          %r" % s[:66])
            continue

        found[addr] = samples[0]

    return found, unstable
