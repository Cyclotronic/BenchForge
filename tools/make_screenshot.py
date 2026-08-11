"""
Regenerate the README screenshot from the current UI.

Renders offscreen and populates the panes with representative traffic, so the
image shows what the app actually looks like in use rather than an empty grid.
Run it after any visible UI change -- a stale screenshot is a documentation bug
that is easy to miss, because nothing fails.

    python tools/make_screenshot.py
    python tools/make_screenshot.py --out docs/images/screenshot_main.jpg

Starts from defaults and writes no settings, so it cannot disturb a saved
session.
"""
import argparse
import os
import sys
import time

ized = "--offscreen" in sys.argv
if ized:
    # Offscreen renders the LAYOUT correctly but resolves no fonts: the theme
    # asks for Segoe UI Variable and Cascadia Mono, and every glyph comes out
    # as a tofu box. Usable for a structural check, useless for documentation.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.argv.remove("--offscreen")
os.environ["BENCHFORGE_IGNORE_SETTINGS"] = "1"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtWidgets import QApplication                  # noqa: E402

from core.gui_qt import BenchForgeQtApp                     # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

parser = argparse.ArgumentParser()
parser.add_argument("--out", default=os.path.join(ROOT, "docs", "images",
                                                  "screenshot_main.jpg"))
parser.add_argument("--width", type=int, default=1440)
parser.add_argument("--height", type=int, default=920)
parser.add_argument("--page", type=int, default=3,
                    help="0 adapter, 1 library, 2 config, 3 traffic")
parser.add_argument("--quality", type=int, default=92)
args = parser.parse_args()

#: A short, realistic exchange: a client addressing three instruments and
#: reading back. Enough to show the columns doing their job.
TRAFFIC = [
    ("IN",  1, "++addr 1", 0.0),
    ("IN",  1, "*IDN?", 0.0),
    ("IN",  1, "++read eoi", 0.0),
    ("OUT", 1, "KEITHLEY INSTRUMENTS INC.,MODEL 2002,4461274,B02  /A02  ", 1.9),
    ("IN",  4, "++addr 4", 0.0),
    ("IN",  4, ":FUNC 'FREQ 1'", 0.0),
    ("IN",  4, "FUNC?", 0.0),
    ("IN",  4, "++read eoi", 0.0),
    ("OUT", 4, '"FREQ 1"', 2.4),
    ("IN",  4, "READ?", 0.0),
    ("IN",  4, "++read eoi", 0.0),
    ("OUT", 4, "+9.99999962E+06", 3.1),
    ("IN",  6, "++addr 6", 0.0),
    ("IN",  6, "FUNC?", 0.0),
    ("IN",  6, "++read eoi", 0.0),
    ("OUT", 6, "SIN", 1.7),
    ("IN",  5, "++addr 5", 0.0),
    ("IN",  5, "++read eoi", 0.0),
]

DIAGNOSTICS = [
    ("INFO", "prologix", None, "client connected", "127.0.0.1:52418"),
    ("INFO", "prologix", 4, "instrument addressed", "Fluke PM6690"),
    ("WARN", "prologix", 5, '-420,"Query UNTERMINATED"',
     "Read issued when the instrument had nothing queued. The client read "
     "before its query produced a reply, or read twice for one query."),
    ("WARN", "prologix", 6, '-113,"Undefined header"',
     "Query for a node this instrument does not have. Real hardware answers "
     "nothing and logs this."),
]


def main():
    app = QApplication.instance() or QApplication([])
    window = BenchForgeQtApp()
    window.resize(args.width, args.height)
    # The window must be shown for the native platform to lay out and resolve
    # fonts. It is closed again before this returns.
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        window.show()
        app.processEvents()

    window.nav.setCurrentRow(window._nav_row_for_page[args.page])
    window.stack.setCurrentIndex(args.page)

    now = time.strftime("%H:%M:%S")
    for index, (direction, address, text, latency) in enumerate(TRAFFIC):
        window._on_packet_event_callback({
            "timestamp": "%s.%03d" % (now, 100 + index * 37),
            "client": "127.0.0.1:52418",
            "direction": direction,
            "address": address,
            "raw_bytes": text.encode(),
            "text": text,
            "latency_ms": latency,
            "policy": "single_connection",
        })
    window._drain_snoop_queue()

    for index, (level, source, address, event, detail) in enumerate(DIAGNOSTICS):
        window._on_diagnostic_callback({
            "timestamp": "%s.%03d" % (now, 120 + index * 91),
            "level": level, "source": source, "address": address,
            "event": event, "detail": detail,
            "code": -420 if "420" in event else (-113 if "113" in event else None),
            "device": "",
        })
    window._drain_warning_queue()

    # Let the layout settle before grabbing, or the splitter renders at its
    # pre-layout sizes and the panes come out wrong.
    app.processEvents()
    time.sleep(0.4)
    app.processEvents()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    pixmap = window.grab()
    if not pixmap.save(args.out, quality=args.quality):
        print("failed to write %s" % args.out)
        window.close()
        return 1

    print("wrote %s  (%dx%d, %.0f KB)"
          % (args.out, pixmap.width(), pixmap.height(),
             os.path.getsize(args.out) / 1024.0))
    print("traffic rows: %d   debug rows: %d"
          % (window.snoop_table.rowCount(), window.debug_table.rowCount()))
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
