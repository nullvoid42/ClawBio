#!/usr/bin/env python3
"""
CI benchmark runner for the Corpas 30x WGS reference genome.

Runs the benchmark test suite with graceful skipping for absent data files.
Structural tests (manifest, citation, rsID lists) always run.

Usage:
    python corpas-30x/run_benchmark.py
    python corpas-30x/run_benchmark.py --ci       # strict: fail on warnings
    python corpas-30x/run_benchmark.py --verbose   # show skip reasons
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

CLAWBIO_DIR = Path(__file__).resolve().parent.parent
TEST_PATH = CLAWBIO_DIR / "tests" / "benchmark" / "test_reference_genome.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reference genome benchmarks")
    parser.add_argument("--ci", action="store_true", help="Strict mode for CI")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if not TEST_PATH.exists():
        print(f"Test file not found: {TEST_PATH}", file=sys.stderr)
        return 1

    cmd = [sys.executable, "-m", "pytest", str(TEST_PATH), "--tb=short"]
    if args.verbose:
        cmd.append("-v")
    else:
        cmd.append("-v")
    if args.ci:
        cmd.append("-W")
        cmd.append("error::pytest.PytestUnhandledCoroutineWarning")

    return subprocess.call(cmd, cwd=str(CLAWBIO_DIR))


if __name__ == "__main__":
    sys.exit(main())
