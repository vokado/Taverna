"""Fail-closed requirements-matrix gate for the final Taverna 1.0 release.

This checks documentary evidence only. A green exit code does NOT replace
Windows 11, LM Studio, GPU, recovery, or full-package acceptance tests.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

EXPECTED_REQUIREMENTS = 181
REQUIRED_COLUMNS = {
    "req_id",
    "implementation_status",
    "test_result",
    "actual_test_paths",
    "execution_id",
    "build_commit_hash",
    "environment",
    "executed_at",
    "evidence",
}


def verify_matrix(path: Path, *, expected_commit: str) -> list[str]:
    errors: list[str] = []
    if not expected_commit or len(expected_commit) != 40 or any(
        char not in "0123456789abcdef" for char in expected_commit.lower()
    ):
        return ["Укажите полный 40-символьный SHA коммита сборки."]
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
                return ["Матрица не содержит обязательных столбцов доказательств."]
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        return [f"Невозможно прочитать матрицу: {exc}"]
    if len(rows) != EXPECTED_REQUIREMENTS:
        errors.append(f"Ожидалось {EXPECTED_REQUIREMENTS} требований, найдено {len(rows)}.")
    ids: set[str] = set()
    for number, row in enumerate(rows, start=2):
        key = (row.get("req_id") or "").strip()
        if not key or key in ids:
            errors.append(f"Строка {number}: пустой или повторяющийся req_id.")
        ids.add(key)
        if row["implementation_status"].strip() != "VERIFIED" or row["test_result"].strip() != "PASS":
            errors.append(f"{key or number}: реализация или проверка не подтверждена.")
            continue
        missing = [
            column for column in (
                "actual_test_paths", "execution_id", "build_commit_hash",
                "environment", "executed_at", "evidence",
            )
            if not (row.get(column) or "").strip()
        ]
        if missing:
            errors.append(f"{key}: отсутствуют доказательства: {', '.join(missing)}.")
        if (row.get("build_commit_hash") or "").strip().lower() != expected_commit.lower():
            errors.append(f"{key}: доказательство относится к другому коммиту.")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка матрицы требований перед релизом")
    parser.add_argument("--matrix", type=Path, default=Path("REQUIREMENTS_TRACEABILITY.csv"))
    parser.add_argument("--commit", required=True, help="Полный SHA проверяемой сборки")
    arguments = parser.parse_args(argv)
    problems = verify_matrix(arguments.matrix, expected_commit=arguments.commit)
    if problems:
        print(f"РЕЛИЗ ЗАБЛОКИРОВАН: {len(problems)} несоответствий.", file=sys.stderr)
        for item in problems[:25]:
            print("- " + item, file=sys.stderr)
        if len(problems) > 25:
            print(f"... ещё {len(problems) - 25}", file=sys.stderr)
        return 1
    print("Матрица подтверждена. Дополнительно обязательна аппаратная и ручная приёмка.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
