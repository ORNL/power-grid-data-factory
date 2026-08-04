from __future__ import annotations


def adaptive_bin_id(descriptor: dict, bin_width: float = 0.05) -> str:
    sec = int(float(descriptor.get("security_margin", 0.0)) / bin_width)
    v50 = int(float(descriptor.get("voltage_p50", 1.0)) / bin_width)
    b90 = int(float(descriptor.get("branch_loading_p90", 0.0)) / bin_width)
    return f"sec{sec}_v{v50}_b{b90}"
