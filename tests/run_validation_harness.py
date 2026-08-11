"""
CLI Build Revision & Protocol Validation Harness Runner (`run_validation_harness.py`)

Executes full hardware protocol assertions (12 checks) and QPS stress benchmarks
against a live or background BenchForge emulator instance.

Usage:
    python tests/run_validation_harness.py [--host HOST] [--prologix-port PORT] [--lxi-port PORT] [--benchmark]
"""

import sys
import os
import argparse
import time

# Ensure core modules are in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.device_emulator import InstrumentRegistry, VirtualInstrument
from core.prologix_emulator import PrologixEmulatorServer
from core.vxi11_lxi_emulator import LXIRawSocketServer
from core.validation_harness import ValidationHarness
from core.performance_tester import PerformanceTester


def run_standalone_validation(host="127.0.0.1", prologix_port=1234, lxi_port=5025, probe_address=1, policy="single_connection", run_benchmarks=False):
    print("=" * 80)
    print("  BenchForge Build Revision & Protocol Validation Harness (CLI Runner)")
    print("=" * 80)

    # Instantiate transient background emulators if not already listening
    registry = InstrumentRegistry()
    dev1 = VirtualInstrument(gpib_address=1, name="Agilent 34401A (Simulated)", idn="HEWLETT-PACKARD,34401A,0,10-1-1")
    dev2 = VirtualInstrument(gpib_address=2, name="Fluke PM6690", idn="FLUKE, PM6690,0,v1.20")
    dev4 = VirtualInstrument(gpib_address=4, name="Keithley 2000 (Simulated)", idn="KEITHLEY INSTRUMENTS INC.,MODEL 2000,1234567,A06/A02")
    registry.set_device(1, dev1)
    registry.set_device(2, dev2)
    registry.set_device(4, dev4)

    server = PrologixEmulatorServer(host=host, port=prologix_port, registry=registry, connection_policy=policy)
    lxi_server = LXIRawSocketServer(host=host, port=lxi_port, registry=registry)

    try:
        server.start()
        lxi_server.start()
        time.sleep(0.1)

        print(f"\n[+] Emulators started on {host} (Prologix Port {prologix_port}, LXI Port {lxi_port})")
        print(f"[*] Running 12 Protocol Assertion Checks (Policy: {policy})...\n")

        harness = ValidationHarness(
            prologix_host=host,
            prologix_port=prologix_port,
            lxi_port=lxi_port,
            connection_policy=policy,
            probe_address=probe_address
        )
        results = harness.run_full_validation_suite()

        passed_count = sum(1 for r in results if r.passed)
        total_count = len(results)
        failed_count = total_count - passed_count

        for r in results:
            icon = "[PASS]" if r.passed else "[FAIL]"
            print(f" {icon} [{r.check_id}] {r.description:<52} -> expected: {r.expected}, got: {r.actual}")
            if not r.passed and r.error_msg:
                print(f"         Error: {r.error_msg}")

        print("\n" + "-" * 80)
        status_str = "SUCCESS" if failed_count == 0 else "FAILED"
        print(f" SUMMARY: [{status_str}] ({passed_count}/{total_count} Protocol Assertion Checks Passed)")
        print("-" * 80)

        if run_benchmarks and failed_count == 0:
            print("\n[*] Running QPS Latency & Throughput Stress Benchmark...")
            tester = PerformanceTester(host=host, port=prologix_port)
            res = tester.run_latency_throughput_test(num_queries=50, address=probe_address)
            print(f"     Throughput: {res.queries_per_second:.1f} QPS | Avg Latency: {res.avg_latency_ms:.2f} ms | P95: {res.p95_latency_ms:.2f} ms")

        return failed_count == 0

    finally:
        server.stop()
        lxi_server.stop()


def main():
    parser = argparse.ArgumentParser(description="BenchForge Build Revision & Protocol Validation CLI Runner")
    parser.add_argument("--host", default="127.0.0.1", help="Binding host IP")
    parser.add_argument("--prologix-port", type=int, default=1234, help="Prologix TCP port")
    parser.add_argument("--lxi-port", type=int, default=5025, help="LXI SCPI TCP port")
    parser.add_argument("--probe-address", type=int, default=1, help="GPIB target address")
    parser.add_argument("--policy", choices=["single_connection", "multi_client"], default="single_connection", help="Socket policy")
    parser.add_argument("--benchmark", action="store_true", help="Run performance stress benchmark")

    args = parser.parse_args()
    success = run_standalone_validation(
        host=args.host,
        prologix_port=args.prologix_port,
        lxi_port=args.lxi_port,
        probe_address=args.probe_address,
        policy=args.policy,
        run_benchmarks=args.benchmark
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
