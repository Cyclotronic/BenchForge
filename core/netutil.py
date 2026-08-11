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

_IS_WINDOWS = sys.platform == "win32"


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
