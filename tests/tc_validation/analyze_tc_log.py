"""
Analyze a TestController debug console log against the emulator's TX log.

Checks the three Prologix observations that were reported to the developer:

  Obs 1  corrupted reads  - every payload TestController reports receiving must
                            exactly equal a frame the emulator actually sent.
                            Anything else is corruption: a *suffix* of a sent
                            frame is the leading-bytes-lost signature originally
                            reported, and a payload matching nothing sent covers
                            a byte dropped from the middle, which also occurs.
  Obs 2  close-path NPE   - NullPointerException in writeWithDelay. Fires on
                            reconnect as well as on shutdown.
  Obs 3  early thread exit- device threads that stop before identifying.

Both inputs may be plain files or the gzipped form kept under captures/.

Usage:
    python analyze_tc_log.py --tc-log tc-3.49.log --tx 3.49-tx.jsonl
    python analyze_tc_log.py --tc-log captures/2026-08-14/tc-pos49.log.gz \
                             --tx     captures/2026-08-14/pos49-tx.jsonl.gz
"""

import argparse
import gzip
import io
import json
import re
import sys


def _open_text(path):
    """Opens a capture, transparently handling the gzipped archive form."""
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8",
                                errors="replace")
    return open(path, encoding="utf-8", errors="replace")

RX_RE = re.compile(r"Rx:\s*<(?P<payload>.*?)>")
START_RE = re.compile(r"Start thread for:\s*(?P<dev>.+?)\s*$")
STOP_RE = re.compile(r"Stopping thread for:\s*(?P<dev>.+?)\s*$")


def load_tx(path):
    sent = []
    if not path:
        return sent
    with _open_text(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            # Only frames the emulator SENT are candidates for what
            # TestController received; inbound command echoes would create
            # false matches.
            if str(rec.get("dir", "")).upper() != "OUT":
                continue
            txt = (rec.get("text") or "").strip()
            if txt:
                sent.append(txt)
    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tc-log", required=True)
    ap.add_argument("--tx", help="emulator <label>-tx.jsonl")
    args = ap.parse_args()

    with _open_text(args.tc_log) as fh:
        lines = fh.read().splitlines()

    sent = load_tx(args.tx)
    sent_set = set(sent)

    # ---- Obs 1: truncation ------------------------------------------------
    exact, truncated, unmatched = 0, [], []
    for ln in lines:
        m = RX_RE.search(ln)
        if not m:
            continue
        p = m.group("payload").strip()
        if not p:
            continue
        if p in sent_set:
            exact += 1
        elif sent and any(s.endswith(p) and s != p for s in sent_set):
            src = next(s for s in sent_set if s.endswith(p) and s != p)
            truncated.append((p, src, ln.strip()))
        else:
            unmatched.append((p, ln.strip()))

    # ---- Obs 2: shutdown NPE ---------------------------------------------
    npes = []
    for i, ln in enumerate(lines):
        if "NullPointerException" in ln:
            frame = ""
            for j in range(i + 1, min(i + 6, len(lines))):
                if "\tat " in lines[j] or lines[j].strip().startswith("at "):
                    frame = lines[j].strip()
                    break
            npes.append((i + 1, ln.strip(), frame))

    # ---- Obs 3: early thread exit ----------------------------------------
    started, stopped = [], []
    for ln in lines:
        m = START_RE.search(ln)
        if m:
            started.append(m.group("dev").strip())
        m = STOP_RE.search(ln)
        if m:
            stopped.append(m.group("dev").strip())
    no_answer = sum(1 for ln in lines if "No asnwer" in ln or "No answer" in ln)

    # ---- report -----------------------------------------------------------
    W = 78
    print("=" * W)
    print("  TestController log analysis")
    print("=" * W)
    print(f"  log lines            : {len(lines)}")
    print(f"  emulator frames sent : {len(sent)}" if sent else
          "  emulator frames sent : (no --tx supplied; truncation check limited)")

    print("\n-- Observation 1: truncated reads " + "-" * (W - 34))
    print(f"  Rx payloads matching a sent frame exactly : {exact}")
    print(f"  Rx payloads that are a SUFFIX of a frame  : {len(truncated)}   <-- truncation")
    print(f"  Rx payloads with no matching frame        : {len(unmatched)}")
    for p, src, ln in truncated[:10]:
        print(f"    TRUNCATED  got <{p}>  from sent <{src}>")
    for p, ln in unmatched[:10]:
        print(f"    UNMATCHED  <{p}>")
    if sent:
        # Any payload that does not exactly equal a frame the emulator sent is a
        # corrupted read, whether or not it happens to be a clean suffix. The
        # originally reported examples lost leading bytes; a dropped byte
        # mid-value is the same fault and must not be scored as a pass.
        bad = len(truncated) + len(unmatched)
        if not bad:
            print("  VERDICT: PASS - every read matched the bytes on the wire.")
        else:
            rate = 100.0 * bad / (exact + bad)
            print(f"  VERDICT: FAIL - {bad} corrupted read(s) of {exact + bad} "
                  f"({rate:.2f}%): {len(truncated)} truncated, "
                  f"{len(unmatched)} otherwise mismatched.")

    print("\n-- Observation 2: shutdown NullPointerException " + "-" * (W - 48))
    print(f"  NullPointerExceptions in log : {len(npes)}")
    for lineno, ln, frame in npes[:5]:
        print(f"    line {lineno}: {ln}")
        if frame:
            print(f"      {frame}")
    print("  VERDICT: " + ("PASS - none seen." if not npes else "FAIL - NPE present."))

    print("\n-- Observation 3: device threads stopping early " + "-" * (W - 48))
    print(f"  threads started : {len(started)}")
    for d in started:
        print(f"      start  {d}")
    print(f"  threads stopped : {len(stopped)}")
    for d in stopped:
        print(f"      stop   {d}")
    print(f"  '*IDN? no answer' events : {no_answer}")
    early = [d for d in stopped if d in started]
    print("  VERDICT: " + ("PASS - no thread stopped during the run."
                           if not stopped else
                           f"REVIEW - {len(early)} thread(s) stopped; confirm whether "
                           "this was your shutdown or an early exit."))
    print("=" * W)

    return 1 if (truncated or unmatched or npes) else 0


if __name__ == "__main__":
    sys.exit(main())
