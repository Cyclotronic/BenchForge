"""
Dynamic Instrument Device Emulator (`device_emulator.py`)

Simulates instrument response logic on mapped GPIB addresses (0-30).
Supports response matching from parsed TestController device files, standard SCPI defaults,
and synthetic dynamic measurement generation.
"""

import math
import random
import re
import threading
from typing import Dict, List, Optional
from .tc_parser import TCDeviceDefinition


class VirtualInstrument:
    """Emulates a single virtual instrument on a GPIB address."""

    #: Instrument classes and the measurement function each powers up in. A
    #: counter that reports itself measuring volts is rejected by client
    #: software just as firmly as one that answers nothing at all.
    CLASS_DEFAULT_FUNCTION = {
        "DMM": "VOLT",
        "COUNTER": "FREQ",
        "SCOPE": "VOLT",
        "PSU": "VOLT",
        # A generator names its waveform, not a measurement. MEASURED on an
        # Agilent 33250A: FUNC? -> SIN, unquoted.
        "FUNCGEN": "SIN",
    }

    #: TestController `#type` values mapped onto those classes.
    TC_TYPE_TO_CLASS = {
        "DMM": "DMM", "MULTIMETER": "DMM", "VOLTMETER": "DMM",
        "COUNTER": "COUNTER", "FREQUENCYCOUNTER": "COUNTER", "TIMER": "COUNTER",
        "SCOPE": "SCOPE", "OSCILLOSCOPE": "SCOPE",
        "PSU": "PSU", "POWERSUPPLY": "PSU",
        "FUNCGEN": "FUNCGEN", "FUNCTIONGENERATOR": "FUNCGEN",
        "GENERATOR": "FUNCGEN", "SIGNALGENERATOR": "FUNCGEN", "AWG": "FUNCGEN",
    }

    #: Waveform names an Agilent 33250A accepts and reports back, unquoted.
    WAVEFORMS = ("SIN", "SQU", "RAMP", "PULS", "NOIS", "DC", "USER")

    #: SCPI-99 standard errors, and the Standard Event Status Register bit each
    #: sets. These three cover the conditions a misbehaving client provokes.
    #:
    #: The text is deliberately NOT vendor-specific, and that costs nothing:
    #: MEASURED on this bench, all three strings came back byte-identical from
    #: Keithley, Agilent, HP and Fluke instruments alike. Only '-213 Init
    #: ignored' and '-350 Queue overflow' varied by vendor, and we raise
    #: neither.
    ERR_UNDEFINED_HEADER = -113
    ERR_QUERY_INTERRUPTED = -410
    ERR_QUERY_UNTERMINATED = -420

    ESR_QYE = 0x04   # query error
    ESR_CME = 0x20   # command error

    SCPI_ERRORS = {
        ERR_UNDEFINED_HEADER:   ("Undefined header", ESR_CME),
        ERR_QUERY_INTERRUPTED:  ("Query INTERRUPTED", ESR_QYE),
        ERR_QUERY_UNTERMINATED: ("Query UNTERMINATED", ESR_QYE),
    }

    #: One depth for every model. Real queues differ (roughly 10 on the
    #: Keithleys, 20 on the Agilent/HP units) but reproducing that would mean a
    #: per-model measurement campaign for a detail no client depends on.
    ERROR_QUEUE_DEPTH = 20

    #: Function names are family-dependent, verified on real hardware:
    #:   Agilent 34411A / Keysight 34461A / HP 34401A -> FUNC? "VOLT"
    #:   Keithley 2010 / 2001M / 2002                 -> FUNC? "VOLT:DC"
    #: A client matching the reply against its own mode list rejects the wrong
    #: spelling outright, so this cannot be normalised away.
    STYLE_SHORT = "SHORT"
    STYLE_LONG = "LONG"

    #: SHORT reply name -> LONG reply name.
    LONG_FUNCTION_NAMES = {
        "VOLT": "VOLT:DC", "CURR": "CURR:DC",
    }

    #: IDN fragments that select the long-form vocabulary.
    LONG_STYLE_VENDORS = ("KEITHLEY",)

    def __init__(self, gpib_address: int, name: str = "", idn: str = "",
                 instrument_class: str = "DMM"):
        self.gpib_address = gpib_address
        self.name = name or f"Virtual Instrument {gpib_address}"
        self.idn = idn or f"BENCHFORGE,SIMULATED-DEV-{gpib_address},SN{1000+gpib_address},v1.0"
        self.tc_definition: Optional[TCDeviceDefinition] = None
        self.custom_responses: Dict[str, str] = {}
        self.nominal_voltage = 5.0
        self.nominal_current = 0.5
        self.nominal_frequency = 1000000.0
        self.nominal_temperature = 22.86

        self.instrument_class = instrument_class.upper()

        idn_upper = self.idn.upper()

        # Serial-poll status byte with no message pending; ++spoll returns
        # base|0x10 once a reply is waiting.
        #
        # MEASURED, and it is NOT uniform:
        #   Keithley 2002/2001M/2010, Fluke PM6690, Agilent 34411A -> 4  (20 pending)
        #   Agilent 33250A, HP E3631A                              -> 0  (16 pending)
        # Bit 2 tracks the error queue on the Agilent/HP units, so a unit that
        # idles at 0 reads 4 once its queue is dirty. See the profile note.
        self.status_byte_base = 0 if any(
            m in idn_upper for m in ("33250A", "E3631A")) else 4

        # Integer replies (*ESR?, *STB?) carry an explicit sign on Agilent/HP
        # instruments and none on Keithley or Fluke. MEASURED: 34411A '+36',
        # 33250A '+164', E3631A '+0' against Keithley '116', PM6690 '196'.
        self.signs_integers = any(
            v in idn_upper for v in ("AGILENT", "HEWLETT-PACKARD", "KEYSIGHT"))

        # *OPT? is not universal. MEASURED silent on the 33250A and E3631A,
        # which then log -113 "Undefined header" -- the E3631A's command set
        # genuinely omits it. Answering it would be an invented capability.
        self.supports_opt = not any(
            m in idn_upper for m in ("33250A", "E3631A"))

        # *OPT? payload. MEASURED: Keithley 2001M/2010 '0,0', 2002 'MEM2,0',
        # Fluke PM6690 'Option 30, Option 10, 0'.
        if "MODEL 2002" in idn_upper:
            self.options = "MEM2,0"
        elif "PM6690" in idn_upper:
            self.options = "Option 30, Option 10, 0"
        else:
            self.options = "0,0"

        # The Keithley 2001/2002 return a multi-element reading rather than a
        # bare number. MEASURED on both; the 2010 does NOT do this.
        #   +10.000086E+00NVDC,+20565.811885SECS,+40709RDNG#,00EXTCHAN
        self.reading_elements = any(
            m in idn_upper for m in ("MODEL 2002", "MODEL 2001"))
        self.reading_number = 40709
        self.timestamp_base = 20565.811885

        # SCPI version reported by SYST:VERS?, MEASURED per family.
        if "KEITHLEY" in idn_upper:
            self.scpi_version = "1991.0"
        elif "FLUKE" in idn_upper:
            self.scpi_version = "1999.0"
        elif "E3631A" in idn_upper:
            self.scpi_version = "1995.0"
        else:
            self.scpi_version = "1994.0"

        # Generator state, MEASURED at rest on the 33250A.
        self.gen_frequency = 1000.0
        self.gen_amplitude = 0.1
        self.gen_offset = 0.0
        self.gen_output_on = False
        self.gen_load = 50.0
        self.gen_duty = 50.0

        # Supply state, MEASURED at rest on the E3631A (P6V rail selected,
        # output off, 5 A limit).
        self.psu_rail = "P6V"
        self.psu_set_voltage = 0.0
        self.psu_set_current = 5.0
        self.psu_output_on = False
        # What the supply's own meter reads, which is NOT the setpoint. MEASURED
        # on the E3631A with the output off: VOLT? -> +0.00000000E+00 while
        # MEAS:VOLT? -> -4.81193600E-01. Reporting the setpoint for both, as we
        # did, hides the offset a client actually sees.
        self.psu_meas_voltage = -0.481194
        self.psu_meas_current = 1.691650

        # SCPI error queue and latched event-status bits. Real instruments
        # accumulate these; an emulator that always answers "No error" cannot
        # show a developer that their client just misused the bus.
        self.error_queue: List[str] = []
        self.esr_bits = 0

        # Real GPIB hardware serves one client, and POLICY_SINGLE reproduces
        # that -- so on the faithful path this lock is never contended. It
        # exists for POLICY_MULTI, an explicitly non-faithful convenience mode
        # where several client threads share one instrument: without it,
        # function, error_queue, esr_bits and reading_number race.
        self._state_lock = threading.RLock()
        #: Called with (address, code, text) whenever an error is raised, so the
        #: gateway can surface it live rather than waiting for a SYST:ERR?.
        self.error_listener = None

        # Input channel(s) a counter is measuring on. Reported after the
        # function name by both CONF? and FUNC?, e.g. "FREQ 1", "FREQ:RAT 1,2".
        self.channel_spec = "1"
        if self.instrument_class == "COUNTER":
            self.nominal_frequency = 10000000.0  # 10 MHz reference

        # Per-model configuration constants. Autoranging reports the model's
        # top range, which differs by instrument (34411A 1000 V, 2010 1010 V,
        # 2001M/2002 1100 V), so callers can set these to match a target.
        self.max_range = 1000.0
        self.nplc = 1.0
        self.averaging_on = False
        # Autoranging is instrument STATE, not a constant. We used to answer
        # '1' unconditionally, which matched the bench only while every meter
        # happened to be autoranging; a 34411A set to a fixed range exposed it.
        self.autorange = True

        # Vendors disagree on how FUNC? spells a DC function; infer from IDN.
        self.function_style = (
            self.STYLE_LONG
            if any(v in self.idn.upper() for v in self.LONG_STYLE_VENDORS)
            else self.STYLE_SHORT
        )

        # Present measurement function, reported by FUNC? and CONF?. Client
        # software uses this to label readings with a quantity and unit.
        self.function = self.CLASS_DEFAULT_FUNCTION.get(self.instrument_class, "VOLT")

    # Function names exactly as an HP/Agilent 34401A reports them via FUNC?.
    # Note DC volts is plain "VOLT" and DC current plain "CURR" -- the ":DC"
    # suffix appears only in the CONF:/MEAS: command form, never in the reply.
    #   name -> (nominal attribute, jitter sigma, decimal places, unit)
    FUNCTION_PROFILES = {
        "VOLT":     ("nominal_voltage", 0.001, 6, "V"),
        "VOLT:AC":  ("nominal_voltage", 0.001, 6, "V"),
        "CURR":     ("nominal_current", 0.0001, 6, "A"),
        "CURR:AC":  ("nominal_current", 0.0001, 6, "A"),
        "RES":      (None, 0.0, 3, "OHM"),
        "FRES":     (None, 0.0, 3, "OHM"),
        "FREQ":     ("nominal_frequency", 0.1, 2, "HZ"),
        "PER":      (None, 0.0, 9, "S"),
        "DIOD":     (None, 0.0, 6, "V"),
        "CONT":     (None, 0.0, 3, "OHM"),
        "VOLT:RAT": (None, 0.0, 6, ""),
        # MEASURED on an Agilent 34411A configured for a 10k thermistor:
        # FUNC? -> "TEMP", READ? -> +2.28648288E+01.
        "TEMP":     ("nominal_temperature", 0.01, 6, "C"),
        # Counter functions. Verified against a Fluke PM6690 (V1.32).
        "FREQ:RAT": (None, 0.0, 9, ""),
        "PHAS":     (None, 0.0, 3, "DEG"),
        "PDUT":     (None, 0.0, 3, "PCT"),
        "PWID":     (None, 0.0, 9, "S"),
        "TOT":      (None, 0.0, 0, ""),
    }

    #: Functions a counter expresses with an input-channel suffix.
    COUNTER_FUNCTIONS = ("FREQ", "PER", "FREQ:RAT", "PHAS", "PDUT", "PWID", "TOT")

    # Command-form spellings that map onto those reply names.
    FUNCTION_ALIASES = {
        "VOLT:DC": "VOLT", "VOLTAGE": "VOLT", "VOLTAGE:DC": "VOLT",
        "VOLTAGE:AC": "VOLT:AC", "CURR:DC": "CURR", "CURRENT": "CURR",
        "CURRENT:DC": "CURR", "CURRENT:AC": "CURR:AC",
        "RESISTANCE": "RES", "FRESISTANCE": "FRES", "RES:FOUR": "FRES",
        "FREQUENCY": "FREQ", "PERIOD": "PER", "DIODE": "DIOD",
        "TEMPERATURE": "TEMP",
        "CONTINUITY": "CONT", "VOLTAGE:RATIO": "VOLT:RAT", "VOLT:RATIO": "VOLT:RAT",
        # Counter spellings. Hardware normalises the long forms in its replies:
        # PHASe is accepted but answered as PHAS.
        "PHASE": "PHAS", "FREQ:RATIO": "FREQ:RAT", "FREQUENCY:RATIO": "FREQ:RAT",
        "PDUTycycle": "PDUT", "PDUTYCYCLE": "PDUT", "PWIDTH": "PWID",
        "TOTALIZE": "TOT", "TOT:CONT": "TOT",
    }

    def _normalise_function(self, text: str) -> Optional[str]:
        """Maps a CONF:/MEAS: function spelling onto its FUNC? reply name."""
        t = text.upper().strip().strip('"').strip("'").lstrip(":")
        t = t.split()[0] if t else ""
        t = t.split(",")[0]
        if t in self.FUNCTION_PROFILES:
            return t
        return self.FUNCTION_ALIASES.get(t)

    def _parse_function_spec(self, text: str):
        """
        Splits a function argument into its name and channel specification.

        Counters address inputs explicitly, and clients send the pair as one
        quoted argument in several spellings:

            :FUNC 'FREQ 1'            -> ("FREQ",     "1")
            FUNC:ON "FREQ:RAT 1,2"    -> ("FREQ:RAT", "1,2")
            FUNC:ON "PHASe 1,2"       -> ("PHAS",     "1,2")

        Returns (function, channel_spec) with channel_spec None when absent.
        """
        raw = text.strip().strip('"').strip("'").strip()
        if not raw:
            return None, None

        head, _, tail = raw.partition(" ")
        function = self._normalise_function(head)
        if function is None:
            return None, None

        channels = tail.strip().replace(" ", "")
        return function, (channels or None)

    def _apply_function_spec(self, text: str) -> bool:
        """Sets function and channel from a quoted spec. True when understood."""
        # A generator's FUNC argument names a waveform, not a measurement.
        if self.instrument_class == "FUNCGEN":
            token = text.strip().strip('"').strip("'").split()[0].upper() if text.strip() else ""
            if token in self.WAVEFORMS:
                self.function = token
                return True
            return False

        function, channels = self._parse_function_spec(text)
        if function is None:
            return False
        self.function = function
        if channels:
            self.channel_spec = channels
        return True

    def _function_reply(self) -> str:
        """
        The quoted function string reported by FUNC? (and, on counters, CONF?).

        A DMM names only the function; a counter always carries its channel.
        The DC spelling follows the vendor's own convention.
        """
        # A generator answers with a bare waveform name and no quotes at all.
        # MEASURED on the 33250A: FUNC? -> SIN. Quoting it, as every measuring
        # instrument here does, produces a token no driver table contains.
        if self.instrument_class == "FUNCGEN":
            return self.function
        if self.instrument_class == "COUNTER" and self.function in self.COUNTER_FUNCTIONS:
            return f'"{self.function} {self.channel_spec}"'
        name = self.function
        if self.function_style == self.STYLE_LONG:
            name = self.LONG_FUNCTION_NAMES.get(name, name)
        return f'"{name}"'

    def _measure(self, query: str = "READ?") -> str:
        """
        Generates a reading consistent with the currently selected function.

        Counters answer in signed scientific notation (a real PM6690 returns
        '+1.000000006E+07'); DMMs answer in plain decimal.
        """
        attr, sigma, places, _unit = self.FUNCTION_PROFILES.get(
            self.function, ("nominal_voltage", 0.001, 6, "V"))

        if attr is None:
            fixed = {"RES": 1000.123, "FRES": 1000.123, "CONT": 0.041,
                     "DIOD": 0.652341, "PER": 1.0 / self.nominal_frequency,
                     "VOLT:RAT": 0.500000, "FREQ:RAT": 1.000000, "PHAS": 90.0,
                     "PDUT": 50.0, "PWID": 0.000000050, "TOT": 0.0}
            value = fixed.get(self.function, 0.0)
            sigma = 0.0
        else:
            value = getattr(self, attr)

        if sigma:
            value += random.gauss(0, sigma)

        # A counter formats to a fixed measurement RESOLUTION, not a fixed
        # number of digits. MEASURED on a PM6690 against a 10 MHz source:
        #     +9.99999962E+06   (8 decimals)
        #     +1.000000038E+07  (9 decimals)
        # Both express 0.01 Hz. The digit count changes with the decade, so
        # tying it to READ? vs FETC? -- as an earlier reading of one sample
        # suggested -- gets it right only half the time.
        if self.instrument_class == "COUNTER":
            quantised = round(value, 2)
            exponent = math.floor(math.log10(abs(quantised))) if quantised else 0
            decimals = max(exponent + 2, 0)
            return f"{quantised:+.{decimals}E}"

        # The Keithley 2001/2002 return a reading with its elements attached
        # rather than a bare number. MEASURED on both:
        #   +10.000086E+00NVDC,+20565.811885SECS,+40709RDNG#,00EXTCHAN
        # A client that parses only the leading float still works; one that
        # splits on ',' sees four fields, as it would against real hardware.
        if self.reading_elements:
            self.reading_number += 21
            self.timestamp_base += 1.274553
            # MEASURED: the mantissa is NOT normalised. A 10 V reading is
            # '+10.000086E+00', not '+1.0000086E+01' -- the exponent stays at
            # E+00 and the value is printed in full. Only the 10 V case has
            # been observed, so the fixed exponent is marked derived.
            places = 6 if "2002" in self.idn.upper() else 5
            return (f"{value:+.{places}f}E+00{self._element_unit()},"
                    f"+{self.timestamp_base:.6f}SECS,"
                    f"+{self.reading_number}RDNG#,00EXTCHAN")

        # Every other measuring instrument on the bench answers in signed
        # scientific notation with eight decimals. MEASURED identically on the
        # Keithley 2010 (+1.00001363E+01) and the Agilent 34411A
        # (+2.28648288E+01) -- plain decimal was an invention on our part.
        return f"{value:+.8E}"

    #: Element suffix Keithley appends to a reading. Only NVDC is MEASURED
    #: (both units were sitting on a 10 V DC reference); the rest follow the
    #: same documented construction and are marked derived in the profile.
    ELEMENT_UNITS = {
        "VOLT": "NVDC", "VOLT:AC": "NVAC", "CURR": "NADC", "CURR:AC": "NAAC",
        "RES": "NOHM", "FRES": "NOHM4", "FREQ": "NHZ", "PER": "NSEC",
        "TEMP": "NDEG",
    }

    def _element_unit(self) -> str:
        return self.ELEMENT_UNITS.get(self.function, "NVDC")

    def raise_error(self, code: int):
        """
        Records a SCPI error and latches the matching event-status bit.

        Oldest entries are dropped once the queue is full. Real hardware
        instead replaces the last entry with -350 "Queue overflow"; we do not
        raise -350, because our queue exists to tell a developer what their
        client did, not to reproduce a full instrument's bookkeeping.
        """
        text, bit = self.SCPI_ERRORS.get(code, ("Unknown error", self.ESR_CME))
        entry = f'{code},"{text}"'
        with self._state_lock:
            self.error_queue.append(entry)
            del self.error_queue[:-self.ERROR_QUEUE_DEPTH]
            self.esr_bits |= bit
        # The listener is called OUTSIDE the lock: it reaches the GUI, and
        # holding an instrument's lock across a callback we do not control is
        # how an emulator ends up deadlocked behind its own user interface.
        if self.error_listener is not None:
            try:
                self.error_listener(self.gpib_address, code, text)
            except Exception:
                pass

    def take_error(self) -> str:
        """Pops the oldest error, FIFO, or reports a clean queue."""
        with self._state_lock:
            if self.error_queue:
                return self.error_queue.pop(0)
            return '+0,"No error"'

    def clear_status(self):
        """*CLS: empties the error queue and clears the latched status bits."""
        with self._state_lock:
            self.error_queue.clear()
            self.esr_bits = 0

    def _integer(self, value: int) -> str:
        """
        Formats an integer reply the way this vendor does.

        MEASURED: Agilent/HP sign them ('+36', '+4', '+0'); Keithley and Fluke
        do not ('116', '4', '0'). A client comparing the reply as a string --
        as several driver tables do -- sees these as different values.
        """
        return f"{value:+d}" if self.signs_integers else str(value)

    def _unit(self) -> str:
        return self.FUNCTION_PROFILES.get(self.function, (None, 0, 0, "V"))[3]

    def _format_setting(self, value: float, places: int = 6) -> str:
        """
        Formats a configuration value the way this vendor reports settings.

        Keithley answers plain decimals ('10.000000', '1.00'); Agilent and
        Keysight answer signed scientific ('+1.00000000E+01').
        """
        if self.function_style == self.STYLE_LONG:
            return f"{value:.{places}f}"
        return f"{value:+.8E}"

    def _conf_parameters(self) -> str:
        """
        Parameters reported after the function name by CONF?.

        The two instrument families answer differently, and clients parse the
        whole string as one token:

          DMM     "VOLT +1.000000E+01,+3.000000E-06"   function, range, resolution
          COUNTER "FREQ 1"                             function, input channel

        A counter that reports a DMM's range/resolution pair produces a token no
        driver lookup table contains, which reads as an unknown mode.
        """
        if self.instrument_class == "COUNTER":
            return str(self.channel_spec)
        # Only Agilent/Keysight DMMs reach here -- Keithley answers CONF? with
        # the bare function name. MEASURED on a 34411A: the range/resolution
        # pair carries EIGHT decimals ('+1.00000000E+00,+3.00000000E-07'), not
        # the six we used to emit.
        if self.function == "TEMP":
            # MEASURED verbatim on a 34411A with a 10k thermistor:
            #   "TEMP THER,10000,+1.00000000E+00,+3.00000000E-07"
            return "THER,10000,+1.00000000E+00,+3.00000000E-07"
        if self.function in ("CURR", "CURR:AC"):
            return "+1.00000000E+00,+1.00000000E-06"
        if self.function in ("RES", "FRES", "CONT"):
            return "+1.00000000E+03,+1.00000000E-03"
        if self.function in ("FREQ", "PER"):
            return f"+{self.nominal_frequency:.8E},+1.00000000E-01"
        return "+1.00000000E+01,+3.00000000E-06"

    def load_tc_definition(self, tc_def: TCDeviceDefinition):
        """Loads a TestController device definition onto this virtual instrument."""
        self.tc_definition = tc_def
        self.name = tc_def.name or self.name
        if tc_def.id_pattern:
            self.idn = tc_def.id_pattern

        # The definition's `#type` states what kind of instrument this is, so
        # adopt the matching personality rather than staying a default DMM.
        key = (tc_def.type or "").upper().replace(" ", "").replace("-", "")
        resolved = self.TC_TYPE_TO_CLASS.get(key)
        if resolved:
            self.instrument_class = resolved
            self.function = self.CLASS_DEFAULT_FUNCTION.get(resolved, self.function)

    @staticmethod
    def split_unquoted(text: str, sep: str = ";") -> List[str]:
        """
        Split on `sep`, ignoring occurrences inside a quoted string.

        MEASURED on an Agilent 33250A: sending DISP:TEXT "A;B" and reading
        DISP:TEXT? returns "A;B" intact, with an empty error queue. The
        instrument treats a semicolon inside quotes as data, so a naive
        cmd.split(';') truncates arguments that real hardware accepts.

        SCPI allows either quote character, and a doubled quote inside a string
        is an escaped literal rather than the end of it.
        """
        parts, current, quote = [], [], ""
        i = 0
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == quote:
                    # A doubled quote is an escaped one, not the terminator.
                    if i + 1 < len(text) and text[i + 1] == quote:
                        current.append(ch)
                        current.append(ch)
                        i += 2
                        continue
                    quote = ""
                current.append(ch)
            elif ch in "\"'":
                quote = ch
                current.append(ch)
            elif ch == sep:
                parts.append("".join(current))
                current = []
            else:
                current.append(ch)
            i += 1
        parts.append("".join(current))
        return parts

    def _handle_compound(self, cmd: str) -> Optional[str]:
        """
        Executes a ';'-separated command chain and joins the replies.

        A leading ':' on a segment restarts at the root; without one the segment
        inherits the previous command's header path, so 'MEAS:VOLT?;CURR?'
        means MEAS:VOLT? followed by MEAS:CURR?.
        """
        replies = []
        header: List[str] = []

        for segment in self.split_unquoted(cmd, ";"):
            segment = segment.strip()
            if not segment:
                continue

            if segment.startswith(":") or segment.startswith("*"):
                full = segment
                if segment.startswith(":"):
                    header = segment.lstrip(":").split()[0].split(":")[:-1]
            elif header:
                full = ":".join(header + [segment])
            else:
                full = segment
                header = segment.split()[0].split(":")[:-1]

            reply = self.handle_scpi_command(full)
            if reply is not None:
                replies.append(reply)

        return ";".join(replies) if replies else None

    def handle_scpi_command(self, raw_cmd: str) -> Optional[str]:
        """
        Handles an incoming SCPI command/query for this instrument.
        Returns response string if query, or None if command requires no reply.

        Serialised per instrument: a real instrument processes one message at a
        time, so two client threads sharing one device under POLICY_MULTI must
        not interleave halfway through a state change. The lock is reentrant
        because compound commands re-enter through _handle_compound.
        """
        with self._state_lock:
            return self._handle_scpi_command(raw_cmd)

    def _handle_scpi_command(self, raw_cmd: str) -> Optional[str]:
        cmd = raw_cmd.strip()
        if not cmd:
            return None

        # SCPI compound form: ':MEAS:VOLT?;CURR?;POW?' is three commands, with
        # each continuation resuming at the previous header's level. Replies are
        # joined with ';'. Clients use this to fetch several values in one trip.
        # Only an UNQUOTED semicolon chains commands. A quoted one is data --
        # MEASURED on a 33250A, which returns DISP:TEXT "A;B" intact.
        if len(self.split_unquoted(cmd, ";")) > 1:
            return self._handle_compound(cmd)

        cmd_upper = cmd.upper()
        base_cmd = cmd_upper.split()[0]  # Base command string (e.g., ':MEAS:VOLT:DC? 10,0.001' -> ':MEAS:VOLT:DC?')

        # Normalized lookup keys (including lstrip(':') variants)
        check_keys = [cmd_upper, base_cmd, cmd_upper.lstrip(":"), base_cmd.lstrip(":")]

        # 1. Check explicit custom responses first
        for key in check_keys:
            if key in self.custom_responses:
                return self.custom_responses[key]

        # 2. Check TC definition matching
        if self.tc_definition:
            for key in check_keys:
                if key in self.tc_definition.non_query_commands:
                    return None
                if key in self.tc_definition.commands:
                    resp = self.tc_definition.commands[key]
                    if resp is not None and len(resp.strip()) > 0:
                        return resp
                    if not base_cmd.endswith("?"):
                        return None

        # 3. Standard SCPI IEEE 488.2 Mandatory Commands
        for key in check_keys:
            if key in ("*IDN?", "ID?"):
                return self.idn
            elif key == "*CLS":
                self.clear_status()
                return None
            elif key in ("*RST", "*WAI"):
                # Reset returns the instrument to its own power-on function --
                # a counter must come back as FREQ, not as a DMM's VOLT.
                self.function = self.CLASS_DEFAULT_FUNCTION.get(
                    self.instrument_class, "VOLT")
                if key == "*RST":
                    self.clear_status()
                return None
            elif key == "*OPT?":
                # Silent where the model genuinely lacks the command; the real
                # units then log -113 "Undefined header" rather than replying.
                return self.options if self.supports_opt else None
            elif key in ("*STB?",):
                return self._integer(self.status_byte_base)
            elif key == "*ESR?":
                # Reading the event-status register clears it: the bits latch
                # until read, which is how a client sees an error it missed.
                bits, self.esr_bits = self.esr_bits, 0
                return self._integer(bits)
            elif key == "*TST?":
                return self._integer(0)
            elif key == "*OPC?":
                return "1"

        bare = base_cmd.lstrip(":")
        # SENSe: is an optional root on measurement headers, so ':SENS:FUNC?'
        # and 'FUNC?' address the same node.
        for root in ("SENSE:", "SENS:"):
            if bare.startswith(root):
                bare = bare[len(root):]
                break
        # Counters number their inputs (INP1:, INP2:); the nodes below are
        # the same, so normalise to the unnumbered form.
        bare = re.sub(r"^(INPUT|INP)(\d+):", r"\1:", bare)

        # 4. Function selection. Clients spell this several ways:
        #       :CONF:FREQ                 header carries the function
        #       :FUNC 'FREQ 1'             single-quoted function + channel
        #       FUNC:ON "FREQ:RAT 1,2"     double-quoted, ratio across channels
        #    All must land on the same state, since FUNC?/CONF? report it back.
        if bare in ("FUNC", "FUNCTION", "FUNC:ON", "FUNCTION:ON"):
            arg = cmd.split(None, 1)[1] if " " in cmd else ""
            self._apply_function_spec(arg)
            return None

        if bare.startswith("CONF:") or bare.startswith("CONFIGURE:"):
            header = bare.split(":", 1)[1]
            arg = cmd.split(None, 1)[1] if " " in cmd else ""
            resolved = self._normalise_function(header)
            if resolved:
                self.function = resolved
                # ':CONF:FREQ 2' addresses an input as a trailing argument.
                channels = arg.strip().strip('"').strip("'").replace(" ", "")
                if channels and self.instrument_class == "COUNTER":
                    if all(c.isdigit() or c == "," for c in channels):
                        self.channel_spec = channels
            return None

        # 5. Range and autorange are settable state, so track them. MEASURED
        #    consequence on a 34411A: selecting an explicit range turns
        #    autoranging off, which is how the bench came to report
        #    VOLT:DC:RANG:AUTO? -> 0 while the emulator still insisted on 1.
        if not base_cmd.endswith("?"):
            arg = cmd.split(None, 1)[1].strip() if " " in cmd else ""
            if bare.endswith("RANG:AUTO") or bare.endswith("RANGE:AUTO"):
                token = arg.upper()
                if token in ("1", "ON"):
                    self.autorange = True
                elif token in ("0", "OFF"):
                    self.autorange = False
                return None
            if bare.endswith("RANG") or bare.endswith("RANGE"):
                token = arg.upper()
                if token == "AUTO":
                    self.autorange = True
                else:
                    try:
                        self.max_range = float(arg)
                        self.autorange = False
                    except ValueError:
                        pass
                return None

        # 5b. Strict Non-Query Check: base command must end with '?'
        if not base_cmd.endswith("?"):
            return None

        # 6. Instrument configuration queries. Client software uses these to
        #    label readings; answering with a generic acknowledgement leaves it
        #    unable to assign a quantity or unit.
        if bare in ("FUNC?", "FUNCTION?"):
            return self._function_reply()
        if bare in ("CONF?", "CONFIGURE?"):
            # CONF? and FUNC? are the same string on a counter, and MEASURED
            # identical on the Keithley 2002/2001M/2010 too -- all three answer
            # a bare "VOLT:DC". Only the Agilent/Keysight DMMs append the
            # range and resolution pair.
            if (self.instrument_class == "COUNTER"
                    or self.function_style == self.STYLE_LONG):
                return self._function_reply()
            return f'"{self.function} {self._conf_parameters()}"'
        # A bare UNIT? is MEASURED silent on the Keithley 2002/2001M/2010 and
        # the Agilent 34411A. We used to answer it, which invented a capability
        # and hid the read timeout a client would really see.
        if bare in ("SYST:ERR?", "SYSTEM:ERROR?"):
            return self.take_error()
        if bare in ("SYST:VERS?", "SYSTEM:VERSION?"):
            return self.scpi_version

        # --- Function generator. MEASURED on an Agilent 33250A at rest. ---
        if self.instrument_class == "FUNCGEN":
            gen = {
                "FREQ?": f"{self.gen_frequency:+.13E}",
                "VOLT?": f"{self.gen_amplitude:+.13E}",
                "VOLT:OFFS?": f"{self.gen_offset:+.13E}",
                "VOLT:UNIT?": "VPP",
                "OUTP?": "1" if self.gen_output_on else "0",
                "OUTP:STAT?": "1" if self.gen_output_on else "0",
                "OUTP:LOAD?": f"{self.gen_load:+.13E}",
                "OUTP:POL?": "NORM",
                "PHAS?": "+0.0000000000000E+00",
                "FUNC:SQU:DCYC?": f"{self.gen_duty:+.13E}",
                "FUNC:RAMP:SYMM?": "+1.0000000000000E+02",
                "BURS:STAT?": "0", "SWE:STAT?": "0",
                "AM:STAT?": "0", "FM:STAT?": "0",
                "TRIG:SOUR?": "IMM",
            }
            if bare in gen:
                return gen[bare]

        # --- Power supply. MEASURED on an HP E3631A at rest, P6V selected. ---
        if self.instrument_class == "PSU":
            psu = {
                "INST?": self.psu_rail, "INST:SEL?": self.psu_rail,
                "VOLT?": f"{self.psu_set_voltage:+.8E}",
                "CURR?": f"{self.psu_set_current:+.8E}",
                "MEAS:VOLT?": f"{self.psu_meas_voltage:+.8E}",
                "MEAS:CURR?": f"{self.psu_meas_current:+.8E}",
                "OUTP?": "1" if self.psu_output_on else "0",
                "OUTP:STAT?": "1" if self.psu_output_on else "0",
                # MEASURED quoted, unlike every other reply this unit gives.
                "APPL?": (f'"{self.psu_set_voltage:.6f},'
                          f'{self.psu_set_current:.6f}"'),
                "DISP?": "1",
            }
            if bare in psu:
                return psu[bare]
            # VOLT:PROT? / VOLT:PROT:STAT? are MEASURED silent -- the E3631A
            # has no OVP subsystem, so it answers nothing at all.
            if bare.startswith("VOLT:PROT"):
                return None

        # Counter setup menu. Values match a Fluke PM6690 (V1.32) at reset.
        if bare in ("ACQ:APER?", "ACQUISITION:APERTURE?"):
            return "+1.0000000000000E-02"
        if bare in ("AVER:STAT?", "AVERAGE:STATE?"):
            return "1"
        if bare in ("INP:LEV:AUTO?", "INPUT:LEVEL:AUTO?"):
            return "1"
        if bare in ("DISP:ENAB?", "DISPLAY:ENABLE?"):
            return "1"
        if bare in ("INP:FILT?", "INPUT:FILTER?"):
            return "0"
        if bare in ("TRIG:SOUR?", "TRIGGER:SOURCE?"):
            return "IMM"

        # Sense-subsystem configuration. Clients read these to populate their
        # setup menus; staying silent makes each one wait out a read timeout.
        # Values and formats verified against the instruments listed above.
        if bare.endswith("RANG:AUTO?") or bare.endswith("RANGE:AUTO?"):
            return "1" if self.autorange else "0"
        if bare.endswith(":AUTO?") or bare in ("ZERO:AUTO?", "INP:IMP:AUTO?"):
            return "0" if bare.startswith("INP:IMP") else "1"
        if bare.endswith("RANG?") or bare.endswith("RANGE?"):
            return self._format_setting(self.max_range, places=6)
        if bare.endswith("NPLC?"):
            return self._format_setting(self.nplc, places=2)
        if bare.endswith("AVER:STAT?") or bare.endswith("AVERAGE:STATE?"):
            return "1" if self.averaging_on else "0"
        if bare.endswith("AVER:COUN?") or bare.endswith("AVERAGE:COUNT?"):
            return "10"
        if bare.endswith("INP:IMP?") or bare.endswith("INPUT:IMPEDANCE?"):
            return "+1.0000000000000E+06"
        if bare.endswith("COUP?") or bare.endswith("COUPLING?"):
            return "AC"
        if bare.endswith("ATT?") or bare.endswith("ATTENUATION?"):
            return "+1.0000000000000E+00"
        if bare.endswith("TERM?") or bare.endswith("TERMINALS?"):
            return "+3"

        # 7. Measurement queries follow the selected function.
        if bare in ("READ?", "FETC?", "FETCH?", "VAL?", "MEAS?"):
            # A generator does not measure. The 33250A logs -113 and stays
            # silent on any query it has no node for.
            if self.instrument_class == "FUNCGEN":
                return None
            return self._measure(bare)

        # An explicit MEAS:/CONF: query names its own function, e.g.
        # ':MEAS:VOLT:DC?' - honour that without changing the standing mode.
        for prefix in ("MEAS:", "MEASURE:", "READ:", "FETC:"):
            if bare.startswith(prefix):
                requested = self._normalise_function(bare[len(prefix):].rstrip("?"))
                if requested:
                    previous, self.function = self.function, requested
                    try:
                        return self._measure()
                    finally:
                        self.function = previous
                return self._measure()

        # Bare function-named queries such as 'VOLT:DC?' or 'FREQ?'
        requested = self._normalise_function(bare.rstrip("?"))
        if requested:
            previous, self.function = self.function, requested
            try:
                return self._measure()
            finally:
                self.function = previous

        # 8. Unknown query. Real hardware answers nothing and logs an error to
        #    the queue; echoing the command back looks like data and misleads
        #    clients that parse the reply.
        #
        #    Only unknown QUERIES raise -113. An unrecognised set-command
        #    reaches step 5 and returns there, indistinguishable from a valid
        #    one we simply accept silently -- so raising an error for it would
        #    invent failures. Under-reporting is the safer error here.
        self.raise_error(self.ERR_UNDEFINED_HEADER)
        return None


#: The bench BenchForge presents on startup.
#:
#: Every `name` here is spelled exactly as the corresponding TestController
#: driver's `#name`, because the generated settingsLoad.txt uses these strings
#: to resolve a driver -- a decorative suffix such as "(Simulated)" leaves the
#: device unmatched and the client unable to load it. The IDN strings were read
#: from the physical instruments, so identification succeeds byte for byte.
#: Mirrors the physical bench on the Prologix at 192.168.1.80, captured
#: 2026-08-10. Addresses, identities and trailing whitespace are verbatim --
#: the Keithley units really do pad with two spaces before the terminator.
DEFAULT_BENCH = [
    {"slot": 1, "name": "Keithley 2002", "class": "DMM",
     "idn": "KEITHLEY INSTRUMENTS INC.,MODEL 2002,4461274,B02  /A02  "},
    {"slot": 2, "name": "Keithley 2001M", "class": "DMM",
     "idn": "KEITHLEY INSTRUMENTS INC.,MODEL 2001M,1150952,B16  /A02  "},
    {"slot": 3, "name": "Keithley 2010", "class": "DMM",
     "idn": "KEITHLEY INSTRUMENTS INC.,MODEL 2010,0636735,A10  /A02  "},
    {"slot": 4, "name": "Fluke PM6690", "class": "COUNTER",
     "idn": "FLUKE, PM6690, 979819, V1.32 26 May 2022 09:54"},
    {"slot": 5, "name": "Agilent 34411A", "class": "DMM",
     "idn": "Agilent Technologies,34411A,MY48005929,2.43-2.40-0.09-46-09"},
]

#: On the bus but deliberately NOT in the startup bench.
#:
#: The HP E3631A sits at address 7 on the physical bench, but TestController
#: ships no driver for it -- `AgilentHP E363xA.TXT` covers the E3632A, E3633A
#: and E3634A only. A bench entry whose name resolves to no driver leaves the
#: client unable to load the device, so it is offered from the library instead,
#: where choosing it is a deliberate act.
UNDRIVEN_INSTRUMENTS = [
    {"slot": 7, "name": "HP E3631A", "class": "PSU",
     "idn": "HEWLETT-PACKARD,E3631A,0,2.1-5.0-1.0",
     "note": "no TestController driver; emulated for bus fidelity only"},
]


def build_instrument(spec: Dict[str, object]) -> "VirtualInstrument":
    """Creates a VirtualInstrument from a DEFAULT_BENCH-style specification."""
    return VirtualInstrument(
        gpib_address=int(spec["slot"]),
        name=str(spec["name"]),
        idn=str(spec["idn"]),
        instrument_class=str(spec.get("class", "DMM")),
    )


class InstrumentRegistry:
    """Registry managing virtual instruments mapped across GPIB addresses 0–30."""

    def __init__(self):
        self.devices: Dict[int, VirtualInstrument] = {}
        # Pre-populate default virtual instruments
        self._init_defaults()

    def _init_defaults(self):
        for spec in DEFAULT_BENCH:
            self.devices[spec["slot"]] = build_instrument(spec)

    def get_device(self, gpib_address: int) -> Optional[VirtualInstrument]:
        """Retrieves virtual instrument for a given GPIB address, or None if unmapped."""
        return self.devices.get(gpib_address)

    def set_device(self, gpib_address: int, device: VirtualInstrument):
        """Sets a virtual instrument on a specific GPIB slot."""
        self.devices[gpib_address] = device

    def remove_device(self, gpib_address: int):
        """Removes device from a slot."""
        if gpib_address in self.devices:
            del self.devices[gpib_address]

    def process_command(self, gpib_address: int, raw_cmd: str) -> Optional[str]:
        """Routes a SCPI command to the instrument assigned to gpib_address."""
        device = self.get_device(gpib_address)
        if device is None:
            return None  # Unmapped address returns None (simulating bus timeout / no response)
        return device.handle_scpi_command(raw_cmd)

