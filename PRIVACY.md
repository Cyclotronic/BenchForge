# Privacy

BenchForge does not collect telemetry, usage analytics, crash reports, or
personal information. It does not contact an update service or any
BenchForge-operated Internet service.

BenchForge is a network instrument and gateway emulator. When the user starts
an emulation engine, it may:

- listen on the host and ports selected by the user;
- answer protocol requests from clients that connect to those listeners;
- advertise the selected emulation mode on the local network using mDNS when
  discovery is enabled and the listener is not bound to loopback; and
- make a VXI-11 interrupt callback to an address explicitly supplied by a
  connected VXI-11 client.

These network operations provide the requested emulator functionality. No
resulting traffic is sent to the BenchForge maintainers or to a third-party
analytics service.

The **Qt for Python Source** Help-menu command opens the Qt project website in
the user's default browser only when the user selects that command.

Instrument traffic and diagnostics remain in application memory unless the
user explicitly exports them. BenchForge does not upload exported data.

Questions about this policy may be opened as a GitHub issue that contains no
sensitive information.
