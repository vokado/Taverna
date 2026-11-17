"""Ensure the final-release requirements gate fails closed on missing proof."""

import csv
import subprocess
import sys
from pathlib import Path

COMMIT = "a" * 40
COLUMNS = (
    "req_id", "implementation_status", "test_result", "actual_test_paths",
    "execution_id", "build_commit_hash", "environment", "executed_at", "evidence",
)


def run_gate(matrix: Path, commit: str = COMMIT):
    script = Path(__file__).resolve().parents[1] / "scripts/release_gate.py"
    return subprocess.run(
        [sys.executable, str(script), "--matrix", str(matrix), "--commit", commit],
        capture_output=True,
        text=True,
        check=False,
    )


def make_matrix(path: Path, *, rows: int = 181, bad_row: int | None = None):
    with path.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=COLUMNS)
        writer.writeheader()
        for index in range(rows):
            writer.writerow({
                "req_id": f"QA-{index:03d}",
                "implementation_status": "VERIFIED",
                "test_result": "NOT_RUN" if index == bad_row else "PASS",
                "actual_test_paths": "tests/test_validation.py",
                "execution_id": "native-gate-smoke",
                "build_commit_hash": COMMIT,
                "environment": "test",
                "executed_at": "2026-11-17T00:00:00Z",
                "evidence": "synthetic-fixture",
            })


def test_181_fully_verified_matrix_passes_documentary_gate(tmp_path):
    matrix = tmp_path / "matrix.csv"
    make_matrix(matrix)
    assert run_gate(matrix).returncode == 0


def test_incomplete_matrix_fails_closed(tmp_path):
    matrix = tmp_path / "matrix.csv"
    make_matrix(matrix, bad_row=47)
    result = run_gate(matrix)
    assert result.returncode == 1
    assert "QA-047" in result.stderr


def test_missing_requirement_or_wrong_commit_fails_closed(tmp_path):
    matrix = tmp_path / "matrix.csv"
    make_matrix(matrix, rows=180)
    assert run_gate(matrix).returncode == 1
    make_matrix(matrix)
    assert run_gate(matrix, "b" * 40).returncode == 1
