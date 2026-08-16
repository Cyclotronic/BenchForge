#!/usr/bin/env python3
"""
Patch a TestController.jar so its Keysight E5810 interface addresses GPIB
instruments, for validating the findings in docs/E5810A_PROTOCOL_GUIDE.md.

What is wrong
-------------
TestController already has the whole E5810 path: SharedInterfaceKeysightE5810
drives LXIInterfaceMulti, which keeps one independent VXI-11 link per GPIB
address. That is the correct architecture for this gateway. Two lines in
LXIInterface.open() defeat it:

  * line 55 puts the GPIB address in the portmapper's GETPORT `port` argument,
    which every portmapper discards -- MEASURED: ports 0, 5, 21, 22 and 99 all
    return the same core port.
  * line 62 hardcodes the create_link device string as "inst0", which this
    gateway refuses with error 3. GPIB instruments are addressed as
    "gpib0,<primary address>".

So the address reaches the gateway in a field that is thrown away, and the
field that would carry it is a constant. The link is never established, the
error code is never checked, and every later call runs against a link that does
not exist -- which is why the failure is silent.

This is a VALIDATION build, not a proposed patch
------------------------------------------------
The edits here are chosen to be small and safe to observe, not to be what
upstream should ship:

  * The device name is derived from scpiPort rather than passed in. That is
    sound only because LXIInterface.setPort() has exactly one caller
    (LXIInterfaceMulti) and LXIInterfaceMulti has exactly one user
    (SharedInterfaceKeysightE5810), so scpiPort != 0 uniquely identifies the
    E5810 path. Upstream should thread a device name through properly.
  * "gpib0" is a literal. It is the SICL interface name and is user-settable on
    the gateway; a real fix should read <SICLInterfaceName> from
    http://<ip>/agilentExtensions.xml.
  * create_link's error code is logged but not acted on. Nulling the interface
    on failure is the right fix and is what should be proposed, but here it
    would turn a silent failure into an NPE, because LXIInterfaceMulti maps the
    interface whatever the outcome. For validation you only need to see the
    code.

Consequently: GPIB address 0 is not supported by this build (it falls back to
"inst0"). Use addresses 1-31 on the test bench.

How it works
------------
Identical in shape to VMSG's tools/patch_testcontroller.py, which is proven on
this jar: decompile the affected classes out of *your* jar, rewrite anchored
patterns, recompile against that same jar with ECJ, and write a new jar with
only those classes replaced. Your original jar is never modified. If an anchor
stops matching because a future build changed the code, it stops rather than
emitting a partly-patched jar.

Requirements
------------
A Java runtime (JRE 8 or newer) plus CFR and ECJ. ECJ is used rather than javac
so that no JDK is needed. --fetch-tools will download both; they are also
already present in ../../VMSG/tools/tc-patch-tools, which is searched first.

Usage
-----
  python patch_testcontroller_e5810.py --check          # report only
  python patch_testcontroller_e5810.py                  # patch
  python patch_testcontroller_e5810.py --jar path/to/TestController.jar

Then, to validate:
  1. python tests/tc_validation/bench_emulator.py --gateway e5810 \
         --label e5810-unpatched
  2. Add to TestController's settingsGPIB.txt:  Keysight E5810|E|127.0.0.1||
     and configure devices as E:1 .. E:5.
  3. java -jar TestController.jar          -> expect create_link 'inst0' error 3
  4. java -jar TestController-e5810.jar    -> expect create_link 'gpib0,N' ok

Step 3 is the negative control and costs nothing. Do not skip it.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

CFR_URL = "https://github.com/leibnitz27/cfr/releases/download/0.152/cfr-0.152.jar"
ECJ_URL = "https://repo1.maven.org/maven2/org/eclipse/jdt/core/compiler/ecj/4.6.1/ecj-4.6.1.jar"
CFR_NAME = "cfr-0.152.jar"
ECJ_NAME = "ecj-4.6.1.jar"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# Searched in order. The VMSG copy already exists on this machine, so the
# common case needs no download.
TOOLS_DIRS = [
    os.path.join(HERE, "tc-patch-tools"),
    os.path.abspath(os.path.join(REPO, os.pardir, "VMSG", "tools", "tc-patch-tools")),
]

# Deliberately NOT patching dk/hkj/main/PopupGpibConfig, which is what would add
# "Keysight E5810" to the interface dropdown. It is a large Swing class full of
# anonymous inner classes -- by far the biggest recompile risk here, for no
# validation value. SharedInterfaceList.addGPIBInterface() already parses the
# type out of settingsGPIB.txt, so add the line by hand instead.
TARGET_CLASSES = [
    "dk/hkj/comm/LXIInterface",
    "dk/hkj/shared/SharedInterfaceKeysightE5810",
]

# (id, package path, source file, description, find, replace, already-applied)
PATCHES = [
    (
        "1-device-string",
        "dk/hkj/comm",
        "LXIInterface.java",
        "Address GPIB instruments as gpib0,<n> instead of the literal inst0",
        re.compile(r"this\.lxirpc\.addParam\(\s*\"inst0\"\s*\)\s*;"),
        "this.lxirpc.addParam(this.tcDeviceName());",
        re.compile(r"this\.lxirpc\.addParam\(\s*this\.tcDeviceName\(\)\s*\)\s*;"),
    ),
    (
        "2-getport-arg",
        "dk/hkj/comm",
        "LXIInterface.java",
        "Stop putting the GPIB address in the portmapper's GETPORT filter",
        re.compile(
            r"(portmapGetport\(\s*395183\s*,\s*1\s*,\s*6\s*,\s*)this\.scpiPort(\s*\))"
        ),
        r"\g<1>0\g<2>",
        re.compile(r"portmapGetport\(\s*395183\s*,\s*1\s*,\s*6\s*,\s*0\s*\)"),
    ),
    (
        "3-log-link-error",
        "dk/hkj/comm",
        "LXIInterface.java",
        "Log create_link's error code, so a refusal is visible rather than silent",
        # Anchored on the pair of guard returns that precede the link id read,
        # which is a distinctive shape in this method.
        re.compile(
            r"(if\s*\(\s*!\s*this\.lxirpc\.decodeAnswer\(\)\s*\)\s*\{\s*return\s*;\s*\}\s*)"
            r"(this\.linkId\s*=\s*this\.lxirpc\.getAnswer\(\s*1\s*\)\s*;)"
        ),
        r'\1this.log("create_link " + this.tcDeviceName() + " -> error "'
        r' + this.lxirpc.getAnswer(0));\n        \2',
        re.compile(r'this\.log\("create_link "\s*\+\s*this\.tcDeviceName\(\)'),
    ),
    (
        "4-device-name-helper",
        "dk/hkj/comm",
        "LXIInterface.java",
        "Add the tcDeviceName() helper the patches above call",
        # Injected after open() closes. `bb.clear()` immediately followed by a
        # closing brace occurs only at the end of open(); every other call to it
        # is followed by more statements. Anchoring on the next method instead
        # would risk landing between an @Override and the method it annotates,
        # and whether CFR emits @Override at all depends on its classpath.
        re.compile(r"(this\.bb\.clear\(\)\s*;\s*\n\s*\})"),
        r"\1\n\n    private String tcDeviceName() {\n"
        "        // scpiPort is set only by LXIInterfaceMulti, which is used only by\n"
        "        // SharedInterfaceKeysightE5810, so a nonzero value means the E5810\n"
        "        // path and nothing else. Address 0 is not reachable this way.\n"
        "        return this.scpiPort > 0 ? \"gpib0,\" + this.scpiPort : \"inst0\";\n"
        "    }",
        re.compile(r"private\s+String\s+tcDeviceName\s*\(\s*\)"),
    ),
    (
        "5-read-null-guard",
        "dk/hkj/comm",
        "LXIInterface.java",
        "Do not pass a null readBin result into the String constructor",
        re.compile(
            r"(byte\[\]\s+(\w+)\s*=\s*this\.readBin\(\s*timeout\s*\)\s*;)\s*"
            r"(String\s+answer\s*=\s*new\s+String\()"
        ),
        r'\1\n        if (\2 == null) {\n            return "";\n        }\n        \3',
        re.compile(r"this\.readBin\(\s*timeout\s*\)\s*;\s*if\s*\(\s*\w+\s*==\s*null\s*\)"),
    ),
    (
        "6-idempotent-interface",
        "dk/hkj/shared",
        "SharedInterfaceKeysightE5810.java",
        "Do not discard the link map on every neededCommInterface() call",
        # The same fix 3.49 applied to PrologixEthernet, AR488Lan and Kofen but
        # not to this class. Without it the LXIInterfaceMulti map -- and every
        # open VXI-11 link in it -- is thrown away without destroy_link, and the
        # gateway leaks links until create_link starts returning error 3.
        re.compile(
            r"(public\s+String\s+neededCommInterface\s*\(\s*\)\s*\{)\s*"
            r"(this\.ci\s*=\s*new\s+LXIInterfaceMulti\([^;]*;)\s*"
            r"(this\.ci\.debugLog\s*=[^;]*;)\s*"
            r"(return\s+null\s*;)"
        ),
        r"\1\n        if (this.ci == null) {\n            \2\n            \3\n        }\n        \4",
        re.compile(
            r"public\s+String\s+neededCommInterface\s*\(\s*\)\s*\{\s*"
            r"if\s*\(\s*this\.ci\s*==\s*null\s*\)"
        ),
    ),
]

DEFAULT_JAR_LOCATIONS = [
    os.path.expanduser("~/Documents/TestController/TestController.jar"),
    os.path.expanduser("~/TestController/TestController.jar"),
    "./TestController.jar",
]


def log(msg):
    print(msg, flush=True)


def die(msg, code=1):
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def have_java():
    try:
        subprocess.run(["java", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def fetch_tools(tools_dir):
    import urllib.request
    os.makedirs(tools_dir, exist_ok=True)
    for name, url in ((CFR_NAME, CFR_URL), (ECJ_NAME, ECJ_URL)):
        dest = os.path.join(tools_dir, name)
        if os.path.exists(dest):
            log(f"  already present: {name}")
            continue
        log(f"  downloading {name} from {url}")
        urllib.request.urlretrieve(url, dest)
        log(f"  saved {dest} ({os.path.getsize(dest):,} bytes)")


def locate_tools(explicit):
    """Returns (cfr, ecj). Searches --tools-dir, then the known locations."""
    candidates = [explicit] if explicit else list(TOOLS_DIRS)
    for d in candidates:
        cfr = os.path.join(d, CFR_NAME)
        ecj = os.path.join(d, ECJ_NAME)
        if os.path.isfile(cfr) and os.path.isfile(ecj):
            return cfr, ecj, d
    die("CFR and ECJ not found. Looked in:\n  " + "\n  ".join(candidates) +
        "\nRun with --fetch-tools to download them.")


def locate_jar(explicit):
    if explicit:
        if not os.path.isfile(explicit):
            die(f"jar not found: {explicit}")
        return os.path.abspath(explicit)
    for cand in DEFAULT_JAR_LOCATIONS:
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    die("could not find TestController.jar - pass --jar with its path")


def decompile(cfr, jar, workdir):
    classes_dir = os.path.join(workdir, "classes")
    src_dir = os.path.join(workdir, "src")
    os.makedirs(classes_dir, exist_ok=True)

    with zipfile.ZipFile(jar) as z:
        names = set(z.namelist())
        wanted = []
        for cls in TARGET_CLASSES:
            entry = cls + ".class"
            if entry not in names:
                die(f"{entry} is not in this jar - is it really a TestController build?")
            wanted.append(entry)
            # Inner classes must sit beside their outer class or the decompiled
            # source will reference types it never defines.
            wanted.extend(n for n in names
                          if n.startswith(cls + "$") and n.endswith(".class"))

        for entry in wanted:
            target = os.path.join(classes_dir, *entry.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(z.read(entry))

    for cls in TARGET_CLASSES:
        cls_path = os.path.join(classes_dir, *(cls + ".class").split("/"))
        # --extraclasspath matters: without the jar, CFR cannot resolve the
        # supertypes and silently omits @Override annotations, which both
        # changes the source it emits and makes anchors that mention them
        # unreliable. It also improves generic and cast fidelity generally.
        res = subprocess.run(
            ["java", "-jar", cfr, cls_path, "--extraclasspath", jar,
             "--outputdir", src_dir],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            die(f"decompiling {cls} failed:\n{res.stdout}\n{res.stderr}")
    return src_dir


def apply_patches(src_dir, check_only):
    results, failures = [], []
    # Patches to one file must be applied in order and written once, so group
    # by file rather than reading and writing per patch.
    texts = {}

    for pid, pkg, filename, desc, pattern, replacement, applied in PATCHES:
        path = os.path.join(src_dir, *pkg.split("/"), filename)
        if path not in texts:
            if not os.path.isfile(path):
                failures.append((pid, desc,
                                 f"{pkg}/{filename} was not produced by the decompiler"))
                continue
            with open(path, "r", encoding="utf-8") as fh:
                texts[path] = fh.read()

        new_text, count = pattern.subn(replacement, texts[path], count=1)
        if count == 0:
            if applied.search(texts[path]):
                results.append((pid, desc, "already applied - skipped"))
            else:
                failures.append((pid, desc,
                                 "anchor pattern not found (code may have changed)"))
            continue
        texts[path] = new_text
        results.append((pid, desc, "applied"))

    if not check_only and not failures:
        for path, text in texts.items():
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)

    return results, failures


def compile_sources(ecj, jar, src_dir, workdir):
    out_dir = os.path.join(workdir, "out")
    os.makedirs(out_dir, exist_ok=True)
    sources = []
    for root, _, files in os.walk(src_dir):
        sources.extend(os.path.join(root, f) for f in files if f.endswith(".java"))
    if not sources:
        die("no decompiled sources to compile")

    res = subprocess.run(
        ["java", "-jar", ecj, "-source", "1.8", "-target", "1.8", "-nowarn",
         "-cp", jar, "-d", out_dir] + sources,
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        die("recompilation failed. The decompiled source may need a manual "
            f"touch-up for this TestController version:\n{res.stdout}\n{res.stderr}")
    return out_dir


def repackage(jar, out_dir, output_jar):
    replacements = {}
    for root, _, files in os.walk(out_dir):
        for fn in files:
            if fn.endswith(".class"):
                full = os.path.join(root, fn)
                entry = os.path.relpath(full, out_dir).replace(os.sep, "/")
                replacements[entry] = full

    replaced, missing = [], []
    with zipfile.ZipFile(jar) as zin, \
         zipfile.ZipFile(output_jar, "w", zipfile.ZIP_DEFLATED) as zout:
        jar_entries = set(zin.namelist())
        for entry in replacements:
            if entry not in jar_entries:
                missing.append(entry)
        for item in zin.infolist():
            if item.filename in replacements:
                zout.write(replacements[item.filename], item.filename)
                replaced.append(item.filename)
            else:
                zout.writestr(item, zin.read(item.filename))

    if missing:
        die("compiled classes have no counterpart in the jar: " + ", ".join(missing))
    return replaced


def main():
    ap = argparse.ArgumentParser(
        description="Patch TestController.jar to address GPIB instruments "
                    "through an E5810 gateway",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--jar", help="path to TestController.jar (auto-detected if omitted)")
    ap.add_argument("--output",
                    help="output jar (default: TestController-e5810.jar beside the input)")
    ap.add_argument("--tools-dir", help="where CFR and ECJ live")
    ap.add_argument("--fetch-tools", action="store_true",
                    help="download CFR and ECJ into tools/tc-patch-tools, then exit")
    ap.add_argument("--check", action="store_true",
                    help="report which patches would apply; write nothing")
    ap.add_argument("--keep-work", action="store_true",
                    help="keep the temporary work directory for inspection")
    args = ap.parse_args()

    log("TestController E5810 validation patcher")
    log("=" * 62)

    if args.fetch_tools:
        target = args.tools_dir or TOOLS_DIRS[0]
        log(f"Fetching tools into {target}")
        log("  CFR  - github.com/leibnitz27/cfr (MIT)")
        log("  ECJ  - Eclipse JDT batch compiler (EPL)")
        fetch_tools(target)
        log("\nTools ready. Now run without --fetch-tools to patch.")
        return

    if not have_java():
        die("no 'java' on PATH. A JRE 8 or newer is required.")

    cfr, ecj, tools_dir = locate_tools(args.tools_dir)
    jar = locate_jar(args.jar)
    output_jar = args.output or os.path.join(os.path.dirname(jar),
                                             "TestController-e5810.jar")
    log(f"Tools     : {tools_dir}")
    log(f"Input jar : {jar}")
    log(f"Output jar: {'(none - check mode)' if args.check else output_jar}")

    if os.path.abspath(output_jar) == os.path.abspath(jar):
        die("output jar is the input jar; refusing to overwrite it")

    with zipfile.ZipFile(jar) as z:
        sigs = [n for n in z.namelist()
                if n.startswith("META-INF/") and n.endswith((".SF", ".RSA", ".DSA", ".EC"))]
    if sigs:
        die("this jar is digitally signed; replacing classes would invalidate "
            "the signature. Not proceeding.")

    workdir = tempfile.mkdtemp(prefix="tce5810-")
    try:
        log("\n[1/4] Decompiling affected classes...")
        src_dir = decompile(cfr, jar, workdir)
        log(f"      {len(TARGET_CLASSES)} classes decompiled")

        log("\n[2/4] Applying patches...")
        results, failures = apply_patches(src_dir, args.check)
        for pid, desc, status in results:
            log(f"      [ok]   {pid}: {desc}\n             -> {status}")
        for pid, desc, why in failures:
            log(f"      [FAIL] {pid}: {desc}\n             -> {why}")

        if failures:
            die(f"{len(failures)} patch(es) could not be applied to this build.\n"
                "No output jar was written. The affected methods have likely\n"
                "changed; docs/E5810A_PROTOCOL_GUIDE.md section 5 describes what\n"
                "each edit does so it can be re-derived by hand.")

        if args.check:
            log(f"\nCheck complete: {len(results)} patch(es) would apply cleanly. "
                "Nothing written.")
            return

        log("\n[3/4] Recompiling...")
        out_dir = compile_sources(ecj, jar, src_dir, workdir)
        log("      compiled cleanly")

        log("\n[4/4] Building patched jar...")
        replaced = repackage(jar, out_dir, output_jar)
        log(f"      replaced {len(replaced)} class entries:")
        for entry in sorted(replaced):
            log(f"        {entry}")

        log("\n" + "=" * 62)
        log("Done. Your original jar is untouched.")
        log(f"Run the patched build with:\n  java -jar \"{output_jar}\"")
        log("Add 'debugTime' as an argument for a timestamped protocol trace.")
        log("\nRemember the negative control: run the UNPATCHED jar against the")
        log("emulator first and confirm you see create_link 'inst0' error 3.")
    finally:
        if args.keep_work:
            log(f"\nWork directory kept: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
