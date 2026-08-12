"""
Socket Binding Helpers (`netutil.py`)

Creating a TCP listener is not portable in the way it appears to be.

On POSIX, `SO_REUSEADDR` lets a listener rebind a port still held in TIME_WAIT
by a closed connection — useful, and harmless, because a second live bind to the
same address is still refused.

On Windows the same option means something else entirely: it permits two live
sockets to bind the *same* address. A second instance then starts "successfully"
and the operating system delivers connections to whichever socket it chooses.
For an instrument emulator that is a nasty failure mode — a stale process keeps
answering some fraction of the client's traffic, and the symptom looks like
intermittent flakiness in the emulator rather than a port conflict.

Windows spells the semantics we actually want `SO_EXCLUSIVEADDRUSE`: conflicting
binds fail loudly, which the callers already surface to the user.
"""

import socket
import sys
import threading
from typing import Set

_IS_WINDOWS = sys.platform == "win32"


# Safety envelopes, deliberately far above every command and RPC call in the
# measured hardware captures. These are emulator resource limits, not claims
# that physical hardware rejects at the same boundary.
MAX_PENDING_TEXT_CHARS = 64 * 1024
MAX_RPC_RECORD_BYTES = 1024 * 1024
DEFAULT_MAX_CLIENT_HANDLERS = 64


class ClientLimiter:
    '''Tracks accepted sockets and enforces a hard per-server client ceiling.'''

    def __init__(self, limit: int = DEFAULT_MAX_CLIENT_HANDLERS):
        if limit < 1:
            raise ValueError('client limit must be at least 1')
        self.limit = limit
        self._clients: Set[socket.socket] = set()
        self._lock = threading.Lock()

    def admit(self, sock: socket.socket) -> bool:
        '''Reserve a handler slot for *sock*, returning False when full.'''
        with self._lock:
            if len(self._clients) >= self.limit:
                return False
            self._clients.add(sock)
            return True

    def release(self, sock: socket.socket):
        with self._lock:
            self._clients.discard(sock)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def close_all(self):
        '''Close every admitted client so blocked handlers can exit on stop.'''
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for sock in clients:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def create_tcp_listener(host: str, port: int, backlog: int = 128) -> socket.socket:
    """
    Returns a bound, listening TCP socket that refuses to share its address.

    Raises OSError if the address is already in use, on every platform.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if _IS_WINDOWS:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(backlog)
    except Exception:
        sock.close()
        raise
    return sock


def create_multicast_listener(port: int, group: str) -> socket.socket:
    """
    Returns a UDP socket bound for multicast reception.

    Multicast is the one case where address sharing is correct: mDNS responders
    are expected to coexist with Bonjour and other discovery services on 5353,
    so this deliberately keeps SO_REUSEADDR on every platform.
    """
    import struct

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except Exception:
        sock.close()
        raise
    return sock
