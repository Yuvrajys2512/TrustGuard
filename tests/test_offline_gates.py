"""Run every offline gate under scripts/ as a test.

Each gate is a standalone script that needs no API key and no network. They are
the project's regression checks for the oracle, the probes and the pipelines, so
a non-zero exit from any of them fails the suite.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

GATES = [
    "check_oracle_regression",
    "check_attack1",
    "check_attack3_checker",
    "check_attack4",
    "check_attack5",
    "check_pipeline2",
    "check_probe_ports",
    "check_td_checkers",
    "check_label_routes",
    "check_sweep_path",
]


@pytest.mark.parametrize("gate", GATES)
def test_gate_passes(gate: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / f"{gate}.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"{gate} failed:\n{result.stdout}\n{result.stderr}"
