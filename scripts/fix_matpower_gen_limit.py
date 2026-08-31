#!/usr/bin/env python3.11
"""
Fix MATPOWER .m files that exceed ExaGO's NGEN_AT_BUS_MAX=32 limit.

For each bus with more than MAX_GEN_AT_BUS generators, merges all generators
at that bus into a single equivalent generator (summed Pmin/Pmax/Qmin/Qmax/
Pg/Qg, averaged Vg/mBase).  Also removes any comment lines that contain the
substring "mpc.dcline", which would confuse ExaGO's strstr-based section
parser.

Outputs: {input_stem}_pgdffix.m next to the input file (unless --inplace).

Usage:
    python3.11 scripts/fix_matpower_gen_limit.py external/pglib-opf/pglib_opf_case78484_epigrids.m
    python3.11 scripts/fix_matpower_gen_limit.py external/pglib-opf/pglib_opf_case10192_epigrids.m \\
        external/pglib-opf/pglib_opf_case20758_epigrids.m
"""
import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

MAX_GEN_AT_BUS = 32  # ExaGO's compile-time NGEN_AT_BUS_MAX


def _parse_section(lines: list[str], key: str) -> tuple[int, list[int]]:
    """Return (header_line_idx, [data_line_idxs]) for a MATPOWER matrix section.

    Uses exact word-boundary match (mpc.gen vs mpc.gencost) via regex so that
    searching for 'mpc.gen' does not accidentally match 'mpc.gencost'.
    """
    pattern = re.compile(r'\b' + re.escape(key) + r'\s*=')
    header = None
    data: list[int] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if header is None:
            if pattern.search(s) and '[' in s:
                header = i
        else:
            if s.startswith('%') or not s:
                continue
            if ']' in s:
                break
            clean = re.sub(r'\s*%.*$', '', s)
            parts = re.split(r'\s+', clean)
            if parts and re.match(r'^-?[\d.]+', parts[0]):
                data.append(i)
    return header, data  # type: ignore[return-value]


def _split_row(line: str) -> list[str]:
    """Parse a MATPOWER data row, stripping trailing semicolons and comments."""
    clean = re.sub(r'\s*%.*$', '', line.strip())
    parts = re.split(r'[\s,]+', clean)
    return [p.rstrip(';') for p in parts if p and p != ';']


def fix_case(src: Path, dst: Path) -> bool:
    lines = src.read_text(encoding='utf-8', errors='ignore').splitlines(keepends=True)

    # --- remove comment lines that confuse ExaGO's mpc.dcline strstr check ---
    dcline_trigger = re.compile(r'mpc\.dcline')
    cleaned_lines: list[str] = []
    dcline_comments_removed = 0
    for line in lines:
        s = line.strip()
        if s.startswith('%') and dcline_trigger.search(line):
            cleaned_lines.append(
                line.replace('mpc.dcline', 'mpc_dcline_ignored')
            )
            dcline_comments_removed += 1
        else:
            cleaned_lines.append(line)
    lines = cleaned_lines
    if dcline_comments_removed:
        print(f'  neutralised {dcline_comments_removed} mpc.dcline comment(s)')

    # --- find generator section ---
    gh, gdata = _parse_section(lines, 'mpc.gen')
    if gh is None:
        print('  no mpc.gen section found — skipping')
        return False

    gen_rows = [_split_row(lines[i]) for i in gdata]
    gen_buses = [int(float(r[0])) for r in gen_rows]
    line_to_gidx = {li: gi for gi, li in enumerate(gdata)}

    bus_gen_idxs: dict[int, list[int]] = defaultdict(list)
    for i, b in enumerate(gen_buses):
        bus_gen_idxs[b].append(i)

    over_buses = {b: idxs for b, idxs in bus_gen_idxs.items()
                  if len(idxs) > MAX_GEN_AT_BUS}
    if not over_buses and not dcline_comments_removed:
        print('  nothing to fix')
        return False

    if over_buses:
        print(f'  over-limit buses: { {b: len(v) for b, v in over_buses.items()} }')

    to_remove_g: set[int] = set()
    replacements_g: dict[int, list[str]] = {}
    for bus, idxs in over_buses.items():
        rows = [gen_rows[i] for i in idxs]
        n = max(len(r) for r in rows)
        rows = [r + ['0'] * (n - len(r)) for r in rows]
        merged = list(rows[0])
        for c in [1, 2, 3, 4, 8, 9]:   # Pg,Qg,Qmax,Qmin,Pmax,Pmin: sum
            merged[c] = f'{sum(float(r[c]) for r in rows):.6f}'
        for c in [5, 6]:                # Vg, mBase: average
            merged[c] = f'{sum(float(r[c]) for r in rows) / len(rows):.6f}'
        merged[7] = '1' if any(float(r[7]) for r in rows) else '0'
        replacements_g[idxs[0]] = merged
        for i in idxs[1:]:
            to_remove_g.add(i)

    # --- find gencost section ---
    ch, cdata = _parse_section(lines, 'mpc.gencost')
    to_remove_c: set[int] = set()
    replacements_c: dict[int, list[str]] = {}
    ci_map: dict[int, int] = {}
    if ch is not None:
        print(f'  gencost rows={len(cdata)}, gen rows={len(gen_rows)}')
        if len(cdata) == len(gen_rows):
            cost_rows = [_split_row(lines[i]) for i in cdata]
            ci_map = {li: ci for ci, li in enumerate(cdata)}
            for bus, idxs in over_buses.items():
                crows = [cost_rows[i] for i in idxs]
                nc = max(len(r) for r in crows)
                crows = [r + ['0'] * (nc - len(r)) for r in crows]
                mc = list(crows[0])
                mc[1] = f'{sum(float(r[1]) for r in crows):.6f}'   # startup: sum
                mc[2] = f'{sum(float(r[2]) for r in crows):.6f}'   # shutdown: sum
                for c in range(4, nc):                              # cost coeffs: avg
                    mc[c] = f'{sum(float(r[c]) for r in crows) / len(crows):.6f}'
                replacements_c[idxs[0]] = mc
                for i in idxs[1:]:
                    to_remove_c.add(i)

    # --- rebuild ---
    out: list[str] = []
    for i, line in enumerate(lines):
        if i in line_to_gidx:
            gi = line_to_gidx[i]
            if gi in to_remove_g:
                continue
            if gi in replacements_g:
                out.append('\t' + '\t'.join(replacements_g[gi]) + ';\n')
                continue
        if i in ci_map:
            ci = ci_map[i]
            if ci in to_remove_c:
                continue
            if ci in replacements_c:
                out.append('\t' + '\t'.join(replacements_c[ci]) + ';\n')
                continue
        out.append(line)

    dst.write_text(''.join(out), encoding='utf-8')
    print(f'  wrote {dst}')
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('files', nargs='+', type=Path,
                        help='MATPOWER .m files to fix')
    parser.add_argument('--suffix', default='_pgdffix',
                        help='suffix for output files (default: _pgdffix)')
    args = parser.parse_args()

    for src in args.files:
        if not src.exists():
            print(f'ERROR: {src} not found', file=sys.stderr)
            sys.exit(1)
        dst = src.parent / (src.stem + args.suffix + src.suffix)
        print(f'Processing {src.name} ...')
        fix_case(src, dst)


if __name__ == '__main__':
    main()
