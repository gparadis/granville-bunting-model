#!/usr/bin/env python3
"""
Granville bunting clearance feasibility model.

All units are meters unless noted.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import argparse
import itertools
import math
import os
import sys
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Inputs:
    # Measurement inputs
    measurement_distance_m: float = 9.10
    measurement_angle_deg: float = 25.0
    eye_height_m: float = 1.83
    # Geometry
    powerline_gap_from_post_m: float = 6.0  # horizontal gap from lamp post to powerline mount
    # Cable geometry
    support_cable_drop_m: float = 0.50
    live_wire_drop_below_support_m: float = 0.15
    # Clearances
    clearance_support_m: float = 0.30
    clearance_live_m: float = 1.00
    min_road_clearance_m: float = 5.0
    road_crown_m: float = 0.15
    # Bunting properties
    bunting_mass_per_m_kg: float = 0.02
    gravity_m_s2: float = 9.81


@dataclass(frozen=True)
class Case:
    name: str
    lamp_post_gap_m: float
    bunting_offset_along_m: float


def bracket_height_m(inp: Inputs) -> float:
    """Height of the support cable bracket on the lamp post."""
    return inp.eye_height_m + math.tan(math.radians(inp.measurement_angle_deg)) * inp.measurement_distance_m


def solve_catenary_a(span_m: float, sag_mid_m: float) -> float:
    """Solve for catenary parameter a given span and midspan sag."""
    if sag_mid_m <= 0:
        return math.inf

    def f(a: float) -> float:
        return a * math.cosh(span_m / (2 * a)) - a - sag_mid_m

    a_low = 1e-9
    a_high = max(1.0, span_m)
    while f(a_high) > 0:
        a_high *= 2.0
        if a_high > 1e12:
            break

    for _ in range(120):
        mid = 0.5 * (a_low + a_high)
        if f(mid) > 0:
            a_low = mid
        else:
            a_high = mid
    return a_high


def catenary_z(span_m: float, sag_mid_m: float, s_m: float) -> float:
    """Height above the lowest point at distance s from midspan."""
    if sag_mid_m <= 0:
        return 0.0
    a = solve_catenary_a(span_m, sag_mid_m)
    return a * math.cosh(s_m / a) - a


def compute_derived(inp: Inputs, case: Case) -> Dict[str, float]:
    bracket = bracket_height_m(inp)
    support_mount = bracket - inp.support_cable_drop_m
    wire = support_mount - inp.live_wire_drop_below_support_m
    min_bunting = inp.min_road_clearance_m + inp.road_crown_m
    span = math.hypot(case.lamp_post_gap_m, case.bunting_offset_along_m)
    t_cross = inp.powerline_gap_from_post_m / case.lamp_post_gap_m
    s_cross = abs(t_cross - 0.5) * span
    w = inp.bunting_mass_per_m_kg * inp.gravity_m_s2

    return {
        "bracket_height_m": bracket,
        "support_mount_height_m": support_mount,
        "wire_height_m": wire,
        "min_bunting_height_m": min_bunting,
        "span_length_m": span,
        "t_cross": t_cross,
        "s_cross_m": s_cross,
        "weight_per_m_N": w,
    }


def best_case_requirements(inp: Inputs, derived: Dict[str, float]) -> Dict[str, float]:
    min_bunting = derived["min_bunting_height_m"]
    required_wire = min_bunting + inp.clearance_live_m
    required_bracket = required_wire + inp.support_cable_drop_m + inp.live_wire_drop_below_support_m
    return {
        "required_wire_height_m": required_wire,
        "required_bracket_height_m": required_bracket,
    }


def height_only_feasible(inp: Inputs, derived: Dict[str, float]) -> bool:
    min_bunting = derived["min_bunting_height_m"]
    max_mount_support = derived["bracket_height_m"] - inp.clearance_support_m
    max_mount_wire = derived["wire_height_m"] - inp.clearance_live_m
    return min_bunting <= min(max_mount_support, max_mount_wire)


def feasible_for_a(inp: Inputs, derived: Dict[str, float], a: float) -> Dict[str, float]:
    span = derived["span_length_m"]
    s_cross = derived["s_cross_m"]
    min_bunting = derived["min_bunting_height_m"]
    bracket = derived["bracket_height_m"]
    wire = derived["wire_height_m"]

    sag = a * math.cosh(span / (2 * a)) - a
    z_cross = a * math.cosh(s_cross / a) - a

    mount_min = min_bunting + sag
    mount_max_support = bracket - inp.clearance_support_m
    mount_max_wire = wire - inp.clearance_live_m + sag - z_cross
    mount_max = min(mount_max_support, mount_max_wire)

    return {
        "sag_m": sag,
        "z_cross_m": z_cross,
        "mount_min_m": mount_min,
        "mount_max_m": mount_max,
        "mount_max_support_m": mount_max_support,
        "mount_max_wire_m": mount_max_wire,
        "feasible": mount_min <= mount_max,
    }


def find_min_tension(inp: Inputs, derived: Dict[str, float]) -> Dict[str, object]:
    if not height_only_feasible(inp, derived):
        return {
            "feasible": False,
            "min_tension_N": None,
            "min_a": None,
            "min_sag_m": None,
            "mount_range": None,
        }

    w = derived["weight_per_m_N"]
    a_min = 0.05
    a_max = 1e6
    n = 400
    log_min = math.log10(a_min)
    log_max = math.log10(a_max)

    best = None
    for i in range(n):
        exp = log_min + (log_max - log_min) * (i / (n - 1))
        a = 10 ** exp
        info = feasible_for_a(inp, derived, a)
        if info["feasible"]:
            tension = a * w
            if best is None or tension < best["min_tension_N"]:
                best = {
                    "feasible": True,
                    "min_tension_N": tension,
                    "min_a": a,
                    "min_sag_m": info["sag_m"],
                    "mount_range": (info["mount_min_m"], info["mount_max_m"]),
                }

    if best is None:
        return {
            "feasible": False,
            "min_tension_N": None,
            "min_a": None,
            "min_sag_m": None,
            "mount_range": None,
        }

    return best


def evaluate_case(inp: Inputs, case: Case) -> Dict[str, object]:
    derived = compute_derived(inp, case)
    req = best_case_requirements(inp, derived)
    tension = find_min_tension(inp, derived)
    verdict = "Not feasible"
    if tension["feasible"]:
        verdict = "Feasible"
    return {
        "case": case,
        "derived": derived,
        "req": req,
        "tension": tension,
        "verdict": verdict,
    }


def angle_sensitivity(inp: Inputs, case: Case, delta_deg: float = 1.0) -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []
    for angle in [inp.measurement_angle_deg - delta_deg, inp.measurement_angle_deg, inp.measurement_angle_deg + delta_deg]:
        variant = replace(inp, measurement_angle_deg=angle)
        derived = compute_derived(variant, case)
        req = best_case_requirements(variant, derived)
        results.append(
            {
                "angle_deg": angle,
                "bracket_height_m": derived["bracket_height_m"],
                "wire_height_m": derived["wire_height_m"],
                "required_bracket_height_m": req["required_bracket_height_m"],
                "bracket_shortfall_m": req["required_bracket_height_m"] - derived["bracket_height_m"],
                "height_only_feasible": height_only_feasible(variant, derived),
            }
        )
    return results


def parameter_ranges(inp: Inputs, case: Case) -> List[Dict[str, object]]:
    return [
        {
            "name": "measurement_distance_m",
            "label": "Measurement distance d (m)",
            "low": inp.measurement_distance_m - 0.25,
            "base": inp.measurement_distance_m,
            "high": inp.measurement_distance_m + 0.25,
        },
        {
            "name": "eye_height_m",
            "label": "Eye height h_e (m)",
            "low": inp.eye_height_m - 0.05,
            "base": inp.eye_height_m,
            "high": inp.eye_height_m + 0.05,
        },
        {
            "name": "support_cable_drop_m",
            "label": "Support cable drop delta_s (m)",
            "low": inp.support_cable_drop_m - 0.10,
            "base": inp.support_cable_drop_m,
            "high": inp.support_cable_drop_m + 0.10,
        },
        {
            "name": "live_wire_drop_below_support_m",
            "label": "Live wire drop delta_w (m)",
            "low": inp.live_wire_drop_below_support_m - 0.05,
            "base": inp.live_wire_drop_below_support_m,
            "high": inp.live_wire_drop_below_support_m + 0.05,
        },
        {
            "name": "lamp_post_gap_m",
            "label": "Lamp post gap D (m)",
            "low": case.lamp_post_gap_m - 1.0,
            "base": case.lamp_post_gap_m,
            "high": case.lamp_post_gap_m + 1.0,
        },
        {
            "name": "bunting_offset_along_m",
            "label": "Along-street offset A (m)",
            "low": case.bunting_offset_along_m - 1.0,
            "base": case.bunting_offset_along_m,
            "high": case.bunting_offset_along_m + 1.0,
        },
        {
            "name": "min_road_clearance_m",
            "label": "Minimum road clearance H_road (m)",
            "low": 4.5,
            "base": inp.min_road_clearance_m,
            "high": 5.5,
        },
    ]


def shortfall_metrics(inp: Inputs, case: Case) -> Dict[str, float]:
    derived = compute_derived(inp, case)
    req = best_case_requirements(inp, derived)
    return {
        "bracket_shortfall_m": req["required_bracket_height_m"] - derived["bracket_height_m"],
        "wire_shortfall_m": req["required_wire_height_m"] - derived["wire_height_m"],
    }


def sweep_parameters(inp: Inputs, case: Case) -> Dict[str, object]:
    params = parameter_ranges(inp, case)
    keys = [p["name"] for p in params]
    levels = [[p["low"], p["base"], p["high"]] for p in params]

    total = 0
    feasible_count = 0
    min_shortfall = math.inf
    max_shortfall = -math.inf
    best_case = None

    for values in itertools.product(*levels):
        kwargs = dict(zip(keys, values))
        variant_inp = replace(inp, **{k: v for k, v in kwargs.items() if hasattr(inp, k)})
        variant_case = replace(
            case,
            lamp_post_gap_m=kwargs.get("lamp_post_gap_m", case.lamp_post_gap_m),
            bunting_offset_along_m=kwargs.get("bunting_offset_along_m", case.bunting_offset_along_m),
        )
        derived = compute_derived(variant_inp, variant_case)
        req = best_case_requirements(variant_inp, derived)

        shortfall = req["required_bracket_height_m"] - derived["bracket_height_m"]
        total += 1
        if shortfall <= 0:
            feasible_count += 1
        if shortfall < min_shortfall:
            min_shortfall = shortfall
            best_case = {"shortfall_m": shortfall, "params": kwargs}
        if shortfall > max_shortfall:
            max_shortfall = shortfall

    return {
        "total": total,
        "feasible_count": feasible_count,
        "min_shortfall_m": min_shortfall,
        "max_shortfall_m": max_shortfall,
        "best_case": best_case,
    }


def tornado_data(inp: Inputs, case: Case) -> Dict[str, object]:
    params = parameter_ranges(inp, case)
    base_shortfall = shortfall_metrics(inp, case)["bracket_shortfall_m"]
    rows: List[Dict[str, object]] = []
    for p in params:
        low_kwargs = {p["name"]: p["low"]}
        high_kwargs = {p["name"]: p["high"]}

        low_inp = replace(inp, **{k: v for k, v in low_kwargs.items() if hasattr(inp, k)})
        high_inp = replace(inp, **{k: v for k, v in high_kwargs.items() if hasattr(inp, k)})

        low_case = replace(
            case,
            lamp_post_gap_m=low_kwargs.get("lamp_post_gap_m", case.lamp_post_gap_m),
            bunting_offset_along_m=low_kwargs.get("bunting_offset_along_m", case.bunting_offset_along_m),
        )
        high_case = replace(
            case,
            lamp_post_gap_m=high_kwargs.get("lamp_post_gap_m", case.lamp_post_gap_m),
            bunting_offset_along_m=high_kwargs.get("bunting_offset_along_m", case.bunting_offset_along_m),
        )

        low_shortfall = shortfall_metrics(low_inp, low_case)["bracket_shortfall_m"]
        high_shortfall = shortfall_metrics(high_inp, high_case)["bracket_shortfall_m"]
        rows.append(
            {
                "label": p["label"],
                "low_value": p["low"],
                "high_value": p["high"],
                "low_shortfall": low_shortfall,
                "high_shortfall": high_shortfall,
                "low_delta": low_shortfall - base_shortfall,
                "high_delta": high_shortfall - base_shortfall,
            }
        )
    return {"base_shortfall_m": base_shortfall, "rows": rows}


def generate_plots(
    inp: Inputs,
    derived: Dict[str, float],
    tornado: Dict[str, object],
    plot_dir: str,
) -> Dict[str, str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(plot_dir, exist_ok=True)

    span = derived["span_length_m"]
    s_cross = derived["s_cross_m"]
    bracket = derived["bracket_height_m"]
    wire = derived["wire_height_m"]
    min_bunting = derived["min_bunting_height_m"]

    mount_height = bracket - inp.clearance_support_m
    sag_mid = max(0.0, mount_height - min_bunting)

    xs = [(-span / 2.0) + i * span / 200.0 for i in range(201)]
    ys = [mount_height - sag_mid + catenary_z(span, sag_mid, abs(x)) for x in xs]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(xs, ys, label="Bunting profile (base case)")
    ax.axhline(min_bunting, color="green", linestyle="--", label="Minimum road clearance")
    ax.axhline(wire - inp.clearance_live_m, color="red", linestyle="--", label="Live wire clearance limit")
    ax.axhline(bracket - inp.clearance_support_m, color="blue", linestyle=":", label="Max mount height (support clearance)")
    ax.axvline(s_cross, color="gray", linestyle=":", linewidth=1)
    ax.axvline(-s_cross, color="gray", linestyle=":", linewidth=1)
    ax.set_xlabel("Position along span (m, 0 at midspan)")
    ax.set_ylabel("Height above sidewalk (m)")
    ax.set_title("Bunting height vs span position (base geometry)")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    profile_path = os.path.join(plot_dir, "bunting_profile.png")
    fig.tight_layout()
    fig.savefig(profile_path, dpi=150)
    plt.close(fig)

    rows = tornado["rows"]
    labels = [r["label"] for r in rows]
    low_deltas = [r["low_delta"] for r in rows]
    high_deltas = [r["high_delta"] for r in rows]
    y_positions = list(range(len(rows)))

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for idx, (low_delta, high_delta) in enumerate(zip(low_deltas, high_deltas)):
        left = min(low_delta, high_delta)
        right = max(low_delta, high_delta)
        ax.barh(idx, right - left, left=left, color="#4C78A8")
    ax.axvline(0.0, color="black", linewidth=1)
    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Change in bracket shortfall (m) relative to base case")
    ax.set_title("One-at-a-time sensitivity (tornado)")
    ax.grid(True, axis="x", alpha=0.3)
    tornado_path = os.path.join(plot_dir, "sensitivity_tornado.png")
    fig.tight_layout()
    fig.savefig(tornado_path, dpi=150)
    plt.close(fig)

    return {
        "profile": profile_path,
        "tornado": tornado_path,
    }


def run_self_checks(inp: Inputs, case: Case) -> None:
    derived = compute_derived(inp, case)
    span = derived["span_length_m"]
    s_cross = derived["s_cross_m"]
    sag_mid = 1.0
    z_mid = catenary_z(span, sag_mid, 0.0)
    z_end = catenary_z(span, sag_mid, span / 2.0)
    z_cross = catenary_z(span, sag_mid, s_cross)

    if abs(z_mid) > 1e-6:
        raise AssertionError("Catenary midspan height should be zero.")
    if abs(z_end - sag_mid) > 1e-6:
        raise AssertionError("Catenary endpoint height should equal sag_mid.")
    if not (0.0 <= z_cross <= z_end + 1e-9):
        raise AssertionError("Catenary cross height out of bounds.")

    t_cross = derived["t_cross"]
    expected = inp.powerline_gap_from_post_m / case.lamp_post_gap_m
    if abs(t_cross - expected) > 1e-9:
        raise AssertionError("Crossing fraction mismatch.")


def format_float(value: float) -> str:
    return f"{value:.3f}"


def case_summary_row(result: Dict[str, object]) -> Dict[str, str]:
    derived = result["derived"]
    req = result["req"]
    tension = result["tension"]
    shortfall = req["required_wire_height_m"] - derived["wire_height_m"]

    min_tension = "n/a"
    if tension["min_tension_N"] is not None:
        min_tension = f"{tension['min_tension_N']:.1f}"

    return {
        "case": result["case"].name,
        "span_m": format_float(derived["span_length_m"]),
        "wire_height_m": format_float(derived["wire_height_m"]),
        "required_wire_m": format_float(req["required_wire_height_m"]),
        "wire_shortfall_m": format_float(shortfall),
        "verdict": result["verdict"],
        "min_tension_N": min_tension,
    }


def write_report(
    path: str,
    inp: Inputs,
    case_results: List[Dict[str, object]],
    base_case: Case,
    sensitivity: List[Dict[str, object]],
    sweep: Dict[str, object],
    tornado: Dict[str, object],
    plot_paths: Dict[str, str],
    run_command: str,
) -> None:
    base_result = next(res for res in case_results if res["case"].name == base_case.name)
    derived = base_result["derived"]
    req = base_result["req"]

    conclusion_lines = []
    for res in case_results:
        conclusion_lines.append(f"- {res['case'].name}: {res['verdict']}")

    lines = [
        "# Bunting Clearance Feasibility Report",
        "",
        "## Mathematical model",
        "Let the horizontal measurement distance be d, the angle up from horizontal be theta, and eye height be h_e.",
        "The lamp-post bracket height is:",
        "H_b = h_e + d*tan(theta)",
        "The support cable mount height at the powerline attachment point is:",
        "H_s = H_b - delta_s",
        "The live wire height is:",
        "H_w = H_s - delta_w",
        "The minimum allowed bunting height above sidewalk is:",
        "H_min = H_road + H_crown",
        "Let the across-street lamp-post gap be D and the along-street offset be A.",
        "The bunting span length is:",
        "L = sqrt(D^2 + A^2)",
        "The bunting sag is modeled as a symmetric catenary with endpoints at equal height.",
        "With x=0 at midspan, the catenary is:",
        "z(x) = a*cosh(x/a) - a",
        "The endpoint sag is S = z(L/2), and the endpoint (mount) height is H_m.",
        "Thus the midspan (lowest point) height is H_m - S and must satisfy:",
        "H_m - S >= H_min  =>  H_m >= H_min + S",
        "The live wires cross the bunting at fraction t = g/D along the straight-line span,",
        "where g is the horizontal offset from lamp post to the powerline mount. The distance from midspan is:",
        "s = |t - 1/2|*L",
        "The bunting height at the wire crossing is:",
        "H_cross = H_m - S + z(s)",
        "Clearance constraints are enforced as:",
        "H_m <= H_b - C_s",
        "H_cross <= H_w - C_w",
        "The catenary parameter a is related to horizontal tension H and weight per unit length w by:",
        "a = H / w, where w = m_bunting * g_accel.",
        "",
        "## Inputs (meters unless noted)",
        f"- Measurement distance to bracket: {format_float(inp.measurement_distance_m)}",
        f"- Measurement angle: {format_float(inp.measurement_angle_deg)} deg",
        f"- Eye height: {format_float(inp.eye_height_m)}",
        f"- Powerline mount offset from post: {format_float(inp.powerline_gap_from_post_m)}",
        f"- Support cable drop: {format_float(inp.support_cable_drop_m)}",
        f"- Live wire drop below support: {format_float(inp.live_wire_drop_below_support_m)}",
        f"- Clearance to support cable: {format_float(inp.clearance_support_m)}",
        f"- Clearance to live wire: {format_float(inp.clearance_live_m)}",
        f"- Minimum road clearance: {format_float(inp.min_road_clearance_m)}",
        f"- Road crown above sidewalk: {format_float(inp.road_crown_m)}",
        f"- Bunting mass per length: {format_float(inp.bunting_mass_per_m_kg)} kg/m",
        f"- Gravity: {format_float(inp.gravity_m_s2)} m/s^2",
        "",
        "## Case definitions",
        "| Case | Lamp post gap D (m) | Along-street offset A (m) |",
        "| --- | --- | --- |",
    ]

    for res in case_results:
        case = res["case"]
        lines.append(f"| {case.name} | {format_float(case.lamp_post_gap_m)} | {format_float(case.bunting_offset_along_m)} |")

    lines += [
        "",
        "## Case results",
        "| Case | Span L (m) | Wire height H_w (m) | Required wire height (m) | Wire shortfall (m) | Min horizontal tension (N) | Verdict |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]

    for res in case_results:
        row = case_summary_row(res)
        lines.append(
            f"| {row['case']} | {row['span_m']} | {row['wire_height_m']} | {row['required_wire_m']} | {row['wire_shortfall_m']} | {row['min_tension_N']} | {row['verdict']} |"
        )

    lines += [
        "",
        "## Base-case derived geometry (1100/1000)",
        f"- Bracket height: {format_float(derived['bracket_height_m'])} m",
        f"- Support cable height at powerline mount: {format_float(derived['support_mount_height_m'])} m",
        f"- Live wire height: {format_float(derived['wire_height_m'])} m",
        f"- Minimum bunting height above sidewalk: {format_float(derived['min_bunting_height_m'])} m",
        f"- Bunting span length: {format_float(derived['span_length_m'])} m",
        f"- Distance from midspan to live-wire crossing: {format_float(derived['s_cross_m'])} m",
        "",
        "## Feasibility checks (base case)",
        f"- Best-case required live wire height (road clearance + 1.0 m): {format_float(req['required_wire_height_m'])} m",
        f"- Best-case required bracket height: {format_float(req['required_bracket_height_m'])} m",
        f"- Live wire shortfall vs best case: {format_float(req['required_wire_height_m'] - derived['wire_height_m'])} m",
        f"- Bracket height shortfall vs best case: {format_float(req['required_bracket_height_m'] - derived['bracket_height_m'])} m",
        "",
        "## Sensitivity analysis: measurement angle +/-1 deg (base case)",
        "| Angle (deg) | Bracket height (m) | Live wire height (m) | Required bracket height (m) | Bracket shortfall (m) | Height-only feasible |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for row in sensitivity:
        lines.append(
            "| "
            + " | ".join(
                [
                    format_float(row["angle_deg"]),
                    format_float(row["bracket_height_m"]),
                    format_float(row["wire_height_m"]),
                    format_float(row["required_bracket_height_m"]),
                    format_float(row["bracket_shortfall_m"]),
                    str(row["height_only_feasible"]),
                ]
            )
            + " |"
        )

    lines += [
        "",
        "## Multi-parameter sweep (base case, 7 parameters, 3 levels each)",
        f"- Total combinations: {sweep['total']}",
        f"- Feasible combinations (best-case height-only check): {sweep['feasible_count']}",
        f"- Minimum bracket shortfall: {format_float(sweep['min_shortfall_m'])} m",
        f"- Maximum bracket shortfall: {format_float(sweep['max_shortfall_m'])} m",
        "",
        "Best-case parameter set (minimum shortfall):",
    ]

    best_case = sweep.get("best_case") or {}
    best_params = best_case.get("params") or {}
    for key, value in best_params.items():
        lines.append(f"- {key}: {format_float(value)}")
    if best_case:
        lines.append(f"- Shortfall: {format_float(best_case['shortfall_m'])} m")

    tornado_rows = tornado.get("rows", [])
    if tornado_rows:
        ranges = [
            (row["label"], max(row["low_delta"], row["high_delta"]) - min(row["low_delta"], row["high_delta"]))
            for row in tornado_rows
        ]
        most_sensitive = max(ranges, key=lambda item: item[1])
        lines += [
            "",
            "One-at-a-time sensitivity highlight:",
            f"- Largest bracket shortfall range: {most_sensitive[0]} ({format_float(most_sensitive[1])} m)",
        ]

    lines += [
        "",
        "## Sensitivity parameter ranges used and rationale",
        "- Measurement distance d: base +/-0.25 m to reflect tape/pace error; affects bracket height via d*tan(theta).",
        "- Eye height h_e: base +/-0.05 m to reflect stance/grade differences; shifts bracket height directly.",
        "- Support cable drop delta_s: base +/-0.10 m to reflect tension/sag uncertainty; shifts wire height.",
        "- Live wire drop delta_w: base +/-0.05 m to reflect hardware variation; shifts wire height.",
        "- Lamp-post gap D: base +/-1.0 m to reflect block-to-block spacing variance; changes span and crossing location.",
        "- Along-street offset A: base +/-1.0 m to reflect layout constraints; changes span and crossing location.",
        "- Minimum road clearance H_road: 4.5 to 5.5 m to reflect possible requirement changes.",
        "",
        "## Plots (base case)",
        f"![Bunting height profile]({plot_paths['profile']})",
        "",
        f"![Sensitivity tornado]({plot_paths['tornado']})",
        "",
        "## Conclusion",
    ]

    lines.extend(conclusion_lines)

    lines += [
        "",
        "## Assumptions and notes",
        "- The bracket height computed from the angle measurement is the lamp post support cable mount.",
        "- Support cable drop of 0.50 m occurs over the 6.0 m horizontal gap to the powerline mount.",
        "- Live wires are 0.15 m below the support cable at the mount point.",
        "- Clearance requirements are enforced at the exact plan intersections with live wires.",
        "- The multi-parameter sweep uses a best-case height-only check (no catenary feasibility scan).",
        "",
        "## Run log",
        f"- {run_command}",
    ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="Granville bunting clearance model.")
    parser.add_argument("--report", default=None, help="Write a markdown report to this path.")
    parser.add_argument("--self-test", action="store_true", help="Run internal consistency checks.")
    args = parser.parse_args()

    inp = Inputs()
    cases = [
        Case("1100/1000", lamp_post_gap_m=18.9, bunting_offset_along_m=20.0),
        Case("900", lamp_post_gap_m=15.0, bunting_offset_along_m=17.0),
        Case("800", lamp_post_gap_m=12.1, bunting_offset_along_m=15.0),
    ]

    case_results = [evaluate_case(inp, case) for case in cases]

    base_case = cases[0]
    base_derived = compute_derived(inp, base_case)
    sensitivity = angle_sensitivity(inp, base_case, delta_deg=1.0)

    if args.self_test:
        run_self_checks(inp, base_case)
        print("Self-checks passed.")

    if args.report:
        sweep = sweep_parameters(inp, base_case)
        tornado = tornado_data(inp, base_case)
        plot_paths = generate_plots(inp, base_derived, tornado, plot_dir="plots")
        run_command = " ".join([os.path.basename(sys.executable)] + sys.argv)
        write_report(
            args.report,
            inp,
            case_results,
            base_case,
            sensitivity,
            sweep,
            tornado,
            plot_paths,
            run_command,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
