"""
Single source of truth for the application version.

Kept in its own module so the GUI, the build script and the release notes
cannot drift apart.
"""

__version__ = "1.0.0-rc1"

#: Firmware/identity the Prologix persona reports. Changing this changes what
#: every client sees, so it lives beside the version rather than inline.
PROLOGIX_FIRMWARE = "01.06.06.00"

#: Date the hardware profiles in profiles/ were last verified end to end.
PROFILES_VERIFIED = "2026-08-10"


def banner() -> str:
    return "BenchForge Studio %s" % __version__
