"""
AR488 / AR488Lan GPIB Adapter Emulator (`ar488_emulator.py`)

> SOURCE-DERIVED, NOT HARDWARE-MEASURED.
>
> Behaviour here is taken from the AR488 firmware source
> (Twilight-Logic/AR488, `src/AR488/AR488.ino`, ver. 0.53.46) and from
> TestController's client-side driver. No physical AR488 has been profiled.
> See profiles/AR488_ADAPTER_PROFILE.md, section 4, for what to measure.

The AR488 speaks a Prologix-*like* command set, so this subclasses the Prologix
server and overrides only where the firmware genuinely differs. Presenting a
Prologix as an AR488 would mislead a client on at least six points -- command
case, token delimiter, error variety, ++default, argument ranges, and the
version string.
"""

from typing import Optional

from .prologix_emulator import PrologixEmulatorServer, PrologixState, KIND_CTRL


class AR488EmulatorServer(PrologixEmulatorServer):
    """Emulates an AR488 (USB) or AR488Lan (socket) GPIB adapter."""

    #: `++ver` reply. Firmware-specific; set to match the unit being emulated.
    VERSION_STRING = "AR488 GPIB controller, ver. 0.53.46, 22/05/2026"

    #: errorMsg() in the firmware distinguishes four cases; Prologix has one.
    ERR_UNRECOGNIZED = "Unrecognized command"
    ERR_MISSING_PARAM = "Missing parameter"
    ERR_INVALID_PARAM = "Invalid parameter"
    ERR_TRANSMIT = "Transmit failed!"

    #: An out-of-range or unparseable argument on a SHARED command -- ++addr 99,
    #: ++eos 9 -- reaches the inherited Prologix parser. That parser used to
    #: answer with its own single message, so the AR488 persona reported
    #: 'Unrecognized command' where the firmware calls errorMsg(2). Overriding
    #: this one constant fixes every shared command without duplicating the
    #: parser.
    #:
    #: SOURCE-DERIVED, NOT MEASURED. Taken from errorMsg() case 2 in AR488.ino.
    #: No AR488 has been on the bench; re-verify when one is.
    ERR_BAD_ARGUMENT = ERR_INVALID_PARAM

    #: Wider than Prologix, per the firmware's command table.
    ARG_RANGES = dict(
        PrologixEmulatorServer.ARG_RANGES,
        auto=(0, 3),
        read_tmo_ms=(1, 32000),
        eor=(0, 7),
        flags=(0, 7),
        idn=(0, 2),
        lon=(0, 1),
        prom=(0, 1),
        ren=(0, 1),
        srqauto=(0, 1),
        status=(0, 255),
        tct=(0, 30),
        ton=(0, 2),
        macro=(0, 9),
    )

    #: Secondary addressing starts at 31 here, not 96.
    SECONDARY_RANGE = (31, 126)

    #: Commands the firmware knows that Prologix does not. Accepted silently so
    #: a client is not told the adapter is broken; none of them are modelled.
    EXTRA_COMMANDS = (
        "dcl", "default", "eor", "flags", "fndl", "id", "idn", "lon",
        "macro", "ppoll", "prom", "ren", "repeat", "send", "setvstr",
        "srqauto", "status", "tct", "ton", "unl", "unt", "verbose", "xdiag",
    )

    def _split_command(self, body: str):
        """
        Firmware: `strtok(buffr, " \\t")` -- space OR tab separates the token
        from its parameters, where Prologix accepts only a space.
        """
        token = body
        params = ""
        for i, ch in enumerate(body):
            if ch in " \t":
                token, params = body[:i], body[i + 1:]
                break
        return token, params.strip()

    def _process_command_line(self, state: PrologixState, line: str) -> list:
        cmd = line.strip()
        if not cmd.startswith("++"):
            return super()._process_command_line(state, cmd)

        responses = []

        def ctrl(text):
            responses.append((text, KIND_CTRL))

        token, params = self._split_command(cmd[2:])

        # Firmware compares with strcasecmp, so '++CLR' and '++clr' are equal.
        # TestController relies on this: its AR488 driver sends the control
        # commands in upper case.
        p_cmd = token.lower()

        if not p_cmd:
            ctrl(self.ERR_UNRECOGNIZED)
            return responses

        if p_cmd == "ver":
            # '++ver real' forces the firmware string over any custom one.
            ctrl(self.VERSION_STRING)
            return responses

        if p_cmd == "default":
            state.__init__()            # setDefaultCfg()
            return responses

        if p_cmd in self.EXTRA_COMMANDS:
            # Recognised by the firmware; range-check what we can, then accept.
            if p_cmd in self.ARG_RANGES and params:
                try:
                    value = int(params.split()[0])
                except ValueError:
                    ctrl(self.ERR_INVALID_PARAM)
                    return responses
                lo, hi = self.ARG_RANGES[p_cmd]
                if not lo <= value <= hi:
                    ctrl(self.ERR_INVALID_PARAM)
            return responses

        # Everything else follows the Prologix handler, but re-cased so its
        # case-sensitive guard does not reject a legitimately upper-case token,
        # and with the AR488 argument ranges applied by class attribute.
        return super()._process_command_line(
            state, "++" + p_cmd + ((" " + params) if params else ""))


class AR488LanEmulatorServer(AR488EmulatorServer):
    """
    AR488Lan: the same firmware behind a network socket.

    TestController's driver defaults to **port 23**, overridden by a
    `port:<n>` element in the interface settings
    (`SharedInterfaceAR488Lan.getPort()`).
    """

    DEFAULT_PORT = 23

    def __init__(self, host: str = "127.0.0.1", port: Optional[int] = None,
                 registry=None,
                 connection_policy: str = PrologixEmulatorServer.POLICY_SINGLE):
        super().__init__(host=host,
                         port=self.DEFAULT_PORT if port is None else port,
                         registry=registry,
                         connection_policy=connection_policy)
