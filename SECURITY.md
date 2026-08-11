# Security Policy

## Supported versions

Security fixes are made against the current default branch and included in the
next release. Once stable releases exist, only the latest stable release will
receive security fixes unless a release notice states otherwise.

## Reporting a vulnerability

Do not disclose a suspected vulnerability in a public issue. Use the
repository's **Report a vulnerability** form:

https://github.com/Cyclotronic/BenchForge/security/advisories/new

Include the affected version, operating system, reproduction steps, impact,
and any proposed mitigation. Do not include credentials, private instrument
captures, or unrelated personal information.

The maintainer will acknowledge a complete report, assess its severity, and
coordinate a fix and disclosure when warranted. This is a best-effort
open-source project and does not promise a particular response time.

BenchForge intentionally opens network listeners when the user starts an
emulation engine. An expected listener or mDNS advertisement is not by itself
a vulnerability; unexpected exposure, authentication bypass outside the
documented emulator behavior, or arbitrary code execution should be reported.
