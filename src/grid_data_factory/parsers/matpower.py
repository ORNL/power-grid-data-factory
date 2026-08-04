"""Shared MATPOWER (.m) case parser.

Single source of truth for turning a MATPOWER file into the canonical case dict
used across every solver entry point. Handles both standard PGLib/MATPOWER
exports (semicolon-terminated rows) and PowerWorld exports (newline-delimited
rows without semicolons, e.g. the TAMU EPIGRIDS bundles).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Mandatory column counts per matrix, used only to reassemble newline-delimited
# rows that wrap across physical lines.
_MIN_COLS = {"bus": 13, "gen": 21, "branch": 13, "gencost": 5}


def to_number(token: str) -> int | float:
    val = float(token)
    if val.is_integer():
        return int(val)
    return val


def parse_matrix(content: str, field: str) -> list[list[int | float]]:
    pattern = re.compile(rf"mpc\.{re.escape(field)}\s*=\s*\[(.*?)\];", re.DOTALL)
    match = pattern.search(content)
    if not match:
        return []

    lines: list[str] = []
    for raw_line in match.group(1).splitlines():
        line = raw_line.split("%", 1)[0].strip()
        if line:
            lines.append(line)

    # Semicolon-terminated rows (standard PGLib/MATPOWER export).
    merged = " ".join(lines)
    if ";" in merged:
        rows: list[list[int | float]] = []
        for chunk in merged.split(";"):
            line = chunk.strip()
            if line:
                rows.append([to_number(tok) for tok in line.split()])
        return rows

    # Newline-delimited rows (PowerWorld exports): each physical line is one
    # matrix row. Accumulate tokens until a row has at least the mandatory
    # column count to tolerate the rare row that wraps across lines.
    min_cols = _MIN_COLS.get(field, 1)
    rows = []
    acc: list[str] = []
    for line in lines:
        acc.extend(line.split())
        if len(acc) >= min_cols:
            rows.append([to_number(tok) for tok in acc])
            acc = []
    if acc:
        rows.append([to_number(tok) for tok in acc])
    return rows


def parse_base_mva(content: str) -> float:
    match = re.search(r"mpc\.baseMVA\s*=\s*([0-9.eE+\-]+)\s*;", content)
    if not match:
        raise ValueError("Could not parse mpc.baseMVA")
    return float(match.group(1))


def gencost_to_quad_triplet(row: list[int | float]) -> list[float]:
    if len(row) < 5:
        return [0.0, 1.0, 0.0]
    model = int(row[0])
    n = int(row[3])
    coeffs = [float(x) for x in row[4 : 4 + n]]

    if model != 2:
        return [0.0, 1.0, 0.0]
    if len(coeffs) >= 3:
        return [coeffs[0], coeffs[1], coeffs[2]]
    if len(coeffs) == 2:
        return [0.0, coeffs[0], coeffs[1]]
    if len(coeffs) == 1:
        return [0.0, 0.0, coeffs[0]]
    return [0.0, 1.0, 0.0]


def parse_matpower_case(case_file: Path, case_id: str) -> dict[str, Any]:
    text = Path(case_file).read_text(encoding="utf-8", errors="ignore")
    base_mva = parse_base_mva(text)

    bus_rows = parse_matrix(text, "bus")
    gen_rows = parse_matrix(text, "gen")
    branch_rows = parse_matrix(text, "branch")
    gencost_rows = parse_matrix(text, "gencost")

    if not bus_rows or not gen_rows or not branch_rows:
        raise ValueError("MATPOWER case missing one of bus/gen/branch sections")

    buses = []
    loads = []
    for row in bus_rows:
        bus_id = str(int(row[0]))
        buses.append(
            {
                "bus_id": bus_id,
                "type": int(row[1]),
                "vm": float(row[7]),
                "va": float(row[8]),
                "vmin": float(row[12]),
                "vmax": float(row[11]),
            }
        )
        pd = float(row[2])
        qd = float(row[3])
        if abs(pd) > 0.0 or abs(qd) > 0.0:
            loads.append(
                {
                    "load_id": f"load_{len(loads) + 1:06d}",
                    "bus_id": bus_id,
                    "pd": pd,
                    "qd": qd,
                }
            )

    generators = []
    for idx, row in enumerate(gen_rows):
        status = int(row[7])
        if status <= 0:
            continue
        cost = gencost_to_quad_triplet(gencost_rows[idx]) if idx < len(gencost_rows) else [0.0, 1.0, 0.0]
        generators.append(
            {
                "gen_id": f"gen_{idx + 1:06d}",
                "bus_id": str(int(row[0])),
                "pmin": float(row[9]),
                "pmax": float(row[8]),
                "qmin": float(row[4]),
                "qmax": float(row[3]),
                "cost": cost,
            }
        )

    branches = []
    for idx, row in enumerate(branch_rows):
        rate_a = float(row[5])
        if rate_a <= 0.0:
            rate_a = 1.0e6
        branches.append(
            {
                "branch_id": f"branch_{idx + 1:06d}",
                "from": str(int(row[0])),
                "to": str(int(row[1])),
                "r": float(row[2]),
                "x": float(row[3]),
                "rate_a": rate_a,
            }
        )

    return {
        "case_id": case_id,
        "base_mva": base_mva,
        "buses": buses,
        "generators": generators,
        "loads": loads,
        "branches": branches,
    }
