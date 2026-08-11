"""
Minimal VXI-11 / ONC-RPC client, written to expose the wire.

PyVISA can talk to an E5810A perfectly well, but it hides exactly the fields an
emulator has to reproduce: the link identifier, the abort channel port, the
negotiated maxRecvSize, the `reason` bits that end a read, and the error code
for every failure path. This client surfaces all of them.

Only what the capture needs is implemented. Nothing here writes gateway
configuration.

References: VXI-11 Rev 1.0 (VMEbus Extensions for Instrumentation, TCP/IP
Instrument Protocol) and RFC 5531 (ONC RPC v2).
"""
import socket
import struct
import time

# --- ONC RPC ---------------------------------------------------------------
RPC_VERSION = 2
MSG_CALL = 0
MSG_REPLY = 1

PORTMAP_PROG, PORTMAP_VERS, PORTMAP_GETPORT = 100000, 2, 3
IPPROTO_TCP_RPC = 6

# --- VXI-11 programs -------------------------------------------------------
CORE_PROG, CORE_VERS = 0x0607AF, 1        # 395183
ABORT_PROG, ABORT_VERS = 0x0607B0, 1      # 395184
INTR_PROG, INTR_VERS = 0x0607B1, 1        # 395185

# --- Core channel procedures ----------------------------------------------
CREATE_LINK = 10
DEVICE_WRITE = 11
DEVICE_READ = 12
DEVICE_READSTB = 13
DEVICE_TRIGGER = 14
DEVICE_CLEAR = 15
DEVICE_REMOTE = 16
DEVICE_LOCAL = 17
DEVICE_LOCK = 18
DEVICE_UNLOCK = 19
DEVICE_ENABLE_SRQ = 20
DEVICE_DOCMD = 22
DESTROY_LINK = 23
CREATE_INTR_CHAN = 25
DESTROY_INTR_CHAN = 26

PROC_NAMES = {
    CREATE_LINK: "create_link", DEVICE_WRITE: "device_write",
    DEVICE_READ: "device_read", DEVICE_READSTB: "device_readstb",
    DEVICE_TRIGGER: "device_trigger", DEVICE_CLEAR: "device_clear",
    DEVICE_REMOTE: "device_remote", DEVICE_LOCAL: "device_local",
    DEVICE_LOCK: "device_lock", DEVICE_UNLOCK: "device_unlock",
    DEVICE_ENABLE_SRQ: "device_enable_srq", DEVICE_DOCMD: "device_docmd",
    DESTROY_LINK: "destroy_link", CREATE_INTR_CHAN: "create_intr_chan",
    DESTROY_INTR_CHAN: "destroy_intr_chan",
}

#: VXI-11 error codes, so a capture reads as behaviour rather than integers.
ERRORS = {
    0: "no error", 1: "syntax error", 3: "device not accessible",
    4: "invalid link identifier", 5: "parameter error",
    6: "channel not established", 8: "operation not supported",
    9: "out of resources", 11: "device locked by another link",
    12: "no lock held by this link", 15: "I/O timeout", 17: "I/O error",
    21: "invalid address", 23: "abort", 29: "channel already established",
}

#: device_write flags.
WRITE_END = 0x08          # assert EOI on the last byte
WRITE_TERMCHRSET = 0x80   # honour termChar

#: device_read `reason` bits, telling the client why the read stopped.
REASON_REQCNT = 0x01
REASON_CHR = 0x02
REASON_END = 0x04

READ_TERMCHRSET = 0x80

#: Device_AddrFamily for create_intr_chan.
DEVICE_TCP, DEVICE_UDP = 0, 1


def reason_text(reason):
    parts = []
    if reason & REASON_REQCNT:
        parts.append("REQCNT")
    if reason & REASON_CHR:
        parts.append("CHR")
    if reason & REASON_END:
        parts.append("END")
    return "|".join(parts) or "none"


def error_text(code):
    return "%d (%s)" % (code, ERRORS.get(code, "undocumented"))


# --- XDR helpers -----------------------------------------------------------
def xdr_string(value: bytes) -> bytes:
    """Length-prefixed, padded to a 4-byte boundary."""
    pad = (-len(value)) % 4
    return struct.pack(">I", len(value)) + value + b"\x00" * pad


def read_xdr_string(buf, off):
    (length,) = struct.unpack(">I", buf[off:off + 4])
    off += 4
    data = buf[off:off + length]
    off += length + ((-length) % 4)
    return data, off


class RPCError(Exception):
    pass


class RPCClient:
    """ONC RPC v2 over TCP, with record marking."""

    def __init__(self, host, port, timeout=10.0):
        self.host, self.port = host, port
        self.sock = socket.socket()
        self.sock.settimeout(timeout)
        self.sock.connect((host, port))
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._xid = int(time.time()) & 0x7FFFFFFF

    def call(self, prog, vers, proc, args=b""):
        self._xid = (self._xid + 1) & 0x7FFFFFFF
        body = struct.pack(">IIIIII", self._xid, MSG_CALL, RPC_VERSION,
                           prog, vers, proc)
        body += struct.pack(">IIII", 0, 0, 0, 0)   # null cred, null verf
        body += args
        self.sock.sendall(struct.pack(">I", 0x80000000 | len(body)) + body)
        return self._recv_reply()

    def _recv_exactly(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RPCError("connection closed mid-reply")
            buf += chunk
        return buf

    def _recv_reply(self):
        payload = b""
        while True:
            (marker,) = struct.unpack(">I", self._recv_exactly(4))
            length = marker & 0x7FFFFFFF
            payload += self._recv_exactly(length)
            if marker & 0x80000000:
                break

        off = 4                                   # xid
        (msg_type,) = struct.unpack(">I", payload[off:off + 4]); off += 4
        if msg_type != MSG_REPLY:
            raise RPCError("not a reply (msg_type=%d)" % msg_type)
        (reply_stat,) = struct.unpack(">I", payload[off:off + 4]); off += 4
        if reply_stat != 0:
            raise RPCError("call rejected (reply_stat=%d)" % reply_stat)
        off += 4                                  # verf flavor
        (vlen,) = struct.unpack(">I", payload[off:off + 4]); off += 4
        off += vlen + ((-vlen) % 4)
        (accept_stat,) = struct.unpack(">I", payload[off:off + 4]); off += 4
        if accept_stat != 0:
            raise RPCError("accept_stat=%d (0=success, 1=prog unavail, "
                           "2=prog mismatch, 3=proc unavail, 4=garbage args)"
                           % accept_stat)
        return payload[off:]

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def getport(host, prog, vers, proto=IPPROTO_TCP_RPC, timeout=5.0):
    """Ask the portmapper which port serves a program. 0 means unregistered."""
    rpc = RPCClient(host, 111, timeout=timeout)
    try:
        args = struct.pack(">IIII", prog, vers, proto, 0)
        result = rpc.call(PORTMAP_PROG, PORTMAP_VERS, PORTMAP_GETPORT, args)
        return struct.unpack(">I", result[:4])[0]
    finally:
        rpc.close()


class VXI11Link:
    """One VXI-11 core-channel link to a single instrument."""

    def __init__(self, client, lid, abort_port, max_recv_size, device):
        self.client, self.lid = client, lid
        self.abort_port, self.max_recv_size = abort_port, max_recv_size
        self.device = device


class VXI11Client:
    """
    Core-channel client.

    Links are held, never opened per query: an E5810A degrades progressively
    when links are churned, and eventually refuses create_link outright.
    """

    def __init__(self, host, port=None, timeout=10.0, client_id=0x42):
        self.host = host
        self.port = port or getport(host, CORE_PROG, CORE_VERS)
        if not self.port:
            raise RPCError("VXI-11 core channel is not registered")
        self.rpc = RPCClient(host, self.port, timeout=timeout)
        self.client_id = client_id

    def create_link(self, device="gpib0", lock=False, lock_timeout=0):
        args = struct.pack(">III", self.client_id, 1 if lock else 0,
                           lock_timeout) + xdr_string(device.encode())
        result = self.rpc.call(CORE_PROG, CORE_VERS, CREATE_LINK, args)
        error, lid, abort_port, max_recv = struct.unpack(">IIII", result[:16])
        if error:
            return None, error
        return VXI11Link(self, lid, abort_port, max_recv, device), 0

    def device_write(self, link, data, io_timeout=10000, lock_timeout=0,
                     flags=WRITE_END):
        args = struct.pack(">IIII", link.lid, io_timeout, lock_timeout, flags)
        args += xdr_string(data)
        result = self.rpc.call(CORE_PROG, CORE_VERS, DEVICE_WRITE, args)
        error, size = struct.unpack(">II", result[:8])
        return error, size

    def device_read(self, link, request_size=4096, io_timeout=10000,
                    lock_timeout=0, flags=0, term_char=0):
        args = struct.pack(">IIIIII", link.lid, request_size, io_timeout,
                           lock_timeout, flags, term_char)
        result = self.rpc.call(CORE_PROG, CORE_VERS, DEVICE_READ, args)
        error, reason = struct.unpack(">II", result[:8])
        data, _ = read_xdr_string(result, 8)
        return error, reason, data

    def device_readstb(self, link, flags=0, lock_timeout=0, io_timeout=10000):
        args = struct.pack(">IIII", link.lid, flags, lock_timeout, io_timeout)
        result = self.rpc.call(CORE_PROG, CORE_VERS, DEVICE_READSTB, args)
        error, stb = struct.unpack(">II", result[:8])
        return error, stb

    def _generic(self, proc, link, flags=0, lock_timeout=0, io_timeout=10000):
        args = struct.pack(">IIII", link.lid, flags, lock_timeout, io_timeout)
        result = self.rpc.call(CORE_PROG, CORE_VERS, proc, args)
        return struct.unpack(">I", result[:4])[0]

    def device_trigger(self, link, **kw):
        return self._generic(DEVICE_TRIGGER, link, **kw)

    def device_clear(self, link, **kw):
        return self._generic(DEVICE_CLEAR, link, **kw)

    def device_remote(self, link, **kw):
        return self._generic(DEVICE_REMOTE, link, **kw)

    def device_local(self, link, **kw):
        return self._generic(DEVICE_LOCAL, link, **kw)

    def device_lock(self, link, flags=0, lock_timeout=1000):
        args = struct.pack(">III", link.lid, flags, lock_timeout)
        result = self.rpc.call(CORE_PROG, CORE_VERS, DEVICE_LOCK, args)
        return struct.unpack(">I", result[:4])[0]

    def device_unlock(self, link):
        args = struct.pack(">I", link.lid)
        result = self.rpc.call(CORE_PROG, CORE_VERS, DEVICE_UNLOCK, args)
        return struct.unpack(">I", result[:4])[0]

    def destroy_link(self, link):
        args = struct.pack(">I", link.lid)
        result = self.rpc.call(CORE_PROG, CORE_VERS, DESTROY_LINK, args)
        return struct.unpack(">I", result[:4])[0]

    def create_intr_chan(self, host_addr, host_port, prog=INTR_PROG,
                         vers=INTR_VERS, family=DEVICE_TCP):
        """
        Ask the gateway to call BACK to an RPC server the client is running.

        This is what makes SRQ work: the roles invert, and the gateway becomes
        the client. `host_addr` is a packed IPv4 address as an integer.
        """
        args = struct.pack(">IIIII", host_addr, host_port, prog, vers, family)
        result = self.rpc.call(CORE_PROG, CORE_VERS, CREATE_INTR_CHAN, args)
        return struct.unpack(">I", result[:4])[0]

    def destroy_intr_chan(self):
        result = self.rpc.call(CORE_PROG, CORE_VERS, DESTROY_INTR_CHAN, b"")
        return struct.unpack(">I", result[:4])[0]

    def device_enable_srq(self, link, enable=True, handle=b""):
        """Arms SRQ notification for one link. `handle` comes back verbatim."""
        args = struct.pack(">II", link.lid, 1 if enable else 0)
        args += xdr_string(handle)
        result = self.rpc.call(CORE_PROG, CORE_VERS, DEVICE_ENABLE_SRQ, args)
        return struct.unpack(">I", result[:4])[0]

    def device_docmd(self, link, cmd, data_in=b"", flags=0, io_timeout=5000,
                     lock_timeout=0, network_order=True, datasize=1):
        """The GPIB escape hatch: bus-level operations such as REN and ATN."""
        args = struct.pack(">IIIIiII", link.lid, flags, io_timeout,
                           lock_timeout, cmd, 1 if network_order else 0,
                           datasize)
        args += xdr_string(data_in)
        result = self.rpc.call(CORE_PROG, CORE_VERS, DEVICE_DOCMD, args)
        error = struct.unpack(">I", result[:4])[0]
        data_out, _ = read_xdr_string(result, 4)
        return error, data_out

    def close(self):
        self.rpc.close()


class VXI11AbortClient:
    """
    The abort channel: a SECOND connection, so a client can cancel a core-channel
    call that is already blocked.

    The core channel is synchronous -- a device_read waiting out a 10 s timeout
    is occupying the connection the client would otherwise use to say "stop".
    Hence a separate program on a separate port, carrying one procedure.
    """

    DEVICE_ABORT = 1

    def __init__(self, host, port, timeout=5.0):
        self.rpc = RPCClient(host, port, timeout=timeout)

    def device_abort(self, lid):
        args = struct.pack(">I", lid)
        result = self.rpc.call(ABORT_PROG, ABORT_VERS, self.DEVICE_ABORT, args)
        return struct.unpack(">I", result[:4])[0]

    def close(self):
        self.rpc.close()
