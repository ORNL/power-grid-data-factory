#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_int(token: str, default: int = 0) -> int:
    try:
        return int(float(token.strip()))
    except Exception:  # noqa: BLE001
        return default


def _to_float(token: str, default: float = 0.0) -> float:
    try:
        return float(token.strip())
    except Exception:  # noqa: BLE001
        return default


def _parse_csv_record(line: str) -> list[str]:
    # GO Challenge PSS/E text uses commas with quoted IDs/names.
    return next(csv.reader([line], delimiter=",", quotechar="'", skipinitialspace=True))


def _is_section_terminator(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("0 /"):
        return True
    # Some RAW variants use plain "0" or "0,<comment>" as section terminator.
    try:
        first = _parse_csv_record(stripped)[0].strip()
    except Exception:  # noqa: BLE001
        first = stripped.split(",", 1)[0].strip()
    return _to_int(first, default=1) == 0


def _is_section_end(line: str, section_name: str) -> bool:
    lo = line.strip().lower()
    if lo.startswith("0 /") and f"end {section_name}" in lo:
        return True
    return _is_section_terminator(line)


@dataclass
class ParsedRaw:
    base_mva: float
    bus_rows: list[list[str]]
    load_rows: list[list[str]]
    shunt_rows: list[list[str]]
    gen_rows: list[list[str]]
    branch_rows: list[list[str]]
    transformer_rows: list[tuple[list[str], list[str], list[str], list[str]]]
    skipped_three_winding: int


def _parse_raw_text(raw_text: str) -> ParsedRaw:
    lines = raw_text.splitlines()
    if len(lines) < 4:
        raise ValueError("RAW file too short")

    header_tokens = _parse_csv_record(lines[0])
    if len(header_tokens) < 2:
        raise ValueError("RAW header missing baseMVA")
    base_mva = _to_float(header_tokens[1], 100.0)

    idx = 3

    def collect_until(end_name: str) -> list[list[str]]:
        nonlocal idx
        rows: list[list[str]] = []
        while idx < len(lines) and not _is_section_end(lines[idx], end_name):
            raw = lines[idx].strip()
            if raw and not raw.startswith("@"):
                rows.append(_parse_csv_record(raw))
            idx += 1
        if idx >= len(lines):
            raise ValueError(f"Could not find end marker for section: {end_name}")
        idx += 1
        return rows

    bus_rows = collect_until("bus section")
    load_rows = collect_until("load section")
    shunt_rows = collect_until("fixed shunt section")
    gen_rows = collect_until("generator section")
    branch_rows = collect_until("non-transformer branch section")

    # Transformer records are 4-line blocks in this GO dataset.
    transformer_rows: list[tuple[list[str], list[str], list[str], list[str]]] = []
    skipped_three_winding = 0
    while idx < len(lines) and not _is_section_end(lines[idx], "transformer section"):
        rec1 = lines[idx].strip()
        rec2 = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
        rec3 = lines[idx + 2].strip() if idx + 2 < len(lines) else ""
        rec4 = lines[idx + 3].strip() if idx + 3 < len(lines) else ""
        if not rec1 or not rec2 or not rec3 or not rec4:
            raise ValueError("Malformed transformer block in RAW file")
        r1 = _parse_csv_record(rec1)
        r2 = _parse_csv_record(rec2)
        r3 = _parse_csv_record(rec3)
        r4 = _parse_csv_record(rec4)
        k_bus = _to_int(r1[2], 0) if len(r1) > 2 else 0
        if k_bus != 0:
            skipped_three_winding += 1
        else:
            transformer_rows.append((r1, r2, r3, r4))
        idx += 4

    if idx >= len(lines):
        raise ValueError("Could not find end marker for transformer section")

    return ParsedRaw(
        base_mva=base_mva,
        bus_rows=bus_rows,
        load_rows=load_rows,
        shunt_rows=shunt_rows,
        gen_rows=gen_rows,
        branch_rows=branch_rows,
        transformer_rows=transformer_rows,
        skipped_three_winding=skipped_three_winding,
    )


@dataclass
class RopCostMaps:
    gen_dispatch: dict[tuple[int, str], int]
    active_dispatch: dict[int, tuple[float, float, int]]
    pwl_tables: dict[int, list[tuple[float, float]]]


def _normalize_id(token: str) -> str:
    return token.strip().strip("'").strip()


def _parse_rop_text(rop_text: str) -> RopCostMaps:
    gen_dispatch: dict[tuple[int, str], int] = {}
    active_dispatch: dict[int, tuple[float, float, int]] = {}
    pwl_tables: dict[int, list[tuple[float, float]]] = {}

    state = "other"
    lines = rop_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        upper = line.upper()

        if "BEGIN GENERATOR DISPATCH DATA" in upper:
            state = "gen_dispatch"
            i += 1
            continue
        if "END GENERATOR DISPATCH DATA BEGIN ACTIVE POWER DISPATCH TABLE DATA" in upper:
            state = "active_dispatch"
            i += 1
            continue
        if "END ACTIVE POWER DISPATCH TABLE DATA" in upper:
            state = "other"
            i += 1
            continue
        if "BEGIN PIECE-WISE LINEAR COST TABLES" in upper:
            state = "pwl"
            i += 1
            continue
        if "END PIECE-WISE LINEAR COST TABLES" in upper:
            state = "other"
            i += 1
            continue

        if not line or line.startswith("0 /"):
            i += 1
            continue

        if state == "gen_dispatch":
            fields = _parse_csv_record(line)
            if len(fields) >= 4:
                bus = _to_int(fields[0], -1)
                gid = _normalize_id(fields[1])
                disp_tbl = _to_int(fields[3], -1)
                if bus > 0 and disp_tbl > 0:
                    gen_dispatch[(bus, gid)] = disp_tbl
            i += 1
            continue

        if state == "active_dispatch":
            fields = _parse_csv_record(line)
            if len(fields) >= 7:
                tbl_id = _to_int(fields[0], -1)
                pmax = _to_float(fields[1], 0.0)
                pmin = _to_float(fields[2], 0.0)
                cost_tbl = _to_int(fields[6], -1)
                if tbl_id > 0 and cost_tbl > 0:
                    active_dispatch[tbl_id] = (pmax, pmin, cost_tbl)
            i += 1
            continue

        if state == "pwl":
            hdr = _parse_csv_record(line)
            if len(hdr) < 3:
                i += 1
                continue
            tbl_id = _to_int(hdr[0], -1)
            npts = _to_int(hdr[2], 0)
            points: list[tuple[float, float]] = []
            for k in range(npts):
                if i + 1 + k >= len(lines):
                    break
                pt_line = lines[i + 1 + k].strip()
                if not pt_line or pt_line.startswith("0 /"):
                    break
                pt = _parse_csv_record(pt_line)
                if len(pt) >= 2:
                    points.append((_to_float(pt[0]), _to_float(pt[1])))
            if tbl_id > 0 and points:
                pwl_tables[tbl_id] = points
            i += 1 + npts
            continue

        i += 1

    return RopCostMaps(gen_dispatch=gen_dispatch, active_dispatch=active_dispatch, pwl_tables=pwl_tables)


def _piecewise_to_quadratic(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    if not points:
        return 0.0, 1.0, 0.0
    if len(points) == 1:
        return 0.0, 0.0, points[0][1]

    x1, y1 = points[0]
    x2, y2 = points[-1]
    if abs(x2 - x1) < 1.0e-9:
        return 0.0, 0.0, y1

    c1 = (y2 - y1) / (x2 - x1)
    c0 = y1 - c1 * x1
    return 0.0, c1, c0


def _format_number(v: float | int) -> str:
    if isinstance(v, int):
        return str(v)
    if abs(v) < 1.0e-12:
        v = 0.0
    text = f"{v:.10g}"
    if text == "-0":
        return "0"
    return text


def _rows_to_matpower_text(
    case_name: str,
    parsed: ParsedRaw,
    rop_costs: RopCostMaps | None,
    use_rop_limits: bool,
) -> tuple[str, dict[str, int]]:
    load_pd: dict[int, float] = {}
    load_qd: dict[int, float] = {}
    shunt_gs: dict[int, float] = {}
    shunt_bs: dict[int, float] = {}

    for row in parsed.load_rows:
        if len(row) < 7:
            continue
        status = _to_int(row[2], 0)
        if status <= 0:
            continue
        bus = _to_int(row[0], -1)
        if bus <= 0:
            continue
        load_pd[bus] = load_pd.get(bus, 0.0) + _to_float(row[5], 0.0)
        load_qd[bus] = load_qd.get(bus, 0.0) + _to_float(row[6], 0.0)

    for row in parsed.shunt_rows:
        if len(row) < 5:
            continue
        status = _to_int(row[2], 0)
        if status <= 0:
            continue
        bus = _to_int(row[0], -1)
        if bus <= 0:
            continue
        shunt_gs[bus] = shunt_gs.get(bus, 0.0) + _to_float(row[3], 0.0)
        shunt_bs[bus] = shunt_bs.get(bus, 0.0) + _to_float(row[4], 0.0)

    bus_rows_out: list[list[float | int]] = []
    for row in parsed.bus_rows:
        if len(row) < 13:
            continue
        bus = _to_int(row[0], -1)
        if bus <= 0:
            continue
        ide = _to_int(row[3], 1)
        btype = ide if ide in {1, 2, 3, 4} else 1
        area = _to_int(row[4], 1)
        zone = _to_int(row[5], area)
        vm = _to_float(row[7], 1.0)
        va = _to_float(row[8], 0.0)
        nvhi = _to_float(row[9], 1.05)
        nvlo = _to_float(row[10], 0.95)
        base_kv = _to_float(row[2], 0.0)

        bus_rows_out.append(
            [
                bus,
                btype,
                load_pd.get(bus, 0.0),
                load_qd.get(bus, 0.0),
                shunt_gs.get(bus, 0.0),
                shunt_bs.get(bus, 0.0),
                area,
                vm,
                va,
                base_kv,
                zone,
                nvhi,
                nvlo,
            ]
        )

    gen_rows_out: list[list[float | int]] = []
    gencost_rows_out: list[list[float | int]] = []
    for row in parsed.gen_rows:
        if len(row) < 18:
            continue
        bus = _to_int(row[0], -1)
        if bus <= 0:
            continue
        gid = _normalize_id(row[1])
        pg = _to_float(row[2], 0.0)
        qg = _to_float(row[3], 0.0)
        qmax = _to_float(row[4], 0.0)
        qmin = _to_float(row[5], 0.0)
        vg = _to_float(row[6], 1.0)
        mbase = _to_float(row[8], parsed.base_mva)
        status = _to_int(row[14], 1)
        pmax = _to_float(row[16], 0.0)
        pmin = _to_float(row[17], 0.0)

        coeff = (0.0, 1.0, 0.0)
        if rop_costs is not None:
            disp_id = rop_costs.gen_dispatch.get((bus, gid))
            if disp_id is not None:
                dispatch_rec = rop_costs.active_dispatch.get(disp_id)
                if dispatch_rec is not None:
                    rop_pmax, rop_pmin, tbl_id = dispatch_rec
                    if use_rop_limits:
                        pmax = rop_pmax
                        pmin = rop_pmin
                    points = rop_costs.pwl_tables.get(tbl_id)
                    if points:
                        coeff = _piecewise_to_quadratic(points)

        gen_rows_out.append(
            [
                bus,
                pg,
                qg,
                qmax,
                qmin,
                vg,
                mbase,
                status,
                pmax,
                pmin,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
            ]
        )
        gencost_rows_out.append([2, 0, 0, 3, coeff[0], coeff[1], coeff[2]])

    branch_rows_out: list[list[float | int]] = []
    for row in parsed.branch_rows:
        if len(row) < 14:
            continue
        fbus = _to_int(row[0], -1)
        tbus = _to_int(row[1], -1)
        if fbus <= 0 or tbus <= 0:
            continue
        r = _to_float(row[3], 0.0)
        x = _to_float(row[4], 0.0)
        b = _to_float(row[5], 0.0)
        rate_a = _to_float(row[6], 0.0)
        rate_b = _to_float(row[7], rate_a)
        rate_c = _to_float(row[8], rate_b)
        status = _to_int(row[13], 1)
        branch_rows_out.append([fbus, tbus, r, x, b, rate_a, rate_b, rate_c, 0, 0, status, -360, 360])

    for rec1, rec2, rec3, rec4 in parsed.transformer_rows:
        if len(rec1) < 12 or len(rec2) < 3 or len(rec3) < 6:
            continue
        fbus = _to_int(rec1[0], -1)
        tbus = _to_int(rec1[1], -1)
        if fbus <= 0 or tbus <= 0:
            continue

        status = _to_int(rec1[11], 1)
        r = _to_float(rec2[0], 0.0)
        x = _to_float(rec2[1], 0.0)
        b = 0.0

        windv1 = _to_float(rec3[0], 1.0) if len(rec3) > 0 else 1.0
        ang1 = _to_float(rec3[2], 0.0) if len(rec3) > 2 else 0.0
        rate_a = _to_float(rec3[3], 0.0) if len(rec3) > 3 else 0.0
        rate_b = _to_float(rec3[4], rate_a) if len(rec3) > 4 else rate_a
        rate_c = _to_float(rec3[5], rate_b) if len(rec3) > 5 else rate_b
        windv2 = _to_float(rec4[0], 1.0) if len(rec4) > 0 else 1.0

        ratio = 0.0
        if abs(windv2) > 1.0e-9:
            ratio_val = windv1 / windv2
            if abs(ratio_val - 1.0) > 1.0e-9:
                ratio = ratio_val

        branch_rows_out.append([fbus, tbus, r, x, b, rate_a, rate_b, rate_c, ratio, ang1, status, -360, 360])

    lines: list[str] = []
    lines.append(f"function mpc = {case_name}")
    lines.append("% Auto-generated from GO Challenge PSS/E RAW scenario data")
    lines.append(f"% generated_at: {_now()}")
    lines.append("mpc.version = '2';")
    lines.append(f"mpc.baseMVA = {_format_number(parsed.base_mva)};")
    lines.append("")

    def emit_matrix(field: str, rows: list[list[float | int]]) -> None:
        lines.append(f"mpc.{field} = [")
        for row in rows:
            rendered = "\t".join(_format_number(v) for v in row)
            lines.append(f"\t{rendered};")
        lines.append("];")
        lines.append("")

    emit_matrix("bus", bus_rows_out)
    emit_matrix("gen", gen_rows_out)
    emit_matrix("branch", branch_rows_out)
    emit_matrix("gencost", gencost_rows_out)

    stats = {
        "buses": len(bus_rows_out),
        "generators": len(gen_rows_out),
        "branches": len(branch_rows_out),
        "transformers_as_branches": len(parsed.transformer_rows),
        "skipped_three_winding_transformers": parsed.skipped_three_winding,
    }
    return "\n".join(lines) + "\n", stats


def _candidate_raw_entries(zf: zipfile.ZipFile) -> list[str]:
    return sorted([name for name in zf.namelist() if name.lower().endswith(".raw") and not name.endswith("/")])


def _candidate_raw_entries_from_names(names: list[str]) -> list[str]:
    return sorted([name for name in names if name.lower().endswith(".raw") and not name.endswith("/")])


def _resolve_network_root(raw_member_path: str) -> Path:
    p = Path(raw_member_path)
    if any(part.lower().startswith("scenario_") for part in p.parts):
        return p.parent.parent
    return p.parent


def _safe_case_symbol(name: str) -> str:
    cleaned = [ch if (ch.isalnum() or ch == "_") else "_" for ch in name]
    out = "".join(cleaned).strip("_")
    if not out:
        out = "case_go"
    if out[0].isdigit():
        out = f"case_{out}"
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert GO Challenge PSS/E RAW scenarios into MATPOWER .m files.")
    p.add_argument("--input-root", default="external/go_challenge1/raw", help="Directory with GO Challenge zip archives")
    p.add_argument("--output-root", default="external/go_challenge1/extracted/matpower", help="Directory for generated MATPOWER files")
    p.add_argument("--archive-glob", default="Challenge_1*.zip")
    p.add_argument("--max-cases", type=int, default=0, help="Optional cap for quick smoke runs (0 means no cap)")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--skip-rop", action="store_true", help="Ignore .rop companion files and use default linear costs")
    p.add_argument("--no-rop-limits", action="store_true", help="Do not override RAW generator Pmin/Pmax with ROP dispatch bounds")
    p.add_argument("--report", default="data/analysis/go_challenge1_conversion_report.json")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    input_root = (repo_root / args.input_root).resolve()
    output_root = (repo_root / args.output_root).resolve()
    report_path = (repo_root / args.report).resolve()

    if not input_root.exists():
        raise SystemExit(f"input-root not found: {input_root}")

    archives = sorted(input_root.glob(args.archive_glob))
    if not archives:
        raise SystemExit(f"no archives found under {input_root} matching {args.archive_glob}")

    converted = 0
    skipped_existing = 0
    failed = 0
    invalid_archives: list[dict[str, str]] = []
    records: list[dict[str, object]] = []

    for archive in archives:
        archive_mode = None
        if zipfile.is_zipfile(archive):
            archive_mode = "zip"
        elif tarfile.is_tarfile(archive):
            archive_mode = "tar"
        else:
            invalid_archives.append({"archive": str(archive), "error": "unsupported_archive"})
            continue

        if archive_mode == "zip":
            arc = zipfile.ZipFile(archive)
            archive_names = arc.namelist()
        else:
            arc = tarfile.open(archive, "r:*")
            archive_names = [m.name for m in arc.getmembers() if m.isfile()]

        with arc:
            raw_members = _candidate_raw_entries_from_names(archive_names)
            for raw_member in raw_members:
                if args.max_cases > 0 and converted >= args.max_cases:
                    break

                out_member = Path(raw_member).with_suffix(".m")
                out_path = output_root / archive.stem / out_member
                if out_path.exists() and not args.overwrite:
                    skipped_existing += 1
                    continue

                rec: dict[str, object] = {
                    "archive": archive.name,
                    "raw_member": raw_member,
                    "out_file": str(out_path.relative_to(repo_root)),
                    "ok": False,
                }
                try:
                    if archive_mode == "zip":
                        raw_bytes = arc.read(raw_member)
                    else:
                        fh = arc.extractfile(raw_member)
                        if fh is None:
                            raise ValueError(f"Could not read member: {raw_member}")
                        raw_bytes = fh.read()
                    raw_text = raw_bytes.decode("latin-1", errors="ignore")
                    parsed = _parse_raw_text(raw_text)

                    rop_costs = None
                    rop_member = None
                    if not args.skip_rop:
                        network_root = _resolve_network_root(raw_member)
                        rop_member = str(network_root / "case.rop")
                        if rop_member in archive_names:
                            if archive_mode == "zip":
                                rop_bytes = arc.read(rop_member)
                            else:
                                rf = arc.extractfile(rop_member)
                                if rf is None:
                                    rop_bytes = b""
                                else:
                                    rop_bytes = rf.read()
                            rop_text = rop_bytes.decode("latin-1", errors="ignore")
                            rop_costs = _parse_rop_text(rop_text)

                    case_name = _safe_case_symbol(
                        f"go_{archive.stem}_{Path(raw_member).parent.name}_{Path(raw_member).stem}"
                    )
                    matpower_text, stats = _rows_to_matpower_text(
                        case_name=case_name,
                        parsed=parsed,
                        rop_costs=rop_costs,
                        use_rop_limits=not args.no_rop_limits,
                    )

                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(matpower_text, encoding="utf-8")

                    rec["ok"] = True
                    rec["stats"] = stats
                    rec["used_rop"] = rop_costs is not None
                    rec["rop_member"] = rop_member
                    converted += 1
                except Exception as exc:  # noqa: BLE001
                    rec["error"] = f"{type(exc).__name__}: {exc}"
                    failed += 1

                records.append(rec)

            if args.max_cases > 0 and converted >= args.max_cases:
                break

    report = {
        "generated_at": _now(),
        "input_root": str(input_root),
        "output_root": str(output_root),
        "archive_glob": args.archive_glob,
        "max_cases": args.max_cases,
        "skip_rop": bool(args.skip_rop),
        "use_rop_limits": not bool(args.no_rop_limits),
        "archives_seen": [a.name for a in archives],
        "invalid_archives": invalid_archives,
        "converted": converted,
        "skipped_existing": skipped_existing,
        "failed": failed,
        "records": records,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "converted": converted,
                "skipped_existing": skipped_existing,
                "failed": failed,
                "invalid_archives": len(invalid_archives),
                "report": str(report_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()