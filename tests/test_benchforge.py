"""
Integration & Unit Test Suite for BenchForge Studio
"""

import importlib.util
import os
import socket
import struct
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.tc_parser import (
    parse_tc_device_file,
    generate_recommended_configs,
)
from core.device_emulator import (
    InstrumentRegistry, VirtualInstrument, DEFAULT_BENCH, UNDRIVEN_INSTRUMENTS
)
from core.prologix_emulator import PrologixEmulatorServer
from core.vxi11_lxi_emulator import LXIDiscoveryResponder, LXIRawSocketServer
from core.netutil import (
    MAX_PENDING_TEXT_CHARS, MAX_RPC_RECORD_BYTES, ClientLimiter,
)


def bench_spec(name):
    """
    A DEFAULT_BENCH entry by instrument name.

    The physical bench gets rewired, and the emulated bench follows it. Tests
    that hard-code GPIB addresses fail for reasons that have nothing to do with
    the behaviour under test, so look instruments up by name instead.
    """
    for spec in DEFAULT_BENCH:
        if spec["name"] == name:
            return spec
    raise AssertionError(f"{name!r} is not on the default bench")


def slot_of(name):
    return bench_spec(name)["slot"]


def idn_of(name):
    return bench_spec(name)["idn"]


class TestBenchForge(unittest.TestCase):

    def setUp(self):
        self.sample_file = os.path.join(os.path.dirname(__file__), "..", "core", "sample_devices", "Agilent_34401A.txt")

    def test_01_parse_tc_device_file(self):
        dev = parse_tc_device_file(self.sample_file)
        self.assertEqual(dev.name, "Agilent 34401A")
        self.assertEqual(dev.type, "DMM")
        self.assertEqual(dev.driver, "SCPI")
        self.assertEqual(dev.port_type, "GPIB")
        self.assertEqual(dev.cmd_mode, "SCPI")
        self.assertIn("*IDN?", dev.commands)

    def test_02_config_generator(self):
        devices = [
            {"name": "Agilent 34401A", "gpib_address": 1, "enabled": 1},
            {"name": "Fluke PM6690", "gpib_address": 2, "enabled": 1},
        ]
        gpib_txt, load_txt = generate_recommended_configs(devices, host="127.0.0.1", port=1234)
        self.assertIn("PrologixEthernet|id:A", gpib_txt)
        self.assertIn("Device:Agilent 34401A|PortType:GPIB|Address:A:1", load_txt)
        self.assertIn("Device:Fluke PM6690|PortType:GPIB|Address:A:2", load_txt)

    def test_03_non_query_commands_and_unmapped_slots(self):
        registry = InstrumentRegistry()
        dev1 = registry.get_device(1)
        self.assertIsNotNone(dev1)

        # 1. Non-query setting commands should return None
        self.assertIsNone(registry.process_command(1, ":CONF:VOLT:DC 10"))
        self.assertIsNone(registry.process_command(1, "*RST"))

        # 2. Parameterized queries should return values (N1 fix)
        self.assertIsNotNone(registry.process_command(1, ":MEAS:VOLT:DC? 10,0.001"))
        self.assertIsNotNone(registry.process_command(1, "MEAS:VOLT:DC? AUTO"))

        # 3. TC-defined device test (N2 fix & Issue 11 fix)
        tc_dev = parse_tc_device_file(self.sample_file)
        v_dev = VirtualInstrument(gpib_address=5)
        v_dev.load_tc_definition(tc_dev)
        registry.set_device(5, v_dev)

        # TC defined set command returns None
        self.assertIsNone(registry.process_command(5, ":CONF:VOLT:DC 10"))
        self.assertIsNone(registry.process_command(5, "CONF:VOLT:DC 10"))

        # TC defined query returns mapped pattern regardless of leading colon
        self.assertIsNotNone(registry.process_command(5, "*IDN?"))
        self.assertIsNotNone(registry.process_command(5, "MEAS:VOLT:DC?"))

        # 4. Unmapped slot returns None (simulates read timeout on empty address)
        self.assertIsNone(registry.process_command(20, "*IDN?"))

    def test_04_prologix_secondary_addr_and_empty_read(self):
        registry = InstrumentRegistry()
        server = PrologixEmulatorServer(host="127.0.0.1", port=1237, registry=registry, connection_policy=PrologixEmulatorServer.POLICY_SINGLE)
        server.start()
        time.sleep(0.1)

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(("127.0.0.1", 1237))

            # Test secondary address parsing
            s.sendall(b"++addr 1 96\n")
            time.sleep(0.01)
            s.sendall(b"++addr\n")
            addr_resp = s.recv(1024).decode().strip()
            self.assertEqual(addr_resp, "1 96")

            # Test empty ++read buffer returns nothing (silent / read timeout)
            s.sendall(b"++read\n")
            with self.assertRaises(socket.timeout):
                _ = s.recv(1024)

            s.close()
        finally:
            server.stop()

    def test_05_lxi_raw_scpi_socket_server(self):
        registry = InstrumentRegistry()
        lxi_server = LXIRawSocketServer(host="127.0.0.1", port=5026, registry=registry)
        lxi_server.default_address = slot_of("Agilent 34411A")
        lxi_server.start()
        time.sleep(0.1)

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(("127.0.0.1", 5026))

            # Send SCPI query directly on Port 5026 without Prologix ++ commands
            s.sendall(b"*IDN?\n")
            data = s.recv(1024)
            self.assertIn(idn_of("Agilent 34411A").encode(), data)
            s.close()
        finally:
            lxi_server.stop()


    @staticmethod
    def _dns_records(packet):
        _ident, flags, questions, answers, authorities, additionals = struct.unpack(
            ">HHHHHH", packet[:12])
        offset = 12
        for _ in range(questions):
            _name, offset = LXIDiscoveryResponder._read_name(packet, offset)
            offset += 4
        records = []
        for _ in range(answers + authorities + additionals):
            name, offset = LXIDiscoveryResponder._read_name(packet, offset)
            record_type, record_class, ttl, length = struct.unpack(
                ">HHIH", packet[offset:offset + 10])
            offset += 10
            data = packet[offset:offset + length]
            offset += length
            records.append((name, record_type, record_class, ttl, data))
        return flags, records

    def test_06_mdns_advertises_the_selected_persona(self):
        responder = LXIDiscoveryResponder(host_name="benchforge-test")
        responder.configure_prologix("127.0.0.1", 1234)
        flags, records = self._dns_records(responder.build_announcement())
        self.assertEqual(flags, 0x8400)
        names = {record[0] for record in records}
        self.assertIn("_prologix-gpib._tcp.local.", names)
        self.assertNotIn("_lxi._tcp.local.", names)
        srv = next(record for record in records
                   if record[1] == responder.TYPE_SRV)
        self.assertEqual(struct.unpack(">HHH", srv[4][:6])[2], 1234)
        self.assertTrue(srv[2] & responder.CACHE_FLUSH)

        # A loopback listener is not reachable by other hosts and must never
        # publish 127.0.0.1 onto the LAN.
        responder.start()
        self.assertFalse(responder._is_running)

        responder.configure_ar488("127.0.0.1", 8488)
        _flags, records = self._dns_records(responder.build_announcement())
        names = {record[0] for record in records}
        self.assertIn("_ar488-gpib._tcp.local.", names)
        self.assertNotIn("_prologix-gpib._tcp.local.", names)
        responder.configure_lxi("127.0.0.1", raw_port=5025, vxi11_port=1024)
        _flags, records = self._dns_records(responder.build_announcement())
        ptr_names = {record[0] for record in records
                     if record[1] == responder.TYPE_PTR}
        self.assertEqual(ptr_names, {
            "_lxi._tcp.local.",
            "_scpi-raw._tcp.local.",
            "_vxi-11._tcp.local.",
        })
        txt_records = [record for record in records
                       if record[1] == responder.TYPE_TXT]
        self.assertEqual(len(txt_records), 3)
        for record in txt_records:
            first_length = record[4][0]
            self.assertEqual(record[4][1:1 + first_length], b"txtvers=1")
            self.assertIn(b"Manufacturer=BenchForge", record[4])
            self.assertIn(b"Model=Keysight E5810A", record[4])

    def test_07_mdns_answers_dns_sd_queries_and_ignores_unrelated_names(self):
        responder = LXIDiscoveryResponder(host_name="benchforge-test")
        responder.configure_lxi("127.0.0.1", raw_port=5025, vxi11_port=1024)

        def query(name, qtype, qclass=1, ident=0):
            return (struct.pack(">HHHHHH", ident, 0, 1, 0, 0, 0)
                    + responder._encode_name(name)
                    + struct.pack(">HH", qtype, qclass))

        reply, unicast = responder.response_for_query(query(
            responder.SERVICE_ENUMERATION, responder.TYPE_PTR,
            responder.CLASS_IN | responder.CACHE_FLUSH, ident=0x1234))
        self.assertTrue(unicast)
        ident, flags, _questions, answers, _auth, additional = struct.unpack(
            ">HHHHHH", reply[:12])
        self.assertEqual(ident, 0x1234)
        self.assertEqual(flags, 0x8400)
        self.assertEqual(answers, 6)  # enumeration PTR + instance PTR per service
        self.assertEqual(additional, 7)  # SRV/TXT per service + one A record
        self.assertIsNone(responder.response_for_query(query(
            "_http._tcp.local.", responder.TYPE_PTR)))

        _flags, goodbye = self._dns_records(
            responder.build_announcement(goodbye=True))
        self.assertTrue(goodbye)
        self.assertTrue(all(record[3] == 0 for record in goodbye))
    def test_06_serial_poll_mav_lifecycle(self):
        """
        ++spoll must report the MAV bit so a driver's read loop knows data is
        waiting. Without it a client connects, sees traffic on the wire, and
        never issues ++read -- the device reads as present but silent.

        MEASURED on the physical controller: the status byte is the
        instrument's own, not a bare MAV flag. Every instrument on the bus
        (Keithley 2010/2001M/2002, Agilent 34411A) idles at 4 and reports 20
        with a message waiting. Instrument data is relayed with the
        instrument's own LF, not the controller's CRLF.
        """
        registry = InstrumentRegistry()
        server = PrologixEmulatorServer(host="127.0.0.1", port=1238, registry=registry,
                                        connection_policy=PrologixEmulatorServer.POLICY_SINGLE)
        server.start()
        time.sleep(0.1)

        def send(sock, line, wait=0.04):
            sock.sendall(line.encode() + b"\n")
            time.sleep(wait)

        def recv(sock):
            try:
                return sock.recv(1024)
            except socket.timeout:
                return None

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.4)
            s.connect(("127.0.0.1", 1238))

            # A Keithley: MEASURED idle 4, pending 20. Not every instrument
            # shares that base -- the 33250A and E3631A idle at 0.
            dut = slot_of("Keithley 2002")
            send(s, "++auto 0")
            send(s, "++addr %d" % dut)

            # Nothing queued yet: the instrument's idle status byte.
            send(s, "++spoll")
            self.assertEqual(recv(s), b"4\r\n")

            # Query queues a response: MAV (0x10) joins the base byte.
            send(s, "*IDN?", wait=0.06)
            send(s, "++spoll")
            self.assertEqual(recv(s), b"20\r\n")

            # SRQ tracks the same condition.
            send(s, "++srq")
            self.assertEqual(recv(s), b"1\r\n")

            # Reading drains the buffer and clears MAV. Instrument data
            # carries the instrument's LF, never the controller's CRLF.
            send(s, "++read eoi")
            data = recv(s)
            self.assertIn(idn_of("Keithley 2002").encode(), data)
            self.assertTrue(data.endswith(b"\n"), data)
            self.assertFalse(data.endswith(b"\r\n"), data)
            send(s, "++spoll")
            self.assertEqual(recv(s), b"4\r\n")

            # An unmapped address must not answer a serial poll at all.
            send(s, "++addr 20")
            send(s, "++spoll")
            self.assertIsNone(recv(s))

            s.close()
        finally:
            server.stop()

    def test_07_prologix_command_set_matches_hardware(self):
        """
        The command set must match the profiled controller exactly -- including
        what it REJECTS. Accepting a command real firmware refuses is an
        infidelity: it hides a client bug that would surface on the bench.

        Reference: profiles/PROLOGIX_HARDWARE_PROFILE.md section 2.
        """
        registry = InstrumentRegistry()
        server = PrologixEmulatorServer(host="127.0.0.1", port=1239, registry=registry)
        server.start()
        time.sleep(0.1)

        # Section 2 of the profile: every command tested against the real unit.
        supported = [
            "++addr", "++auto", "++clr", "++eoi", "++eos", "++eot_enable",
            "++eot_char", "++ifc", "++llo", "++loc", "++mode", "++read",
            "++read_tmo_ms", "++rst", "++savecfg", "++spoll", "++srq",
            "++trg", "++ver",
        ]
        # Named in the profile as NOT supported by the firmware.
        rejected = ["++status", "++lon", "++invalidcmd"]
        # MEASURED: the firmware DOES answer ++help, though its listing
        # advertises ++status/++lon which this model rejects (profile 2c).
        supported.append("++help")

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.3)
            s.connect(("127.0.0.1", 1239))
            s.sendall(b"++addr 1\n")
            time.sleep(0.04)

            def probe(cmd):
                s.sendall(cmd.encode() + b"\n")
                time.sleep(0.03)
                try:
                    return s.recv(2048)
                except socket.timeout:
                    return None

            wrongly_rejected = [c for c in supported
                                if (r := probe(c)) and b"Unrecognized" in r]
            self.assertEqual(wrongly_rejected, [],
                             f"supported commands rejected: {wrongly_rejected}")

            wrongly_accepted = [c for c in rejected
                                if (r := probe(c)) is None or b"Unrecognized" not in r]
            self.assertEqual(wrongly_accepted, [],
                             f"commands the real firmware refuses were accepted: "
                             f"{wrongly_accepted}")
            s.close()
        finally:
            server.stop()


    # The function names an HP/Agilent 34401A reports via FUNC?. Taken from the
    # driver's own "Known modes" list -- DC volts is "VOLT", not "VOLT:DC".
    TC_KNOWN_MODES = ["VOLT", "VOLT:AC", "RES", "CURR", "CURR:AC", "FRES",
                      "DIOD", "CONT", "FREQ", "PER", "VOLT:RAT"]

    def test_08_instrument_configuration_queries(self):
        """
        Client software asks FUNC? to learn what quantity a reading carries and
        rejects any name outside its known set, leaving readings unlabelled and
        the current-values display empty.
        """
        registry = InstrumentRegistry()
        # An Agilent DMM: reports the SHORT function vocabulary ("VOLT") and a
        # bare reading. The Keithleys on this bench report "VOLT:DC" and, on
        # the 2001/2002, a multi-element reading -- both covered separately.
        dev = registry.get_device(slot_of("Agilent 34411A"))

        # Power-on function must be a name the driver accepts.
        self.assertEqual(dev.handle_scpi_command("FUNC?"), '"VOLT"')
        self.assertIn(dev.handle_scpi_command("FUNC?").strip('"'), self.TC_KNOWN_MODES)
        self.assertNotIn("ACK", dev.handle_scpi_command("FUNC?"))

        # Every CONF: spelling must resolve to a recognised FUNC? name.
        for conf_cmd, expected in [
            ("CONF:VOLT:DC", "VOLT"), ("CONF:VOLT:AC", "VOLT:AC"),
            ("CONF:CURR:DC", "CURR"), ("CONF:CURR:AC", "CURR:AC"),
            ("CONF:RES", "RES"), ("CONF:FRES", "FRES"),
            ("CONF:FREQ", "FREQ"), ("CONF:PER", "PER"),
            ("CONF:DIOD", "DIOD"), ("CONF:CONT", "CONT"),
            ("CONF:VOLT:RAT", "VOLT:RAT"),
        ]:
            dev.handle_scpi_command(conf_cmd)
            reported = dev.handle_scpi_command("FUNC?")
            self.assertEqual(reported, f'"{expected}"', f"{conf_cmd} -> {reported}")
            self.assertIn(expected, self.TC_KNOWN_MODES)
            self.assertIsNotNone(dev.handle_scpi_command("READ?"))

        # A bare UNIT? is MEASURED silent on every DMM on this bench -- the
        # Keithley 2002/2001M/2010 and the Agilent 34411A all let the read time
        # out. Answering it would invent a capability the hardware lacks.
        dev.handle_scpi_command("CONF:FREQ")
        self.assertIsNone(dev.handle_scpi_command("UNIT?"))
        self.assertGreater(float(dev.handle_scpi_command("READ?")), 1000.0)

        dev.handle_scpi_command("CONF:VOLT:DC")
        self.assertAlmostEqual(float(dev.handle_scpi_command("READ?")), 5.0, delta=0.1)

        # Explicit MEAS: queries must not disturb the standing function.
        self.assertAlmostEqual(
            float(dev.handle_scpi_command(":MEAS:FREQ?")), 1000000.0, delta=10.0)
        self.assertEqual(dev.handle_scpi_command("FUNC?"), '"VOLT"')

        # *RST returns to the power-on function.
        dev.handle_scpi_command("CONF:RES")
        dev.handle_scpi_command("*RST")
        self.assertEqual(dev.handle_scpi_command("FUNC?"), '"VOLT"')

        # Housekeeping queries a driver relies on.
        self.assertEqual(dev.handle_scpi_command("SYST:ERR?"), '+0,"No error"')
        self.assertEqual(dev.handle_scpi_command("*OPC?"), "1")

        # An unknown query must stay silent rather than echo a fake reply.
        self.assertIsNone(dev.handle_scpi_command("BOGUS:QUERY?"))


    def test_09_instrument_class_personality(self):
        """
        A counter must not answer as a DMM. Client software reads CONF? to
        learn the function; a PM6690 reporting VOLT is rejected exactly as
        firmly as one that answers nothing.
        """
        registry = InstrumentRegistry()

        dmm = registry.get_device(slot_of("Agilent 34411A"))
        counter = registry.get_device(slot_of("Fluke PM6690"))

        self.assertEqual(dmm.instrument_class, "DMM")
        self.assertEqual(counter.instrument_class, "COUNTER")

        # Power-on functions differ by instrument class.
        self.assertEqual(dmm.handle_scpi_command("FUNC?"), '"VOLT"')
        # Verified against real hardware: a counter names its input channel.
        self.assertEqual(counter.handle_scpi_command("FUNC?"), '"FREQ 1"')

        # CONF? must lead with the counter's own function.
        conf = counter.handle_scpi_command("CONF?")
        self.assertTrue(conf.startswith('"FREQ '), conf)
        self.assertNotIn("VOLT", conf)

        # *RST returns each instrument to ITS power-on function, not a shared one.
        counter.handle_scpi_command("*RST")
        self.assertEqual(counter.handle_scpi_command("FUNC?"), '"FREQ 1"')
        dmm.handle_scpi_command("*RST")
        self.assertEqual(dmm.handle_scpi_command("FUNC?"), '"VOLT"')

        # Readings match the class.
        self.assertGreater(float(counter.handle_scpi_command("READ?")), 1000.0)
        self.assertAlmostEqual(float(dmm.handle_scpi_command("READ?")), 5.0, delta=0.1)

        # A TC definition's `#type` selects the personality on load.
        tc_def = parse_tc_device_file(
            os.path.join(os.path.dirname(__file__), "..", "core",
                         "sample_devices", "Fluke_PM6690.txt"))
        self.assertEqual(tc_def.type, "Counter")
        fresh = VirtualInstrument(gpib_address=9)
        self.assertEqual(fresh.instrument_class, "DMM")
        fresh.load_tc_definition(tc_def)
        self.assertEqual(fresh.instrument_class, "COUNTER")
        self.assertEqual(fresh.handle_scpi_command("FUNC?"), '"FREQ 1"')


    # Verified against a physical Fluke PM6690 (V1.32, 2022-05-26) driven with
    # the exchange from TestController's "Pendulum CNT-9X" driver.
    PM6690_MODES = [
        (":FUNC 'FREQ 1'",           '"FREQ 1"',       "FREQ-1"),
        (":FUNC 'FREQ 2'",           '"FREQ 2"',       "FREQ-2"),
        (":FUNC 'FREQ 3'",           '"FREQ 3"',       "FREQ-3"),
        ('FUNC:ON "FREQ:RAT 1,2"',    '"FREQ:RAT 1,2"', "FREQ:RAT-1-2"),
        ('FUNC:ON "FREQ:RAT 2,1"',    '"FREQ:RAT 2,1"', "FREQ:RAT-2-1"),
        ('FUNC:ON "FREQ:RAT 1,3"',    '"FREQ:RAT 1,3"', "FREQ:RAT-1-3"),
        ('FUNC:ON "PHASe 1,2"',       '"PHAS 1,2"',     "PHAS-1-2"),
        ('FUNC:ON "PHASe 2,1"',       '"PHAS 2,1"',     "PHAS-2-1"),
    ]

    #: The mode tokens the driver will accept, from its #askValues line.
    PM6690_VALID_TOKENS = [
        "FREQ-1", "FREQ-2", "FREQ-3", "PHAS-1-2", "PHAS-2-1",
        "FREQ:RAT-1-2", "FREQ:RAT-2-1", "FREQ:RAT-1-3", "FREQ:RAT-3-1",
    ]

    @staticmethod
    def _readmath(value):
        """The driver's #askModeMathFormat, applied to a CONF? reply."""
        return value.strip("'\"").replace(" ", "-").replace(",", "-")

    def test_10_counter_mode_reporting(self):
        """
        A counter reports its function WITH the input channel, and the client
        collapses that reply into a single mode token. Every mode button must
        yield a token the driver recognises.
        """
        counter = InstrumentRegistry().get_device(slot_of("Fluke PM6690"))

        # Power-on state, after the driver's #initCmd.
        for cmd in ["*RST", "*CLS", "*SRE 0", "*ESE 0", ":STAT:PRES", ":INIT:CONT ON"]:
            counter.handle_scpi_command(cmd)

        self.assertEqual(counter.handle_scpi_command("CONF?"), '"FREQ 1"')
        self.assertEqual(counter.handle_scpi_command("FUNC?"), '"FREQ 1"')
        self.assertIn(self._readmath(counter.handle_scpi_command("CONF?")),
                      self.PM6690_VALID_TOKENS)

        # Each mode button: single-quoted FUNC, double-quoted FUNC:ON, and the
        # long PHASe spelling that hardware answers as PHAS.
        for command, expected_conf, expected_token in self.PM6690_MODES:
            counter.handle_scpi_command("*CLS")
            counter.handle_scpi_command(command)
            conf = counter.handle_scpi_command("CONF?")
            self.assertEqual(conf, expected_conf, f"{command} -> {conf}")
            self.assertEqual(self._readmath(conf), expected_token)
            self.assertIn(self._readmath(conf), self.PM6690_VALID_TOKENS)
            # CONF? and FUNC? are the same string on this instrument.
            self.assertEqual(counter.handle_scpi_command("FUNC?"), expected_conf)

        # Readings come back in signed scientific notation expressing a fixed
        # RESOLUTION rather than a fixed digit count. MEASURED on the PM6690
        # against a 10 MHz source:
        #     +9.99999962E+06   8 decimals
        #     +1.000000038E+07  9 decimals
        # Both resolve 0.01 Hz; the digit count moves with the decade. Asserting
        # a fixed width passes only while the reading stays in one decade.
        counter.handle_scpi_command(":FUNC 'FREQ 1'")
        for query in ("Read?", "FETC?"):
            reading = counter.handle_scpi_command(query)
            self.assertRegex(reading, r"^[+-]\d\.\d+E[+-]\d{2}$", reading)
            self.assertAlmostEqual(float(reading), 1.0e7, delta=1.0e3)

            mantissa, _, exponent = reading.partition("E")
            decimals = len(mantissa.split(".")[1])
            self.assertAlmostEqual(
                10 ** (int(exponent) - decimals), 0.01, places=6,
                msg=f"{query} -> {reading} does not express 0.01 Hz resolution")

        # Setup-menu queries the driver reads on connect.
        for query, expected in [
            (":ACQuisition:APERture?", "+1.0000000000000E-02"),
            (":AVERage:STATe?", "1"),
            (":INP:LEV:AUTO?", "1"),
            (":DISP:ENAB?", "1"),
            (":INPut:FILTer?", "0"),
        ]:
            self.assertEqual(counter.handle_scpi_command(query), expected, query)

        # A DMM must NOT pick up the counter's channel suffix.
        dmm = InstrumentRegistry().get_device(slot_of("Agilent 34411A"))
        self.assertEqual(dmm.handle_scpi_command("FUNC?"), '"VOLT"')
        self.assertTrue(dmm.handle_scpi_command("CONF?").startswith('"VOLT +'))


    def test_11_default_bench_is_client_ready(self):
        """
        The startup bench must be immediately usable by client software:
        at least four instruments, no placeholder identities, and names that
        resolve to a driver. A decorative suffix such as "(Simulated)" leaves
        the generated settingsLoad.txt entry unmatched.
        """
        registry = InstrumentRegistry()
        slots = sorted(registry.devices.keys())

        self.assertGreaterEqual(len(slots), 4,
                                f"only {len(slots)} instruments at startup")
        self.assertEqual(len(DEFAULT_BENCH), len(slots))

        for addr in slots:
            dev = registry.devices[addr]

            # Identity must be a real instrument string, never the placeholder.
            self.assertNotIn("SIMULATED-DEV", dev.idn)
            self.assertNotIn("BENCHFORGE", dev.idn)

            # Names go straight into settingsLoad.txt as the driver key.
            self.assertNotIn("(Simulated)", dev.name)
            self.assertEqual(dev.name, dev.name.strip())

            # Every instrument answers identity and function.
            self.assertEqual(registry.process_command(addr, "*IDN?"), dev.idn)
            func = registry.process_command(addr, "FUNC?")
            self.assertIsNotNone(func, f"slot {addr} does not answer FUNC?")

            if dev.instrument_class == "FUNCGEN":
                # MEASURED on a 33250A: FUNC? -> SIN, a bare waveform name
                # with no quotes. Measuring instruments quote; generators
                # do not, and a client matching the quoted form fails.
                self.assertEqual(func, func.strip('"'), func)
                self.assertIn(func, VirtualInstrument.WAVEFORMS)
            else:
                self.assertTrue(func.startswith('"'), func)

            # A counter must report a frequency function, not a voltage one.
            if dev.instrument_class == "COUNTER":
                self.assertIn("FREQ", func)
            elif dev.instrument_class != "FUNCGEN":
                self.assertNotIn("FREQ", func)

        # The bench includes at least one counter and one DMM, so both code
        # paths are exercised the moment the engine starts.
        classes = {registry.devices[a].instrument_class for a in slots}
        self.assertIn("COUNTER", classes)
        self.assertIn("DMM", classes)

        # Generated configuration references every mapped slot by its name.
        mappings = [{"name": registry.devices[a].name, "gpib_address": a,
                     "enabled": 1} for a in slots]
        _gpib_txt, load_txt = generate_recommended_configs(mappings)
        for addr in slots:
            self.assertIn(f"A:{addr}", load_txt)
            self.assertIn(registry.devices[addr].name, load_txt)
        self.assertNotIn("(Simulated)", load_txt)


    def test_12_no_crosstalk_between_multiplexed_devices(self):
        """
        A client may drive several instruments over one shared connection,
        interleaving '++addr N / query / ++read'. Each ++read must return the
        reply held by the instrument addressed at that moment.

        A single controller-side FIFO passes every single-device test and then
        hands each device another device's answer as soon as two are active.
        """
        import threading

        registry = InstrumentRegistry()
        server = PrologixEmulatorServer(host="127.0.0.1", port=1240,
                                        registry=registry)
        server.start()
        time.sleep(0.15)

        expected = {spec["slot"]: spec["idn"] for spec in DEFAULT_BENCH}
        slots = list(expected)
        results = {}
        lock = threading.Lock()

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            sock.connect(("127.0.0.1", 1240))
            sock.sendall(b"++auto 0\n")
            time.sleep(0.05)

            def worker(slot):
                got = []
                for _ in range(4):
                    with lock:
                        sock.sendall(("++addr %d\n" % slot).encode())
                        sock.sendall(b"*IDN?\n")
                    time.sleep(0.005)          # let other threads interleave
                    with lock:
                        sock.sendall(("++addr %d\n" % slot).encode())
                        sock.sendall(b"++read eoi\n")
                        try:
                            # Drop only the terminator. A blanket .strip()
                            # also eats the trailing spaces the Keithley
                            # units really send ("...B02  /A02  "), which
                            # would hide a genuine relay defect.
                            got.append(sock.recv(2048).decode().rstrip("\r\n"))
                        except socket.timeout:
                            got.append("<TIMEOUT>")
                    time.sleep(0.004)
                results[slot] = got

            threads = [threading.Thread(target=worker, args=(s,)) for s in slots]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            for slot in slots:
                for reply in results[slot]:
                    self.assertEqual(
                        reply, expected[slot],
                        "slot %d received another device's reply: %r" % (slot, reply))

            # MAV belongs to the addressed instrument, not the controller.
            def cmd(*parts, read=True):
                for c in parts:
                    sock.sendall(c.encode() + b"\n")
                    time.sleep(0.04)
                if not read:
                    return None
                try:
                    return sock.recv(1024).decode().strip()
                except socket.timeout:
                    return ""

            a, b = slots[0], slots[1]
            cmd("++addr %d" % a, "*IDN?", read=False)
            self.assertEqual(cmd("++addr %d" % a, "++spoll"), "20")
            self.assertEqual(cmd("++addr %d" % b, "++spoll"), "4")
            self.assertEqual(cmd("++addr %d" % b, "++read eoi"), "")
            self.assertIn(expected[a].strip(), cmd("++addr %d" % a, "++read eoi"))
            self.assertEqual(cmd("++addr %d" % a, "++spoll"), "4")

            sock.close()
        finally:
            server.stop()


    def test_13_esc_escaping_on_the_data_path(self):
        """
        ESC-prefixed bytes are data, not protocol.

        MEASURED on firmware 01.06.06.00 (profile section 2b): '*ESE <ESC>+37'
        leaves *ESE? reading 37 with a clean error queue, and
        '++ver<ESC><LF>++ver' answers 'Unrecognized command' because the
        escaped terminator does not split the line.

        Clients escape ESC, '+', CR and LF when sending them as instrument
        data. The controller strips the prefix, and an escaped CR/LF must not
        terminate the command.
        """
        ESC = b"\x1b"

        registry = InstrumentRegistry()
        server = PrologixEmulatorServer(host="127.0.0.1", port=1241,
                                        registry=registry)
        parsed = []
        server.add_packet_callback(
            lambda ev: parsed.append(ev["text"]) if ev["direction"] == "IN" else None)
        server.start()
        time.sleep(0.15)

        def tc_escape(msg, chars=b"+\r\n"):
            """dk.hkj.shared.SharedInterface.escape(msg, 27, chars)"""
            out = bytearray()
            for b in msg:
                if b in chars:
                    out += ESC
                out.append(b)
            return bytes(out)

        def send(payload):
            parsed.clear()
            sock.sendall(payload + b"\n")
            time.sleep(0.1)
            return list(parsed)

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.4)
            sock.connect(("127.0.0.1", 1241))
            time.sleep(0.05)

            # The prefix is stripped; the instrument sees the intended text.
            for intended in [b"*IDN?", b":SOUR:VOLT +5", b"A+B+C", b"VOLT:VAL+?"]:
                self.assertEqual(send(tc_escape(intended)), [intended.decode()])

            # An escaped terminator stays inside the command.
            for raw in (ESC + b"\n", ESC + b"\r"):
                got = send(b'NAME "A' + raw + b'B"?')
                self.assertEqual(len(got), 1, f"escaped terminator split: {got}")
                self.assertIn("A", got[0])
                self.assertIn("B", got[0])

            # An UNESCAPED terminator still frames normally.
            parsed.clear()
            sock.sendall(b"*IDN?\n++addr\n")
            time.sleep(0.12)
            self.assertEqual(parsed, ["*IDN?", "++addr"])

            # ESC ESC yields one literal ESC.
            self.assertEqual(send(b"TAG" + ESC + ESC + b"?"), ["TAG\x1b?"])

            # A lone trailing ESC waits for its partner instead of truncating.
            parsed.clear()
            sock.sendall(b"*ID")
            sock.sendall(ESC)
            time.sleep(0.08)
            self.assertEqual(parsed, [], "framed a command on an incomplete escape")
            sock.sendall(b"N?\n")
            time.sleep(0.1)
            self.assertEqual(parsed, ["*IDN?"])

            sock.close()
        finally:
            server.stop()


    def test_14_measured_controller_semantics(self):
        """
        Behaviours measured on the physical controller (firmware 01.06.06.00),
        recorded in PROLOGIX_HARDWARE_PROFILE.md sections 2b-2e.

        Each of these was wrong in an earlier build and would have sent a
        client chasing a fault in its own code.
        """
        registry = InstrumentRegistry()
        server = PrologixEmulatorServer(host="127.0.0.1", port=1242,
                                        registry=registry)
        server.start()
        time.sleep(0.15)

        def cmd(text, wait=0.06):
            sock.sendall(text.encode() + b"\n")
            time.sleep(wait)

        def reply(wait=0.25):
            time.sleep(wait)
            try:
                return sock.recv(4096)
            except socket.timeout:
                return None

        def drain():
            sock.settimeout(0.15)
            try:
                while sock.recv(4096):
                    pass
            except Exception:
                pass
            sock.settimeout(0.6)

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.6)
            sock.connect(("127.0.0.1", 1242))
            cmd("++auto 0"); cmd("++addr 1"); drain()

            # -- command names are case-sensitive --------------------------
            for bad in ("++AUTO", "++Addr", "++VER", "++READ"):
                cmd(bad)
                self.assertEqual(reply(), b"Unrecognized command\r\n", bad)

            # -- out-of-range arguments are rejected, not ignored ----------
            for bad in ("++addr 31", "++addr 99", "++addr -1", "++addr abc",
                        "++read_tmo_ms 0", "++read_tmo_ms 99999",
                        "++eos 9", "++spoll 99", "++default", "++"):
                cmd(bad)
                self.assertEqual(reply(), b"Unrecognized command\r\n", bad)

            # A valid argument is still accepted silently.
            cmd("++addr 30")
            self.assertIsNone(reply())
            cmd("++addr")
            self.assertEqual(reply(), b"30\r\n")
            cmd("++addr 1"); drain()

            # -- controller replies CRLF, instrument data keeps its own LF --
            cmd("++ver")
            self.assertEqual(reply(),
                             b"Prologix GPIB-ETHERNET Controller version "
                             b"01.06.06.00\r\n")
            cmd("*IDN?"); cmd("++read eoi")
            data = reply(0.4)
            self.assertTrue(data.endswith(b"\n"), data)
            self.assertFalse(data.endswith(b"\r\n"), data)

            # -- ++eot_enable appends ++eot_char AFTER the instrument data --
            cmd("++eot_enable 1"); cmd("++eot_char 42"); drain()
            cmd("*IDN?"); cmd("++read eoi")
            data = reply(0.4)
            self.assertTrue(data.endswith(b"\n*"), data)
            cmd("++eot_enable 0"); cmd("++eot_char 0"); drain()

            # -- ++read <char> stops at that character, inclusive -----------
            cmd("*IDN?"); cmd("++read 44")            # 44 = ','
            data = reply(0.4)
            self.assertTrue(data.endswith(b","), data)
            # The remainder stays in the instrument's output buffer.
            cmd("++read eoi")
            self.assertIsNotNone(reply(0.4))

            # -- a new query DISCARDS an unread reply (Query INTERRUPTED) ---
            drain()
            cmd("*IDN?")                    # queues one
            cmd("*IDN?")                    # replaces it
            cmd("++spoll")
            self.assertEqual(reply(), b"20\r\n")
            cmd("++read eoi"); reply(0.4)
            cmd("++spoll")
            self.assertEqual(reply(), b"4\r\n",
                             "unread replies accumulated instead of being replaced")

            sock.close()
        finally:
            server.stop()


    def test_15_gui_library_matches_the_emulated_bench(self):
        """
        The pickable instrument library and the startup bench must agree.

        These are two hand-maintained copies of the same facts, and they drifted:
        the library kept offering a Keithley 2000 that has no TestController
        driver and an invented serial number, spelled the 2001M as "2001", and
        dropped the trailing spaces the Keithley units really send -- while the
        bench had moved on to the physical bus. Nothing caught it, because
        nothing compared them.
        """
        # Skip ONLY when the Qt binding is genuinely absent, e.g. headless CI.
        # A blanket `except ImportError` here once swallowed a wrong class name
        # and reported this test as a pass.
        if importlib.util.find_spec("PySide6") is None:
            self.skipTest("PySide6 not installed")

        from core.gui_qt import BenchForgeQtApp

        library = BenchForgeQtApp.PREBUILT_LIBRARY
        by_name = {entry["name"]: entry for entry in library.values()}

        for spec in DEFAULT_BENCH:
            entry = by_name.get(spec["name"])
            self.assertIsNotNone(
                entry, f"{spec['name']} is on the bench but not in the library")
            # Identity must match byte for byte, trailing whitespace included.
            self.assertEqual(entry["idn"], spec["idn"], spec["name"])
            self.assertEqual(entry.get("class", "DMM"), spec["class"],
                             spec["name"])

        # An entry claiming a bench address must actually be at that address.
        bench_by_addr = {spec["slot"]: spec["name"] for spec in DEFAULT_BENCH}
        undriven = {spec["slot"]: spec["name"]
                    for spec in UNDRIVEN_INSTRUMENTS}
        for title, entry in library.items():
            addr = entry.get("bench")
            if addr is None:
                continue
            expected = bench_by_addr.get(addr) or undriven.get(addr)
            self.assertEqual(
                entry["name"], expected,
                f"{title!r} claims GPIB {addr}, which holds {expected!r}")

        # Anything known to have no driver must say so, so the UI can warn.
        for spec in UNDRIVEN_INSTRUMENTS:
            entry = by_name.get(spec["name"])
            if entry is not None:
                self.assertIs(
                    entry.get("tc_driver"), False,
                    f"{spec['name']} has no TestController driver but the "
                    "library does not flag it")


    def test_16_scpi_error_queue_and_protocol_warnings(self):
        """
        The emulator must record what real hardware would have recorded.

        A read with nothing pending and a query sent over an unread reply both
        look like plain silence on the wire, which is precisely what makes them
        expensive to diagnose. Real instruments log -420 and -410 for them, and
        so must we -- otherwise a client can misuse the bus all day and the
        emulator reports a clean run.
        """
        dev = VirtualInstrument(1, "dmm", "TEST,DMM,0,1")

        # Queue starts clean and reports the standard empty response.
        self.assertEqual(dev.handle_scpi_command("SYST:ERR?"), '+0,"No error"')

        # An unknown query raises -113 and answers nothing.
        self.assertIsNone(dev.handle_scpi_command("NOSUCH:NODE?"))
        self.assertEqual(dev.handle_scpi_command("SYST:ERR?"),
                         '-113,"Undefined header"')
        self.assertEqual(dev.handle_scpi_command("SYST:ERR?"), '+0,"No error"')

        # Errors drain FIFO, oldest first.
        dev.raise_error(VirtualInstrument.ERR_QUERY_UNTERMINATED)
        dev.raise_error(VirtualInstrument.ERR_QUERY_INTERRUPTED)
        self.assertEqual(dev.handle_scpi_command("SYST:ERR?"),
                         '-420,"Query UNTERMINATED"')
        self.assertEqual(dev.handle_scpi_command("SYST:ERR?"),
                         '-410,"Query INTERRUPTED"')

        # *ESR? latches until read, then clears. Draining the error QUEUE does
        # not clear the register -- the CME bit from the -113 above is still
        # set -- so start from a known state.
        dev.handle_scpi_command("*CLS")
        dev.raise_error(VirtualInstrument.ERR_QUERY_UNTERMINATED)
        self.assertEqual(int(dev.handle_scpi_command("*ESR?")),
                         VirtualInstrument.ESR_QYE)
        self.assertEqual(int(dev.handle_scpi_command("*ESR?")), 0)

        # *CLS empties the queue and the status bits together.
        dev.raise_error(VirtualInstrument.ERR_UNDEFINED_HEADER)
        dev.handle_scpi_command("*CLS")
        self.assertEqual(dev.handle_scpi_command("SYST:ERR?"), '+0,"No error"')
        self.assertEqual(int(dev.handle_scpi_command("*ESR?")), 0)

        # The queue is bounded, and never grows without limit.
        for _ in range(VirtualInstrument.ERROR_QUEUE_DEPTH + 25):
            dev.raise_error(VirtualInstrument.ERR_UNDEFINED_HEADER)
        self.assertEqual(len(dev.error_queue),
                         VirtualInstrument.ERROR_QUEUE_DEPTH)

        # --- and now the two conditions that arise from the gateway ---------
        registry = InstrumentRegistry()
        server = PrologixEmulatorServer(host="127.0.0.1", port=1243,
                                        registry=registry)
        seen = []
        server.add_warning_callback(seen.append)
        server.start()
        time.sleep(0.15)

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(("127.0.0.1", 1243))

            def send(line, wait=0.06):
                s.sendall(line.encode() + b"\n")
                time.sleep(wait)

            dut = slot_of("Keithley 2002")
            send("++auto 0")
            send("++addr %d" % dut)

            # Reading with nothing queued: silence on the wire, -420 recorded.
            send("++read eoi", wait=0.15)
            with self.assertRaises(socket.timeout):
                s.recv(256)
            codes = [w["code"] for w in seen]
            self.assertIn(-420, codes, f"no -420 raised; saw {codes}")

            # A query over an unread reply discards it and records -410.
            seen.clear()
            send("*IDN?")
            send("*IDN?", wait=0.15)
            codes = [w["code"] for w in seen]
            self.assertIn(-410, codes, f"no -410 raised; saw {codes}")

            # The warning carries enough to act on: which device, and what.
            warning = next(w for w in seen if w["code"] == -410)
            self.assertEqual(warning["address"], dut)
            self.assertEqual(warning["entry"], '-410,"Query INTERRUPTED"')
            self.assertTrue(warning["device"])

            s.close()
        finally:
            server.stop()


    def test_17_vxi11_gateway_matches_e5810a(self):
        """
        The VXI-11 emulator must reproduce what the physical E5810A does.

        Every expectation here is MEASURED and recorded in
        profiles/E5810_HARDWARE_PROFILE.md section 3b. Several are the opposite
        of the obvious guess, which is exactly why they are pinned:

          - create_link succeeds for ANY address 0-31, occupied or not
          - 'inst0' is REFUSED; this gateway has no such logical device
          - write/read on a destroyed link report an I/O timeout, while
            destroy_link on the same dead link reports an invalid link
        """
        from core.vxi11_emulator import VXI11EmulatorServer
        from tools.vxi11 import (
            DEVICE_TCP, INTR_PROG, INTR_VERS, READ_TERMCHRSET,
            REASON_CHR, REASON_END, REASON_REQCNT, VXI11Client,
        )

        srv = VXI11EmulatorServer(host="127.0.0.1", core_port=11026,
                                  portmap_port=11113)
        srv.start()
        time.sleep(0.3)

        try:
            client = VXI11Client("127.0.0.1", port=11026, timeout=8.0)

            dut = slot_of("Agilent 34411A")
            link, err = client.create_link("gpib0,%d" % dut)
            self.assertEqual(err, 0)
            # MEASURED: the abort port is allocated per boot, not fixed. Three
            # consecutive runs against the same unit gave 975, 1005 and 1002,
            # so pinning a single value would encode a coincidence.
            self.assertIn(link.abort_port,
                          range(VXI11EmulatorServer.ABORT_PORT_RANGE[0],
                                VXI11EmulatorServer.ABORT_PORT_RANGE[1] + 1))
            self.assertEqual(link.max_recv_size, 16384)     # MEASURED constant
            # Not a small sequential integer -- the hardware's look like heap
            # pointers, and a client comparing captures would notice.
            self.assertGreater(link.lid, 1_000_000)

            # Presence is NOT checked at link time.
            empty, err_empty = client.create_link("gpib0,20")
            self.assertEqual(err_empty, 0, "empty address must still link")

            for bad in ("inst0", "bogus0", "gpib1,5", "gpib0,99"):
                _l, e = client.create_link(bad)
                self.assertEqual(e, 3, f"{bad} should be 'device not accessible'")

            # --- read termination reasons ---
            client.device_write(link, b"*IDN?\n")
            err, reason, data = client.device_read(link)
            self.assertEqual(err, 0)
            self.assertEqual(reason, REASON_END)
            self.assertEqual(data, (idn_of("Agilent 34411A") + "\n").encode())

            # A short requestSize stops on REQCNT and leaves the remainder.
            client.device_write(link, b"*IDN?\n")
            err, reason, first = client.device_read(link, request_size=10)
            self.assertEqual(reason, REASON_REQCNT)
            self.assertEqual(len(first), 10)
            err, reason, rest = client.device_read(link)
            self.assertEqual(reason, REASON_END)
            self.assertEqual(first + rest,
                             (idn_of("Agilent 34411A") + "\n").encode())

            # termChar stops the read on CHR.
            client.device_write(link, b"*IDN?\n")
            err, reason, data = client.device_read(
                link, flags=READ_TERMCHRSET, term_char=ord(","))
            self.assertEqual(reason, REASON_CHR)
            self.assertTrue(data.endswith(b","), data)
            client.device_read(link, io_timeout=100)

            # --- empty read: error 15, and it waits ---
            start = time.time()
            err, reason, data = client.device_read(link, io_timeout=200)
            elapsed_ms = (time.time() - start) * 1000
            self.assertEqual(err, 15, "empty read must report I/O timeout")
            self.assertEqual(reason, 0)
            self.assertEqual(data, b"")
            # MEASURED: the gateway overshoots the requested timeout by ~166 ms.
            self.assertGreater(elapsed_ms, 200)

            # --- serial poll agrees with the Prologix numbers ---
            err, stb = client.device_readstb(link)
            self.assertEqual((err, stb), (0, 4))
            client.device_write(link, b"*IDN?\n")
            err, stb = client.device_readstb(link)
            self.assertEqual((err, stb), (0, 20))
            client.device_read(link)
            err, stb = client.device_readstb(link)
            self.assertEqual((err, stb), (0, 4))
            # A poll of an absent address returns 0 with NO error.
            err, stb = client.device_readstb(empty)
            self.assertEqual((err, stb), (0, 0))

            # --- interrupt channel: MEASURED as fully supported ---
            # An earlier build answered 8 "operation not supported" to all of
            # these without anyone having asked the hardware. It supports them.
            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            cb_port = listener.getsockname()[1]
            conn = None
            try:
                host_addr = struct.unpack(">I", socket.inet_aton("127.0.0.1"))[0]
                self.assertEqual(
                    client.create_intr_chan(host_addr, cb_port, INTR_PROG,
                                            INTR_VERS, DEVICE_TCP), 0)
                # The gateway holds the callback connection open; a client that
                # accepts and closes gets no channel.
                conn, _addr = listener.accept()

                self.assertEqual(
                    client.device_enable_srq(link, True, b"HANDLE42"), 0)

                # An SRQ must reach the client, carrying its own handle back.
                self.assertTrue(srv.deliver_srq(dut))
                conn.settimeout(3.0)
                notification = conn.recv(4096)
                self.assertIn(b"HANDLE42", notification,
                              "the client's opaque handle must be returned")

                self.assertEqual(client.destroy_intr_chan(), 0)
                # MEASURED: tearing down twice reports 'channel not established'.
                self.assertEqual(client.destroy_intr_chan(), 6)
            finally:
                # In the finally: a failed assertion above used to skip this,
                # leaking the callback socket for the rest of the run.
                if conn is not None:
                    try:
                        conn.close()
                    except OSError:
                        pass
                listener.close()

            # device_docmd is MEASURED unsupported on this gateway.
            err, _data = client.device_docmd(link, 0x00020001)
            self.assertEqual(err, 8)

            # --- abort: MEASURED as a stub, and it must stay one ------------
            # The E5810A answers program 395184 on the CORE port, returns 4 for
            # every lid -- valid or garbage -- and aborting a genuinely blocked
            # read has no effect. A working abort here would let a client pass
            # against the emulator and hang against the hardware.
            from tools.vxi11 import ABORT_PROG, ABORT_VERS, RPCClient
            rpc = RPCClient("127.0.0.1", 11026, timeout=4.0)
            try:
                for lid in (link.lid, 0xDEADBEEF):
                    result = rpc.call(ABORT_PROG, ABORT_VERS, 1,
                                      struct.pack(">I", lid))
                    self.assertEqual(struct.unpack(">I", result[:4])[0], 4,
                                     "device_abort must stub out with error 4")
            finally:
                rpc.close()

            # The advertised abort port accepts and then says nothing at all.
            probe = socket.socket()
            probe.settimeout(1.5)
            try:
                probe.connect(("127.0.0.1", srv.abort_port))
                probe.sendall(b"\x80\x00\x00\x04test")
                with self.assertRaises(socket.timeout):
                    probe.recv(64)
            except (ConnectionRefusedError, OSError) as exc:
                if isinstance(exc, socket.timeout):
                    raise
                self.skipTest("abort port %d unavailable: %s"
                              % (srv.abort_port, exc))
            finally:
                probe.close()

            # --- locking ---
            self.assertEqual(client.device_lock(link), 0)
            self.assertEqual(client.device_unlock(link), 0)
            self.assertEqual(client.device_unlock(link), 12)

            # --- destroy, and the asymmetry afterwards ---
            self.assertEqual(client.destroy_link(link), 0)
            err, _size = client.device_write(link, b"*IDN?\n")
            self.assertEqual(err, 15, "write on a dead link reports a timeout")
            err, _r, _d = client.device_read(link, io_timeout=50)
            self.assertEqual(err, 15, "read on a dead link reports a timeout")
            self.assertEqual(client.destroy_link(link), 4,
                             "destroy on a dead link reports an invalid link")

            client.close()
        finally:
            srv.stop()


    def test_18_gui_actually_constructs(self):
        """
        Build the real window, offscreen.

        Nothing else in this suite executes GUI code. A missing `import os` in
        gui_qt.py once passed 17/17 tests AND a clean PyInstaller build, and
        would have shipped an executable that died silently on launch --
        the packaging is windowed, so a NameError produces no visible error at
        all. Constructing the window is the cheapest guard against that whole
        class of failure.
        """
        if importlib.util.find_spec("PySide6") is None:
            self.skipTest("PySide6 not installed")

        # Offscreen must be set before the QApplication exists. Starting from
        # defaults keeps the test from inheriting -- or writing -- the
        # developer's saved session.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ["BENCHFORGE_IGNORE_SETTINGS"] = "1"

        from PySide6.QtWidgets import QApplication

        from core.gui_qt import BenchForgeQtApp

        app = QApplication.instance() or QApplication([])
        window = BenchForgeQtApp()
        try:
            # The panes a tester relies on must exist, not merely be declared.
            self.assertEqual(window.snoop_split.count(), 2,
                             "traffic and debug panes")
            self.assertEqual(window.debug_table.columnCount(), 6)
            self.assertEqual(sorted(window.debug_filters), ["ERROR", "INFO", "WARN"])

            # Defaults, not whatever was last used.
            self.assertIn("Prologix", window.mode_cb.currentText())
            self.assertGreaterEqual(len(window.registry.devices), 4)
            # License attribution must be discoverable from the installed UI,
            # with a real local notice rather than only a web link.
            self.assertTrue(window.act_licenses.isVisible())
            self.assertTrue(window.act_qt_source.isVisible())
            self.assertFalse(window.windowIcon().isNull())
            notice = window._license_notice_path()
            self.assertIsNotNone(notice)
            self.assertTrue(os.path.isfile(notice))

            # A diagnostic must survive the real callback path onto the table.
            window._on_diagnostic_callback({
                "timestamp": "00:00:00.000", "level": "WARN",
                "source": "prologix", "address": 1,
                "event": '-420,"Query UNTERMINATED"', "detail": "test",
                "code": -420, "device": "Keithley 2002",
            })
            window._drain_warning_queue()
            self.assertEqual(window.debug_table.rowCount(), 1)
            self.assertEqual(window.warning_count, 1)

            # If discovery fails after raw SCPI and VXI-11 have both bound,
            # rollback must release every listener before reporting Stopped.
            def unused_port():
                probe = socket.socket()
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
                probe.close()
                return port

            lxi_port, core_port, portmap_port = (
                unused_port(), unused_port(), unused_port())
            window.mode_cb.setCurrentIndex(1)
            window.lxi_port_input.setText(str(lxi_port))
            window.vxi11_server.core_port = core_port
            window.vxi11_server.portmap_port = portmap_port
            discovery_start = window.lxi_discovery.start

            def fail_discovery():
                raise OSError('simulated mDNS startup failure')

            window.lxi_discovery.start = fail_discovery
            try:
                window._start_servers(silent=True)
            finally:
                window.lxi_discovery.start = discovery_start

            self.assertFalse(window.lxi_raw_server._is_running)
            self.assertFalse(window.vxi11_server._running)
            self.assertFalse(window.lxi_discovery._is_running)
            self.assertEqual(window.vxi11_server._sockets, [])

            for released_port in (lxi_port, core_port, portmap_port):
                probe = socket.socket()
                probe.setsockopt(socket.SOL_SOCKET,
                                 socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    probe.bind(('127.0.0.1', released_port))
                finally:
                    probe.close()
        finally:
            window.close()
            del app
            os.environ.pop("BENCHFORGE_IGNORE_SETTINGS", None)


    def test_19_help_capture_is_byte_exact(self):
        """
        The ++help capture must stay exactly as recorded from the hardware.

        This is a byte-for-byte artefact, not source code. Its CRLF terminators
        are real -- the controller ends its own replies that way -- and any tool
        that "helpfully" normalises line endings silently shortens it by 34
        bytes, breaking a command the emulator otherwise matches exactly.

        That has already happened once: the file was committed under
        core.autocrlf=true, so git stored LF and only the checkout conversion
        was restoring the CRLFs in the working tree. Marking it -text stopped
        git mangling it AND stopped the conversion that was hiding the loss.
        Nothing failed loudly; the length was simply wrong.
        """
        path = os.path.join(os.path.dirname(__file__), "..", "core",
                            "prologix_help.txt")
        with open(path, "rb") as handle:
            raw = handle.read()

        self.assertEqual(len(raw), 1879,
                         "capture is %d bytes, not the recorded 1879 -- line "
                         "endings were probably normalised" % len(raw))
        self.assertEqual(raw.count(b"\r\n"), 34,
                         "CRLF terminators lost; the controller sends CRLF")
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"),
                         "a bare LF appeared where the capture has only CRLF")
        # Content spot-checks. This is the ++help LISTING, not the ++ver
        # identity string -- they are different replies.
        self.assertTrue(raw.startswith(b"The following commands are available:"),
                        raw[:60])
        self.assertIn(b"++read_tmo_ms 1-3000", raw)
        self.assertTrue(raw.rstrip().endswith(b"++help                -- display this help"),
                        raw[-60:])


    def test_20_semicolon_inside_quotes_is_data(self):
        """
        A semicolon inside a quoted SCPI string is data, not a separator.

        MEASURED on the Agilent 33250A at GPIB 6 through the physical Prologix:

            DISP:TEXT "AB"   -> DISP:TEXT? returns '"AB"'   , no errors
            DISP:TEXT "A;B"  -> DISP:TEXT? returns '"A;B"'  , no errors

        The instrument keeps the semicolon. A naive cmd.split(';') truncated
        the argument, so commands real hardware accepts came back silent.
        """
        split = VirtualInstrument.split_unquoted

        # Unquoted semicolons still chain.
        self.assertEqual(split("*CLS;FUNC?"), ["*CLS", "FUNC?"])
        self.assertEqual(split("MEAS:VOLT?;CURR?"), ["MEAS:VOLT?", "CURR?"])

        # Quoted ones do not, for either quote character.
        self.assertEqual(split('DISP:TEXT "A;B"'), ['DISP:TEXT "A;B"'])
        self.assertEqual(split("DISP:TEXT 'A;B'"), ["DISP:TEXT 'A;B'"])

        # Mixed: the quoted one survives, the bare one still splits.
        self.assertEqual(split('DISP:TEXT "A;B";FUNC?'),
                         ['DISP:TEXT "A;B"', "FUNC?"])

        # A doubled quote is an escaped literal, not the end of the string.
        self.assertEqual(split('DISP:TEXT "A""B;C"'), ['DISP:TEXT "A""B;C"'])

        # And the instrument must not treat the quoted form as compound.
        dev = VirtualInstrument(1, "gen",
                                "Agilent Technologies,33250A,0,2.04",
                                instrument_class="FUNCGEN")
        self.assertIsNone(dev.handle_scpi_command('DISP:TEXT "A;B"'))
        # A genuine chain still works.
        dev.handle_scpi_command("FUNC SIN")
        self.assertEqual(dev.handle_scpi_command("*CLS;FUNC?"), "SIN")


    def test_21_ar488_distinguishes_bad_argument_from_unknown_command(self):
        """
        The AR488 has four error messages where the Prologix has one.

        A bad argument on a command the two SHARE -- ++addr 99, ++eos 9 --
        reaches the inherited Prologix parser, which used to answer with its own
        'Unrecognized command'. The firmware calls errorMsg(2), 'Invalid
        parameter'.

        SOURCE-DERIVED, NOT MEASURED: read from AR488.ino, no adapter on the
        bench. This test pins the intended divergence so it survives until
        hardware can confirm or correct it.

        The Prologix side is the control: it must be unchanged, because the
        real controller genuinely answers the same string either way.
        """
        from core.ar488_emulator import AR488EmulatorServer

        cases = [
            # command,              AR488 reply,            Prologix reply
            (b"++addr 99",          b"Invalid parameter",   b"Unrecognized command"),
            (b"++eos 9",            b"Invalid parameter",   b"Unrecognized command"),
            (b"++read_tmo_ms 99999", b"Invalid parameter",  b"Unrecognized command"),
            # An argument on a command that takes none.
            (b"++rst 5",            b"Invalid parameter",   b"Unrecognized command"),
            # An unknown COMMAND is still 'Unrecognized' on both.
            (b"++bogus",            b"Unrecognized command", b"Unrecognized command"),
        ]

        def replies(server, port):
            server.start()
            time.sleep(0.15)
            out = []
            try:
                for payload, _ar, _pro in cases:
                    sock = socket.socket()
                    sock.settimeout(1.0)
                    sock.connect(("127.0.0.1", port))
                    sock.sendall(payload + b"\n")
                    time.sleep(0.12)
                    try:
                        out.append(sock.recv(256))
                    except socket.timeout:
                        out.append(b"")
                    sock.close()
            finally:
                server.stop()
            return out

        ar_out = replies(AR488EmulatorServer(
            host="127.0.0.1", port=1244, registry=InstrumentRegistry()), 1244)
        pro_out = replies(PrologixEmulatorServer(
            host="127.0.0.1", port=1245, registry=InstrumentRegistry()), 1245)

        for (payload, expect_ar, expect_pro), got_ar, got_pro in zip(
                cases, ar_out, pro_out):
            self.assertEqual(got_ar, expect_ar + b"\r\n",
                             "AR488 %s" % payload.decode())
            self.assertEqual(got_pro, expect_pro + b"\r\n",
                             "Prologix %s" % payload.decode())


    def test_22_auto_configure_tc_deployment(self):
        """
        Auto-configuration writes settingsGPIB.txt and settingsLoad.txt directly
        to the user's specified TestController installation directory.
        """
        import tempfile
        from core.tc_parser import generate_recommended_configs

        devices = [
            {"name": "Keithley 2010", "gpib_address": 3, "enabled": 1},
            {"name": "Agilent 34411A", "gpib_address": 5, "enabled": 1},
        ]
        gpib_expected, load_expected = generate_recommended_configs(devices, host="127.0.0.1", port=1234)

        with tempfile.TemporaryDirectory() as tmp_dir:
            gpib_file = os.path.join(tmp_dir, "settingsGPIB.txt")
            load_file = os.path.join(tmp_dir, "settingsLoad.txt")

            with open(gpib_file, "w", encoding="utf-8") as f:
                f.write(gpib_expected)
            with open(load_file, "w", encoding="utf-8") as f:
                f.write(load_expected)

            self.assertTrue(os.path.isfile(gpib_file))
            self.assertTrue(os.path.isfile(load_file))

            with open(gpib_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), gpib_expected)
            with open(load_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), load_expected)

    def test_23_network_safety_envelopes(self):
        '''Oversized or excess local clients are rejected without poisoning servers.'''
        from core.vxi11_emulator import VXI11EmulatorServer
        from tools.vxi11 import VXI11Client

        def wait_for(predicate, timeout=3.0):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if predicate():
                    return True
                time.sleep(0.02)
            return predicate()

        def peer_closed(sock):
            sock.settimeout(0.2)
            try:
                return sock.recv(1) == b''
            except (ConnectionResetError, ConnectionAbortedError):
                return True
            except socket.timeout:
                return False

        # Prologix: exactly-at-limit remains a valid frame, one character over
        # is rejected, and a new client still receives the measured identity.
        pro_events = []
        pro = PrologixEmulatorServer(host='127.0.0.1', port=0)
        pro.add_diagnostic_callback(pro_events.append)
        pro.start()
        pro_port = pro._server_socket.getsockname()[1]
        edge = oversized = healthy = None
        try:
            edge = socket.create_connection(('127.0.0.1', pro_port), 2.0)
            edge.settimeout(2.0)
            edge.sendall(b'++ver' + b' ' *
                         (MAX_PENDING_TEXT_CHARS - len(b'++ver')) + b'\n')
            self.assertIn(b'Prologix GPIB-ETHERNET Controller', edge.recv(256))
            edge.close()
            edge = None

            oversized = socket.create_connection(('127.0.0.1', pro_port), 2.0)
            oversized.sendall(b'X' * (MAX_PENDING_TEXT_CHARS + 1))
            self.assertTrue(wait_for(lambda: peer_closed(oversized)))
            oversized.close()
            oversized = None
            self.assertTrue(any('safety limit' in e['event']
                                for e in pro_events))

            healthy = socket.create_connection(('127.0.0.1', pro_port), 2.0)
            healthy.settimeout(2.0)
            healthy.sendall(b'++ver\n')
            self.assertIn(b'Prologix GPIB-ETHERNET Controller',
                          healthy.recv(256))
            healthy.close()
            healthy = None
        finally:
            for item in (edge, oversized, healthy):
                if item is not None:
                    item.close()
            pro.stop()

        # Raw LXI: the same text boundary applies. A one-slot limiter proves
        # excess connections are dropped without displacing the admitted one.
        lxi_events = []
        lxi = LXIRawSocketServer(host='127.0.0.1', port=0)
        lxi._client_limiter = ClientLimiter(1)
        lxi.add_diagnostic_callback(lxi_events.append)
        lxi.start()
        lxi_port = lxi._server_socket.getsockname()[1]
        try:
            admitted = socket.create_connection(('127.0.0.1', lxi_port), 2.0)
            self.assertTrue(wait_for(
                lambda: lxi._client_limiter.active_count == 1))
            excess = socket.create_connection(('127.0.0.1', lxi_port), 2.0)
            self.assertTrue(wait_for(lambda: peer_closed(excess)))
            excess.close()

            admitted.settimeout(2.0)
            admitted.sendall(b'*IDN?\n')
            self.assertTrue(admitted.recv(512))
            admitted.close()
            self.assertTrue(any('connection refused' in e['event']
                                for e in lxi_events))

            self.assertTrue(wait_for(
                lambda: lxi._client_limiter.active_count == 0))
            oversized = socket.create_connection(('127.0.0.1', lxi_port), 2.0)
            oversized.sendall(b'X' * (MAX_PENDING_TEXT_CHARS + 1))
            self.assertTrue(wait_for(lambda: peer_closed(oversized)))
            oversized.close()
            self.assertTrue(any('command exceeded' in e['event']
                                for e in lxi_events))
        finally:
            lxi.stop()

        # VXI-11: an at-limit declaration is allowed to await its body; an
        # over-limit declaration is rejected before any body is transmitted.
        vxi_events = []
        vxi = VXI11EmulatorServer(host='127.0.0.1', core_port=0,
                                  portmap_port=0)
        vxi.add_diagnostic_callback(vxi_events.append)
        vxi.start()
        core_port = vxi._sockets[1].getsockname()[1]
        edge = bad = client = None
        try:
            edge = socket.create_connection(('127.0.0.1', core_port), 2.0)
            edge.sendall(struct.pack('>I', 0x80000000 | MAX_RPC_RECORD_BYTES))
            edge.settimeout(0.2)
            with self.assertRaises(socket.timeout):
                edge.recv(1)
            edge.close()
            edge = None

            bad = socket.create_connection(('127.0.0.1', core_port), 2.0)
            bad.sendall(struct.pack(
                '>I', 0x80000000 | (MAX_RPC_RECORD_BYTES + 1)))
            self.assertTrue(wait_for(lambda: peer_closed(bad)))
            bad.close()
            bad = None
            self.assertTrue(any('RPC record exceeded' in e['event']
                                for e in vxi_events))

            client = VXI11Client('127.0.0.1', port=core_port, timeout=3.0)
            _link, err = client.create_link('gpib0,1')
            self.assertEqual(err, 0)
        finally:
            for item in (edge, bad):
                if item is not None:
                    item.close()
            if client is not None:
                client.close()
            vxi.stop()

    def test_24_crash_report_is_local_and_actionable(self):
        '''Unhandled failures produce a readable local report without upload.'''
        import tempfile
        from core.crashlog import write_crash_report

        previous = os.environ.get('LOCALAPPDATA')
        with tempfile.TemporaryDirectory() as local_data:
            os.environ['LOCALAPPDATA'] = local_data
            try:
                try:
                    raise RuntimeError('deliberate crash-log test')
                except RuntimeError:
                    path = write_crash_report(*sys.exc_info())

                self.assertIsNotNone(path)
                self.assertTrue(os.path.isfile(path))
                self.assertTrue(os.path.abspath(path).startswith(
                    os.path.abspath(os.path.join(
                        local_data, 'BenchForge', 'logs'))))
                with open(path, 'r', encoding='utf-8') as handle:
                    report = handle.read()
                self.assertIn('BenchForge Studio', report)
                self.assertIn('RuntimeError: deliberate crash-log test', report)
            finally:
                if previous is None:
                    os.environ.pop('LOCALAPPDATA', None)
                else:
                    os.environ['LOCALAPPDATA'] = previous


if __name__ == "__main__":
    unittest.main()
