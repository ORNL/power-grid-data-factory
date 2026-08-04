#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tarfile
import zipfile
from pathlib import Path

try:
    from grid_data_factory.parsers.go_challenge import (
        candidate_raw_entries_from_names,
        parse_raw_text,
        parse_rop_text,
        resolve_network_root,
        rows_to_matpower_text,
        safe_case_symbol,
    )
    from grid_data_factory.storage.attempt_io import utc_now_iso
except ModuleNotFoundError:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root / "src"))
    from grid_data_factory.parsers.go_challenge import (
        candidate_raw_entries_from_names,
        parse_raw_text,
        parse_rop_text,
        resolve_network_root,
        rows_to_matpower_text,
        safe_case_symbol,
    )
    from grid_data_factory.storage.attempt_io import utc_now_iso


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert GO Challenge PSS/E RAW scenarios into MATPOWER .m files.")
    p.add_argument("--input-root", default="external/go_challenge1/raw", help="Directory with GO Challenge zip archives")
    p.add_argument("--output-root", default="external/go_challenge1/extracted/matpower", help="Directory for generated MATPOWER files")
    p.add_argument("--archive-glob", default="Challenge_1*.zip")
    p.add_argument("--max-cases", type=int, default=0, help="Optional cap for quick smoke runs (0 means no cap)")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--skip-rop", action="store_true", help="Ignore .rop companion files and use default linear costs")
    p.add_argument("--no-rop-limits", action="store_true", help="Do not override RAW generator Pmin/Pmax with ROP dispatch bounds")
    p.add_argument("--report", default="data/reports/go_challenge1_conversion_report.json")
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
            raw_members = candidate_raw_entries_from_names(archive_names)
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
                    parsed = parse_raw_text(raw_text)

                    rop_costs = None
                    rop_member = None
                    if not args.skip_rop:
                        network_root = resolve_network_root(raw_member)
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
                            rop_costs = parse_rop_text(rop_text)

                    case_name = safe_case_symbol(
                        f"go_{archive.stem}_{Path(raw_member).parent.name}_{Path(raw_member).stem}"
                    )
                    matpower_text, stats = rows_to_matpower_text(
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
        "generated_at": utc_now_iso(),
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