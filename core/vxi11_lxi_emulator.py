"""
LXI Raw SCPI Socket and mDNS LXI Discovery Responder (`vxi11_lxi_emulator.py`)

Emulates Keysight E5810A / LXI-compliant instrument discovery & raw socket communication ports:
  1. Port 5025: LXI SCPI Raw Socket Server (direct SCPI streams without ++ syntax).
  2. UDP Port 5353: LXI mDNS UDP Discovery Responder.
"""

import socket
import threading
from typing import Optional
from .device_emulator import InstrumentRegistry
from .netutil import create_tcp_listener, create_multicast_listener


class LXIDiscoveryResponder:
    """LXI mDNS / UDP Discovery Responder broadcasting LXI services on UDP port 5353."""

    MDNS_PORT = 5353
    MDNS_GROUP = "224.0.0.251"

    def __init__(self, host_name: str = "tc-lxi-emulator", model_name: str = "Keysight 34461A"):
        self.host_name = host_name
        self.model_name = model_name
        self._is_running = False
        self._udp_sock: Optional[socket.socket] = None

    def start(self):
        if self._is_running:
            return
        try:
            # Multicast deliberately keeps address sharing: mDNS responders
            # are expected to coexist with Bonjour and friends on 5353.
            self._udp_sock = create_multicast_listener(self.MDNS_PORT, self.MDNS_GROUP)
            self._is_running = True

            t = threading.Thread(target=self._listen_loop, daemon=True, name="LXIDiscoveryResponder")
            t.start()
            print(f"[LXIDiscoveryResponder] LXI mDNS Discovery Active on UDP Port {self.MDNS_PORT}")
        except Exception as e:
            print(f"[LXIDiscoveryResponder] mDNS bind notice: {e} (Standard on restricted permission environments)")

    def stop(self):
        self._is_running = False
        if self._udp_sock:
            try:
                self._udp_sock.close()
            except Exception:
                pass
            self._udp_sock = None

    def _listen_loop(self):
        while self._is_running and self._udp_sock:
            try:
                data, addr = self._udp_sock.recvfrom(2048)
                if not data:
                    break
                # Respond to LXI query packets containing _lxi or _scpi-raw
                if b"_lxi" in data or b"_scpi-raw" in data or b"_vxi-11" in data:
                    resp = f"LXI_EMULATOR;MODEL={self.model_name};MAC=00:30:D3:07:A4:C6;PORT=5025\n".encode("utf-8")
                    self._udp_sock.sendto(resp, addr)
            except Exception:
                if not self._is_running:
                    break


class LXIRawSocketServer:
    """LXI SCPI Raw Socket Server listening on TCP port 5025 (standard LXI port)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5025, registry: Optional[InstrumentRegistry] = None):
        self.host = host
        self.port = port
        self.registry = registry or InstrumentRegistry()

        self._server_socket: Optional[socket.socket] = None
        self._is_running = False
        self._server_thread: Optional[threading.Thread] = None
        # Appended and removed by per-client threads, and read by the GUI for
        # its client-count tile. PrologixEmulatorServer guards its equivalent
        # with a lock; this one was missed.
        self.active_clients = []
        self._clients_lock = threading.Lock()
        self.default_address = 6  # Default instrument slot for direct SCPI socket

    def start(self):
        if self._is_running:
            return

        self._server_socket = create_tcp_listener(self.host, self.port, backlog=32)
        self._is_running = True

        self._server_thread = threading.Thread(target=self._accept_loop, daemon=True, name="LXIRawSocketServer")
        self._server_thread.start()
        print(f"[LXIRawSocketServer] Listening on {self.host}:{self.port} (Raw SCPI Port)")

    def stop(self):
        self._is_running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception:
                pass
            self._server_socket = None

    def _accept_loop(self):
        while self._is_running and self._server_socket:
            try:
                client_sock, addr = self._server_socket.accept()
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock, f"{addr[0]}:{addr[1]}"),
                    daemon=True,
                )
                t.start()
            except Exception:
                if not self._is_running:
                    break

    def _handle_client(self, client_sock: socket.socket, client_id: str):
        with self._clients_lock:
            self.active_clients.append(client_id)
        buffer = ""
        try:
            while self._is_running:
                data = client_sock.recv(4096)
                if not data:
                    break

                buffer += data.decode("utf-8", errors="replace")
                while "\n" in buffer or "\r" in buffer:
                    delimiter_idx = min(
                        [idx for idx in (buffer.find("\n"), buffer.find("\r")) if idx != -1]
                    )
                    line = buffer[:delimiter_idx].strip()
                    buffer = buffer[delimiter_idx + 1 :]

                    if not line:
                        continue

                    # Determine target GPIB slot: default_address if mapped, or first available slot in registry
                    target_slot = self.default_address
                    if target_slot not in self.registry.devices and self.registry.devices:
                        target_slot = sorted(self.registry.devices.keys())[0]

                    # Directly route SCPI command to virtual instrument at target slot
                    resp = self.registry.process_command(target_slot, line)
                    if resp is not None:
                        out_bytes = (resp + "\r\n").encode("utf-8")
                        client_sock.sendall(out_bytes)

        except Exception:
            pass
        finally:
            with self._clients_lock:
                if client_id in self.active_clients:
                    self.active_clients.remove(client_id)
            try:
                client_sock.close()
            except Exception:
                pass

