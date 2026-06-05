"""Per-target bitmap-cvg trend extractor for /operate/coverage.

AFL++/AFL runs each emit ``outputs/{aflpp,afl}/default/fuzzer_stats``
with a ``bitmap_cvg`` line in fixed-width AFL format. The dashboard
already surfaces a single coverage-report rollup at the top of
/operate/coverage; this module produces the *trend* — bitmap_cvg over
time, grouped by scenario.id (the per-target key from manifest.json).

Trend semantics:
- One series per scenario.id (== per harness target).
- Points ordered by run_id (ISO-8601 timestamp prefix gives stable
  chronology without a parse round-trip).
- For each target: the last value (latest), the peak across the
  series, and the delta between the final two points so the view can
  highlight up/down/flat moves.

The walk is the same one /operate/crashes uses: ADA2_DWARF_RUNS_DIR
overrides the runs root for tests.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


_BITMAP_RE = re.compile(r"^bitmap_cvg\s*:\s*([0-9.]+)\s*%?\s*$", re.M)


def _runs_root() -> Path:
    env = os.environ.get("ADA2_DWARF_RUNS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "dwarf" / "runs"


def _read_bitmap_cvg(path: Path) -> float | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _BITMAP_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _read_scenario_id(run_dir: Path) -> str | None:
    """Pull scenario.id from manifest.json — the per-target key."""
    mp = run_dir / "manifest.json"
    if not mp.is_file():
        return None
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    sc = m.get("scenario") or {}
    sid = sc.get("id")
    return sid if isinstance(sid, str) and sid else None


def _read_started_at(run_dir: Path) -> str | None:
    """ISO-8601 started_at from manifest. Used for human-readable axis
    labels; series ordering still uses run_id for stable sort."""
    mp = run_dir / "manifest.json"
    if not mp.is_file():
        return None
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    s = m.get("started_at")
    return s if isinstance(s, str) else None


def _scan_run(run_dir: Path) -> dict[str, Any] | None:
    """Return one trend point per AFL/AFL++ run, or None if no
    fuzzer_stats with a parseable bitmap_cvg is present."""
    for engine in ("aflpp", "afl"):
        fs = run_dir / "outputs" / engine / "default" / "fuzzer_stats"
        if not fs.is_file():
            continue
        bc = _read_bitmap_cvg(fs)
        if bc is None:
            continue
        sid = _read_scenario_id(run_dir)
        if not sid:
            continue
        return {
            "run_id": run_dir.name,
            "engine": engine,
            "scenario_id": sid,
            "bitmap_cvg": bc,
            "started_at": _read_started_at(run_dir),
        }
    return None


def _short_target_label(scenario_id: str) -> str:
    """Trim the scenario.id into a chart-friendly label.

    Long forms like ``amaru-cargo-fuzz-blockfetch-aflpp-smoke`` and
    ``package-a-parser-protocol-aflpp-tx-body-stage1`` carry redundant
    prefixes; the chart label keeps the salient middle.
    """
    sid = scenario_id
    for prefix in ("amaru-cargo-fuzz-", "package-a-parser-protocol-aflpp-"):
        if sid.startswith(prefix):
            sid = sid[len(prefix):]
            break
    for suffix in ("-aflpp-smoke", "-stage1"):
        if sid.endswith(suffix):
            sid = sid[: -len(suffix)]
            break
    return sid or scenario_id


def coverage_trend_payload() -> dict[str, Any]:
    """Build the render-ready trend payload.

    Returns one entry per scenario.id with:
    - points: chronological [(run_id, bitmap_cvg, engine, started_at), ...]
    - latest, peak, delta (last - prev), direction ('up'|'down'|'flat'|None)
    """
    root = _runs_root()
    if not root.is_dir():
        return {"targets": [], "empty": True, "total_runs": 0, "total_targets": 0}

    by_target: dict[str, list[dict[str, Any]]] = {}
    total_runs = 0
    for p in root.iterdir():
        if not p.is_dir():
            continue
        point = _scan_run(p)
        if point is None:
            continue
        by_target.setdefault(point["scenario_id"], []).append(point)
        total_runs += 1

    targets: list[dict[str, Any]] = []
    for sid, points in by_target.items():
        points.sort(key=lambda x: x["run_id"])
        latest = points[-1]["bitmap_cvg"]
        peak = max(p["bitmap_cvg"] for p in points)
        prev = points[-2]["bitmap_cvg"] if len(points) >= 2 else None
        delta = (latest - prev) if prev is not None else None
        if delta is None:
            direction = None
        elif delta > 0.05:
            direction = "up"
        elif delta < -0.05:
            direction = "down"
        else:
            direction = "flat"
        targets.append({
            "scenario_id": sid,
            "label": _short_target_label(sid),
            "engine": points[-1]["engine"],
            "points": points,
            "point_count": len(points),
            "latest": latest,
            "peak": peak,
            "first": points[0]["bitmap_cvg"],
            "delta": delta,
            "direction": direction,
            "sparkline": _sparkline_svg(points),
        })

    targets.sort(key=lambda t: (-t["latest"], t["label"]))
    return {
        "targets": targets,
        "empty": not targets,
        "total_runs": total_runs,
        "total_targets": len(targets),
    }


def _sparkline_svg(points: list[dict[str, Any]], *, width: int = 220, height: int = 48) -> str:
    """Inline SVG polyline of bitmap_cvg over chronological run order.

    Y-axis: 0–100% (bitmap_cvg is already a percentage). The trend cell
    stays compact; a single point still renders as a centered dot so
    sparse targets aren't visually empty."""
    if not points:
        return ""
    pad_x, pad_y = 4, 4
    inner_w = width - 2 * pad_x
    inner_h = height - 2 * pad_y
    n = len(points)
    if n == 1:
        cx = width / 2
        cy = height / 2
        return (
            f'<svg viewBox="0 0 {width} {height}" class="cvg-spark" '
            f'role="img" aria-label="single point at {points[0]["bitmap_cvg"]:.2f}%">'
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" class="cvg-spark__dot"/>'
            f'</svg>'
        )
    coords = []
    for i, p in enumerate(points):
        x = pad_x + (inner_w * i / (n - 1))
        # Invert Y: 100% at top, 0% at bottom.
        y = pad_y + inner_h * (1 - (p["bitmap_cvg"] / 100.0))
        coords.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(coords)
    last_x, last_y = coords[-1].split(",")
    aria = (
        f"trend {points[0]['bitmap_cvg']:.2f}% to "
        f"{points[-1]['bitmap_cvg']:.2f}% across {n} runs"
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" class="cvg-spark" '
        f'role="img" aria-label="{aria}">'
        f'<polyline points="{poly}" class="cvg-spark__line" '
        f'fill="none" stroke-width="1.5"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="2.5" class="cvg-spark__dot"/>'
        f'</svg>'
    )
