"""
High-Performance Low-Latency Prologix Ethernet Adapter Emulator (`prologix_emulator.py`)

TCP socket server (default port 1234) emulating a Prologix Ethernet GPIB controller.
Every socket connection strictly enforces 100% real Prologix hardware socket protocol parity
(CRLF \\r\\n line endings, exact 01.06.06.00 version string, exact 'Unrecognized command' errors,
and strict ++auto 0/1 read buffering).

Supports two Socket Connection Policies:
  1. 'single_connection' (DEFAULT - Physical Hardware Parity): Strictly 1 active client connection (drops prior socket).
  2. 'multi_connection' (Multi-Threaded Multiplexing): Allows parallel client connection threads,
     where EVERY socket connection remains 100% faithful to the real Prologix hardware interface.
"""

import os
import socket
import sys
import threading
import time
from typing import Dict, Any, Callable, Optional
from .device_emulator import InstrumentRegistry, VirtualInstrument
from .diagnostics import ERROR_MEANINGS, INFO, WARN, DiagnosticEmitter
from .netutil import (
    DEFAULT_MAX_CLIENT_HANDLERS, MAX_PENDING_TEXT_CHARS, ClientLimiter,
    create_tcp_listener,
)


#: Response origin. The controller terminates its own replies with CRLF;
#: instrument data is relayed exactly as the instrument produced it.
KIND_CTRL = "ctrl"
KIND_INST = "inst"


class PrologixState:
    """Controller state (Global in single_connection mode, per-session in multi_connection mode)."""

    def __init__(self):
        # Profile: the unit's saved startup primary address reads back as 4.
        self.active_address = 4
        self.secondary_address = 0  # Secondary GPIB address (0 = none)
        self.auto_read = 0  # Hardware default ++auto 0
        self.read_timeout_ms = 200  # Hardware default 200 ms
        self.eoi_enabled = 1
        self.eos_mode = 3  # Hardware default 3 (None on instrument bytes)
        self.eot_enable = 0
        self.eot_char = 0
        self.mode = 1  # 1 = Controller mode
        # Pending output, keyed by GPIB address.
        #
        # On a real bus each instrument holds its own output buffer and ++read
        # fetches from whichever one is addressed at that instant. A single
        # controller-side queue looks equivalent until a client multiplexes
        # several instruments over one socket -- then interleaved
        # '++addr N / query / ++read' sequences hand each device another
        # device's reply.
        self.pending: Dict[int, str] = {}

    # IEEE 488.1 status byte bits used by serial poll.
    STB_MAV = 0x10  # Message Available â€” instrument has data waiting to be read
    STB_RQS = 0x40  # Requesting service

    def queue_response(self, address: int, text: str):
        """
        Places a reply in the instrument's output buffer.

        MEASURED: an instrument holds ONE message. Issuing a new query while an
        earlier reply is unread discards that reply -- the Keithley 2001M
        reports '-410,"Query INTERRUPTED"' afterwards. Queueing messages up
        instead would leave MAV asserted after the client had read everything
        it asked for.
        """
        self.pending[address] = text

    def take_response(self, address: int) -> Optional[str]:
        """Removes and returns the reply held by the instrument at `address`."""
        return self.pending.pop(address, None)

    def put_back(self, address: int, text: str):
        """Returns an unread remainder after a partial ++read <char>."""
        if text:
            self.pending[address] = text

    def has_pending(self, address: int) -> bool:
        return bool(self.pending.get(address))

    def any_pending(self) -> bool:
        return any(self.pending.values())

    def serial_poll_byte(self, address: int) -> int:
        """
        Status byte returned by ++spoll.

        MAV reflects the addressed instrument only, since that is the device
        actually being polled.
        """
        return self.STB_MAV if self.has_pending(address) else 0


class PrologixEmulatorServer(DiagnosticEmitter):
    """Low-latency TCP socket server emulating Prologix Ethernet GPIB Gateway."""

    DIAGNOSTIC_SOURCE = "prologix"

    POLICY_SINGLE = "single_connection"
    POLICY_MULTI = "multi_connection"

    # ------------------------------------------------------------------
    # Escape handling
    #
    # MEASURED on firmware 01.06.06.00; see PROLOGIX_HARDWARE_PROFILE.md
    # section 2b. These characters carry protocol meaning, so a client sending
    # them AS DATA prefixes each with ESC (0x1B):
    #
    #   *ESE <ESC>+37  ->  *ESE? returns 37, SYST:ERR? clean
    #                      (the prefix is stripped before the instrument)
    #   ++ver<ESC><LF>++ver  ->  'Unrecognized command'
    #                      (the escaped LF does NOT terminate; the whole line
    #                       becomes one unrecognisable command token)
    #   ++ver\n++ver         ->  two version strings (unescaped LF still frames)
    # ------------------------------------------------------------------
    ESC = "\x1b"
    ESCAPABLE = "\r\n\x1b+"

    @classmethod
    def find_terminator(cls, buffer: str) -> Optional[int]:
        """
        Index of the first UNESCAPED CR or LF, or None if the line is partial.

        A trailing lone ESC means the escaped byte has not arrived yet, so the
        caller waits for more data rather than framing a truncated command.
        """
        i = 0
        while i < len(buffer):
            if buffer[i] == cls.ESC:
                i += 2          # skip ESC and whatever it protects
                continue
            if buffer[i] in "\r\n":
                return i
            i += 1
        return None

    #: ++help output captured verbatim from the physical controller. Kept as a
    #: data file rather than a literal so it stays byte-exact.
    _help_cache: Optional[str] = None
    _help_warned = False

    @classmethod
    def _help_paths(cls):
        """
        Where the capture might live.

        A frozen build unpacks data files under sys._MEIPASS, and resolving
        only from __file__ is how a bundled app ends up serving an empty
        ++help -- a command we otherwise match byte for byte.
        """
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = [os.path.join(here, "prologix_help.txt")]
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            candidates.append(os.path.join(bundle, "core", "prologix_help.txt"))
            candidates.append(os.path.join(bundle, "prologix_help.txt"))
        return candidates

    @classmethod
    def _help_text(cls) -> str:
        """The recorded ++help listing, minus its trailing CRLF."""
        if cls._help_cache is None:
            raw = ""
            for path in cls._help_paths():
                try:
                    with open(path, "rb") as handle:
                        raw = handle.read().decode("utf-8", errors="replace")
                    break
                except OSError:
                    continue
            if not raw and not cls._help_warned:
                # Loud, once. Silently serving an empty ++help would be an
                # invisible fidelity regression in a packaged build.
                cls._help_warned = True
                print("[PrologixEmulatorServer] WARNING: prologix_help.txt not "
                      "found in %s -- ++help will not match hardware."
                      % ", ".join(cls._help_paths()))
            # The sender appends CRLF to every response line, so drop the
            # trailing terminator the capture already carries.
            cls._help_cache = raw[:-2] if raw.endswith("\r\n") else raw
        return cls._help_cache

    @classmethod
    def unescape(cls, text: str) -> str:
        """Removes ESC prefixes, yielding the bytes destined for the instrument."""
        out = []
        i = 0
        while i < len(text):
            if text[i] == cls.ESC and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
            else:
                out.append(text[i])
                i += 1
        return "".join(out)


    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 1234,
        registry: Optional[InstrumentRegistry] = None,
        connection_policy: str = POLICY_SINGLE,
        max_client_handlers: int = DEFAULT_MAX_CLIENT_HANDLERS,
    ):
        self.host = host
        self.port = port
        self.registry = registry or InstrumentRegistry()
        self.connection_policy = connection_policy  # POLICY_SINGLE or POLICY_MULTI

        self._server_socket: Optional[socket.socket] = None
        self._is_running = False
        self._server_thread: Optional[threading.Thread] = None
        self._client_limiter = ClientLimiter(max_client_handlers)

        # Hardware global state
        self.global_state = PrologixState()
        self.active_client_sock: Optional[socket.socket] = None
        self.active_client_id: Optional[str] = None
        self._socket_lock = threading.Lock()

        # Impairment simulation
        self.synthetic_delay_ms = 0.0
        self.simulated_drop_rate = 0.0

        # Callbacks for snoop logging
        self.packet_callbacks: list = []
        self.active_clients: Dict[str, str] = {}

    def set_connection_policy(self, policy: str):
        """Switches connection policy: 'single_connection' (hardware single client) vs 'multi_connection' (multi-threaded)."""
        if policy in (self.POLICY_SINGLE, self.POLICY_MULTI):
            self.connection_policy = policy

    def add_packet_callback(self, callback: Callable[[Dict[str, Any]], None]):
        if callback not in self.packet_callbacks:
            self.packet_callbacks.append(callback)

    def add_warning_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Registers a listener for protocol misuse by the connected client only.

        A narrower view of the diagnostic channel, kept because a caller that
        only cares about client errors should not have to filter lifecycle
        chatter out for itself.
        """
        def only_warnings(record):
            if record.get("level") == WARN and record.get("code") is not None:
                callback(record)

        self.add_diagnostic_callback(only_warnings)

    def _raise_instrument_error(self, address: int, code: int):
        """Logs a SCPI error against the addressed instrument and reports it."""
        device = self.registry.get_device(address) if self.registry else None
        if device is None:
            return
        device.raise_error(code)
        text = VirtualInstrument.SCPI_ERRORS.get(code, ("Unknown error", 0))[0]
        self.diagnose(
            WARN, f'{code},"{text}"', ERROR_MEANINGS.get(code, ""),
            address=address, code=code, device=device.name,
            # Keys the narrower warning listeners were built against.
            extra={"entry": f'{code},"{text}"', "text": text})

    def _notify_packet(self, client_addr: str, direction: str, raw_bytes: bytes, address: int, latency_ms: float = 0.0):
        text = raw_bytes.decode("utf-8", errors="replace").strip()
        event_data = {
            "timestamp": time.strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}",
            "client": client_addr,
            "direction": direction,
            "address": address,
            "raw_bytes": raw_bytes,
            "text": text,
            "latency_ms": round(latency_ms, 2),
            "policy": self.connection_policy,
        }
        for cb in self.packet_callbacks:
            try:
                cb(event_data)
            except Exception:
                pass

    def start(self):
        if self._is_running:
            return

        # Refuses to share the port, so a stale instance cannot silently
        # steal half the client's traffic (see netutil for why).
        self._server_socket = create_tcp_listener(self.host, self.port, backlog=128)
        self._is_running = True

        self._server_thread = threading.Thread(target=self._accept_loop, daemon=True, name="PrologixEmulatorServer")
        self._server_thread.start()
        print(f"[PrologixEmulatorServer] Listening on {self.host}:{self.port}")

    def stop(self):
        self._is_running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None

        self._client_limiter.close_all()

        with self._socket_lock:
            if self.active_client_sock:
                try:
                    self.active_client_sock.close()
                except Exception:
                    pass
                self.active_client_sock = None
                self.active_client_id = None

        print("[PrologixEmulatorServer] Stopped")

    def _accept_loop(self):
        while self._is_running and self._server_socket:
            try:
                client_sock, addr = self._server_socket.accept()
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client_id = f"{addr[0]}:{addr[1]}"

                if not self._client_limiter.admit(client_sock):
                    self.diagnose(
                        WARN, 'client connection refused by safety limit',
                        'already handling %d clients; limit is %d'
                        % (self._client_limiter.active_count,
                           self._client_limiter.limit))
                    client_sock.close()
                    continue

                with self._socket_lock:
                    if self.connection_policy == self.POLICY_SINGLE:
                        # Physical hardware drops previous socket when a new connection arrives
                        if self.active_client_sock:
                            try:
                                self.active_client_sock.close()
                            except Exception:
                                pass
                        self.active_client_sock = client_sock
                        self.active_client_id = client_id

                    self.active_clients[client_id] = f"{addr[0]}:{addr[1]}"

                self.diagnose(INFO, "client connected",
                              f"{addr[0]}:{addr[1]}")

                client_thread = threading.Thread(
                    target=self._handle_limited_client,
                    args=(client_sock, client_id),
                    daemon=True,
                    name=f"ClientThread-{client_id}",
                )
                try:
                    client_thread.start()
                except Exception:
                    self._client_limiter.release(client_sock)
                    client_sock.close()
                    with self._socket_lock:
                        self.active_clients.pop(client_id, None)
                        if self.active_client_sock is client_sock:
                            self.active_client_sock = None
                            self.active_client_id = None
                    raise
            except Exception:
                if not self._is_running:
                    break

    def _handle_limited_client(self, client_sock: socket.socket, client_id: str):
        try:
            self._handle_client(client_sock, client_id)
        finally:
            self._client_limiter.release(client_sock)

    def _handle_client(self, client_sock: socket.socket, client_id: str):
        # Every socket connection uses strict real Prologix hardware state behavior
        state = self.global_state if self.connection_policy == self.POLICY_SINGLE else PrologixState()
        buffer = ""

        try:
            while self._is_running:
                data = client_sock.recv(4096)
                if not data:
                    break

                t_start = time.perf_counter()
                buffer += data.decode("utf-8", errors="replace")

                while True:
                    delimiter_idx = self.find_terminator(buffer)
                    if delimiter_idx is None:
                        if len(buffer) > MAX_PENDING_TEXT_CHARS:
                            self.diagnose(
                                WARN, 'client command exceeded safety limit',
                                'more than %d characters without a terminator; '
                                'connection closed' % MAX_PENDING_TEXT_CHARS)
                            return
                        break

                    if delimiter_idx > MAX_PENDING_TEXT_CHARS:
                        self.diagnose(
                            WARN, 'client command exceeded safety limit',
                            'more than %d characters before a terminator; '
                            'connection closed' % MAX_PENDING_TEXT_CHARS)
                        return

                    raw_line = buffer[:delimiter_idx]
                    buffer = buffer[delimiter_idx + 1:]

                    # ESC-prefixed bytes are data, not protocol.
                    line = self.unescape(raw_line).strip()

                    if not line:
                        continue

                    self._notify_packet(client_id, "IN", line.encode(), state.active_address)

                    # Process command line
                    responses = self._process_command_line(state, line)

                    if self.synthetic_delay_ms > 0:
                        time.sleep(self.synthetic_delay_ms / 1000.0)

                    # EVERY socket connection ALWAYS outputs CRLF (\r\n) matching physical hardware
                    for resp in responses:
                        if resp is None:
                            continue

                        # MEASURED: the controller terminates its OWN replies
                        # with CRLF, but passes instrument data through byte for
                        # byte -- the trailing LF in '...A10  /A02  \n' is the
                        # instrument's, not the controller's. When ++eot_enable
                        # is 1 the EOT char is appended after that data.
                        payload, kind = resp
                        if kind == KIND_CTRL:
                            out_str = payload if payload.endswith("\r\n") else payload + "\r\n"
                        else:
                            out_str = payload
                            if state.eot_enable:
                                out_str += chr(state.eot_char)
                        out_bytes = out_str.encode("utf-8")

                        t_elapsed = (time.perf_counter() - t_start) * 1000.0
                        client_sock.sendall(out_bytes)
                        self._notify_packet(client_id, "OUT", out_bytes, state.active_address, latency_ms=t_elapsed)

        except Exception:
            pass
        finally:
            try:
                client_sock.close()
            except Exception:
                pass
            self.diagnose(INFO, "client disconnected", str(client_id))
            with self._socket_lock:
                if client_id in self.active_clients:
                    del self.active_clients[client_id]
                if self.active_client_id == client_id:
                    self.active_client_sock = None
                    self.active_client_id = None

    #: Rejection messages. The Prologix answers the same string whether the
    #: command is unknown or its argument is out of range -- MEASURED, profile
    #: section 2d. They are separate constants because other firmware does not
    #: conflate them: the AR488 has a distinct "Invalid parameter", so it only
    #: has to override ERR_BAD_ARGUMENT rather than duplicate the parser.
    ERR_UNRECOGNIZED = "Unrecognized command"
    ERR_BAD_ARGUMENT = "Unrecognized command"

    # Argument ranges enforced by the firmware. Anything outside these answers
    # 'Unrecognized command' -- MEASURED, see profile section 2d.
    ARG_RANGES = {
        "addr": (0, 30),
        "auto": (0, 1),
        "mode": (0, 1),
        "read_tmo_ms": (1, 3000),
        "eos": (0, 3),
        "eoi": (0, 1),
        "eot_enable": (0, 1),
        "eot_char": (0, 255),
        "savecfg": (0, 1),
        "spoll": (0, 30),
    }

    #: Secondary GPIB addresses accepted after a primary, per ++help.
    SECONDARY_RANGE = (96, 126)

    def _process_command_line(self, state: PrologixState, line: str) -> list:
        """
        Returns a list of (payload, kind) tuples.

        Fidelity notes, all MEASURED on firmware 01.06.06.00:
          * command names are case-sensitive -- '++AUTO' is not '++auto'
          * an out-of-range argument answers 'Unrecognized command'
          * instrument data is relayed verbatim, controller replies get CRLF
        """
        responses = []
        cmd = line.strip()

        def ctrl(text):
            responses.append((text, KIND_CTRL))

        def unrecognized():
            """The command itself was not understood."""
            ctrl(self.ERR_UNRECOGNIZED)

        def bad_argument():
            """The command was understood; its argument was not acceptable."""
            ctrl(self.ERR_BAD_ARGUMENT)

        if not cmd.startswith("++"):
            # A query arriving while an earlier reply is still unread discards
            # that reply, and the instrument logs -410 "Query INTERRUPTED".
            # This is one of the two conditions a client's unsynchronised
            # write/read pair provokes, so surface it rather than dropping the
            # reply silently.
            if state.has_pending(state.active_address):
                self._raise_instrument_error(
                    state.active_address, VirtualInstrument.ERR_QUERY_INTERRUPTED)

            # SCPI pass-through to the addressed instrument.
            resp = self.registry.process_command(state.active_address, cmd)
            if resp is not None:
                # The instrument supplies its own terminator.
                payload = resp if resp.endswith("\n") else resp + "\n"
                if state.auto_read == 1:
                    responses.append((payload, KIND_INST))
                else:
                    state.queue_response(state.active_address, payload)
            return responses

        # The controller splits on SPACE only; an embedded CR/LF leaves the
        # token unrecognisable rather than acting as a separator.
        body = cmd[2:]
        if " " in body:
            p_cmd, p_arg = body.split(" ", 1)
            p_arg = p_arg.strip()
        else:
            p_cmd, p_arg = body, ""

        # MEASURED: '++AUTO' and '++Addr' are both rejected. No case folding.
        if p_cmd != p_cmd.lower() or not p_cmd:
            unrecognized()
            return responses

        def parse_ints(text):
            try:
                return [int(t) for t in text.split()]
            except ValueError:
                return None

        def in_range(name, value):
            lo, hi = self.ARG_RANGES[name]
            return lo <= value <= hi

        # ---- settings with a validated numeric argument ----------------
        SIMPLE = {
            "auto": "auto_read", "mode": "mode", "read_tmo_ms": "read_timeout_ms",
            "eos": "eos_mode", "eoi": "eoi_enabled", "eot_enable": "eot_enable",
            "eot_char": "eot_char",
        }
        if p_cmd in SIMPLE:
            attr = SIMPLE[p_cmd]
            if not p_arg:
                ctrl(str(getattr(state, attr)))
                return responses
            values = parse_ints(p_arg)
            if values is None or len(values) != 1 or not in_range(p_cmd, values[0]):
                bad_argument()
                return responses
            setattr(state, attr, values[0])
            return responses

        if p_cmd == "addr":
            if not p_arg:
                if state.secondary_address:
                    ctrl("%d %d" % (state.active_address, state.secondary_address))
                else:
                    ctrl(str(state.active_address))
                return responses
            values = parse_ints(p_arg)
            if values is None or not 1 <= len(values) <= 2:
                bad_argument()
                return responses
            if not in_range("addr", values[0]):
                bad_argument()
                return responses
            if len(values) == 2:
                lo, hi = self.SECONDARY_RANGE
                if not lo <= values[1] <= hi:
                    bad_argument()
                    return responses
                state.secondary_address = values[1]
            else:
                state.secondary_address = 0
            state.active_address = values[0]
            return responses

        if p_cmd == "savecfg":
            if not p_arg:
                ctrl("1")
                return responses
            values = parse_ints(p_arg)
            if values is None or len(values) != 1 or not in_range("savecfg", values[0]):
                bad_argument()
            return responses

        if p_cmd == "ver":
            ctrl("Prologix GPIB-ETHERNET Controller version 01.06.06.00")
            return responses

        if p_cmd == "help":
            text = self._help_text()
            if text:
                ctrl(text)
            return responses

        if p_cmd in ("read", "read_eoi"):
            held = state.take_response(state.active_address)
            if held is None:
                # Read timeout: nothing goes on the wire, which is correct. But
                # the read still addressed the instrument to talk with nothing
                # to say, and real hardware logs -420 "Query UNTERMINATED" for
                # it. Silence alone tells a developer nothing; this is the
                # single most useful signal the emulator can give them.
                self._raise_instrument_error(
                    state.active_address,
                    VirtualInstrument.ERR_QUERY_UNTERMINATED)
                return responses
            # '++read <char>' stops at that character, inclusive; the rest
            # stays in the instrument's output buffer.
            if p_arg and p_arg != "eoi":
                values = parse_ints(p_arg)
                if values is None or len(values) != 1 or not 0 <= values[0] <= 255:
                    bad_argument()
                    state.put_back(state.active_address, held)
                    return responses
                stop = chr(values[0])
                idx = held.find(stop)
                if idx != -1 and idx + 1 < len(held):
                    remainder = held[idx + 1:]
                    held = held[:idx + 1]
                    state.put_back(state.active_address, remainder)
            responses.append((held, KIND_INST))
            return responses

        if p_cmd == "srq":
            ctrl("1" if state.any_pending() else "0")
            return responses

        if p_cmd == "spoll":
            target = state.active_address
            if p_arg:
                values = parse_ints(p_arg)
                if values is None or not 1 <= len(values) <= 2:
                    bad_argument()
                    return responses
                if not in_range("spoll", values[0]):
                    bad_argument()
                    return responses
                target = values[0]
            device = self.registry.get_device(target)
            if device is not None:
                base = getattr(device, "status_byte_base", 4)
                ctrl(str(base | state.serial_poll_byte(target)))
            return responses

        if p_cmd in ("rst", "clr", "loc", "llo", "trg", "ifc"):
            if p_arg:
                bad_argument()
            return responses

        unrecognized()
        return responses
