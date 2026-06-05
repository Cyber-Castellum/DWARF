#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_yaml(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None


def _parse_float(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _infer_protocol(*, target_id: str | None = None, scenario_id: str | None = None, text: str | None = None) -> str | None:
    for candidate in (target_id, scenario_id, text):
        lowered = str(candidate or "").lower()
        if not lowered:
            continue
        for protocol in (
            "localstatequery",
            "localtxsubmission",
            "localtxmonitor",
            "blockfetch",
            "chainsync",
            "txsubmission",
            "peersharing",
            "keepalive",
            "keep-alive",
            "handshake",
            "ledger",
            "block",
        ):
            if protocol in lowered:
                return "keepalive" if protocol == "keep-alive" else protocol
    return None


def _target_manifest_index(manifests_dir: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    if not manifests_dir.is_dir():
        return index
    for path in manifests_dir.glob("*.yaml"):
        body = _read_yaml(path)
        if not body:
            continue
        target_id = body.get("id")
        if target_id:
            index[str(target_id)] = body
    return index


def _manifest_target_id(manifest: dict[str, Any]) -> str | None:
    target = manifest.get("target") or {}
    return target.get("id") or manifest.get("target_id")


def _manifest_impl(manifest: dict[str, Any]) -> str | None:
    target = manifest.get("target") or {}
    return target.get("implementation") or manifest.get("target_implementation")


def _manifest_scenario_id(manifest: dict[str, Any]) -> str | None:
    scenario = manifest.get("scenario") or {}
    return scenario.get("id") or manifest.get("scenario_id")


def _cargo_fuzz_coverage(sub_output_dir: Path) -> dict[str, int | None]:
    coverage_json = _read_json(sub_output_dir / "coverage" / "coverage.json") or {}
    return {
        "covered_functions": _parse_int(coverage_json.get("covered_functions")),
        "covered_lines": _parse_int(coverage_json.get("covered_lines")),
        "covered_regions": _parse_int(coverage_json.get("covered_regions")),
    }


def _upsert_cell(
    cells: dict[tuple[str, str, str], dict[str, Any]],
    *,
    target_id: str,
    implementation: str,
    protocol: str,
    run_id: str,
    metrics: dict[str, Any],
) -> None:
    key = (target_id, implementation, protocol)
    cell = cells.setdefault(
        key,
        {
            "target_id": target_id,
            "implementation": implementation,
            "protocol": protocol,
            "runs": [],
            "bitmap_cvg_max": None,
            "execs_total": 0,
            "queue_total": 0,
            "saved_crashes_total": 0,
            "saved_hangs_total": 0,
            "property_checks_total": 0,
            "property_failures_total": 0,
            "property_pass_rate": None,
            "covered_functions_max": None,
            "covered_lines_max": None,
            "covered_regions_max": None,
            "coverage_regressed": False,
        },
    )
    cell["runs"].append(run_id)
    for count_key, metric_key in (
        ("execs_total", "exec_count"),
        ("queue_total", "queue_count"),
        ("saved_crashes_total", "saved_crashes"),
        ("saved_hangs_total", "saved_hangs"),
        ("property_checks_total", "properties_run"),
        ("property_failures_total", "properties_failed"),
    ):
        value = _parse_int(metrics.get(metric_key))
        if value is not None:
            cell[count_key] += value
    for max_key, metric_key in (
        ("bitmap_cvg_max", "bitmap_cvg"),
        ("covered_functions_max", "covered_functions"),
        ("covered_lines_max", "covered_lines"),
        ("covered_regions_max", "covered_regions"),
    ):
        value = _parse_float(metrics.get(metric_key))
        if value is None:
            continue
        prior = cell.get(max_key)
        cell[max_key] = value if prior is None or value > prior else prior
    if metrics.get("coverage_regressed"):
        cell["coverage_regressed"] = True
    if cell["property_checks_total"] > 0:
        passed = cell["property_checks_total"] - cell["property_failures_total"]
        cell["property_pass_rate"] = round((passed / cell["property_checks_total"]) * 100.0, 2)


def _collect_aflpp_cells(cells: dict[tuple[str, str, str], dict[str, Any]], *, runs_root: Path, manifest_index: dict[str, dict[str, Any]]) -> None:
    for run_dir in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        summary = _read_json(run_dir / "outputs" / "aflpp" / "summary.json")
        stats_text = (run_dir / "outputs" / "aflpp" / "default" / "fuzzer_stats")
        if not summary or not stats_text.is_file():
            continue
        manifest = _read_json(run_dir / "manifest.json") or {}
        target_id = _manifest_target_id(manifest) or _manifest_scenario_id(manifest) or run_dir.name
        implementation = _manifest_impl(manifest) or "unknown"
        protocol = _infer_protocol(target_id=target_id, scenario_id=_manifest_scenario_id(manifest), text=run_dir.name) or "unknown"
        stats = {}
        for line in stats_text.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            stats[key.strip()] = value.strip()
        _upsert_cell(
            cells,
            target_id=target_id,
            implementation=implementation,
            protocol=protocol,
            run_id=run_dir.name,
            metrics={
                "queue_count": summary.get("queue_count"),
                "saved_crashes": summary.get("crash_count"),
                "saved_hangs": summary.get("hang_count"),
                "exec_count": stats.get("execs_done"),
                "bitmap_cvg": stats.get("bitmap_cvg"),
            },
        )


def _collect_cargo_fuzz_cells(cells: dict[tuple[str, str, str], dict[str, Any]], *, runs_root: Path, manifest_index: dict[str, dict[str, Any]]) -> None:
    for run_dir in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        summary = _read_json(run_dir / "outputs" / "cargo-fuzz" / "summary.json")
        if not summary:
            continue
        manifest = _read_json(run_dir / "manifest.json") or {}
        target_id = _manifest_target_id(manifest) or _manifest_scenario_id(manifest) or run_dir.name
        implementation = _manifest_impl(manifest) or "unknown"
        protocol = _infer_protocol(target_id=target_id, scenario_id=_manifest_scenario_id(manifest), text=run_dir.name) or "unknown"
        stats = summary.get("libfuzzer_stats") or {}
        coverage = _cargo_fuzz_coverage(run_dir / "outputs" / "cargo-fuzz")
        _upsert_cell(
            cells,
            target_id=target_id,
            implementation=implementation,
            protocol=protocol,
            run_id=run_dir.name,
            metrics={
                "queue_count": summary.get("queue_count"),
                "saved_crashes": summary.get("crash_count"),
                "saved_hangs": summary.get("hang_count"),
                "exec_count": stats.get("number_of_executed_units", stats.get("number_of_executed_units_estimate")),
                "covered_functions": coverage["covered_functions"],
                "covered_lines": coverage["covered_lines"],
                "covered_regions": coverage["covered_regions"],
            },
        )


def _collect_campaign_cells(cells: dict[tuple[str, str, str], dict[str, Any]], *, runs_root: Path, manifest_index: dict[str, dict[str, Any]]) -> None:
    for run_dir in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        report = _read_json(run_dir / "outputs" / "fuzz-campaign" / "campaign-report.json")
        if not report:
            continue
        manifest = _read_json(run_dir / "manifest.json") or {}
        implementation = _manifest_impl(manifest) or "amaru"
        for subcampaign in report.get("subcampaigns") or []:
            target_id = str(subcampaign.get("id") or run_dir.name)
            protocol = _infer_protocol(target_id=target_id, scenario_id=_manifest_scenario_id(manifest), text=target_id) or "unknown"
            sub_output_dir = Path(str(subcampaign.get("sub_output_dir") or ""))
            coverage = _cargo_fuzz_coverage(sub_output_dir) if subcampaign.get("engine") == "cargo-fuzz" else {
                "covered_functions": None,
                "covered_lines": None,
                "covered_regions": None,
            }
            _upsert_cell(
                cells,
                target_id=target_id,
                implementation=implementation,
                protocol=protocol,
                run_id=run_dir.name,
                metrics={
                    "queue_count": subcampaign.get("queue_count"),
                    "saved_crashes": subcampaign.get("crash_count"),
                    "saved_hangs": subcampaign.get("hang_count"),
                    "exec_count": (subcampaign.get("stats") or {}).get(
                        "number_of_executed_units",
                        (subcampaign.get("stats") or {}).get("execs_done"),
                    ),
                    "bitmap_cvg": (subcampaign.get("stats") or {}).get("bitmap_cvg"),
                    "covered_functions": coverage["covered_functions"],
                    "covered_lines": coverage["covered_lines"],
                    "covered_regions": coverage["covered_regions"],
                },
            )


def _collect_property_cells(cells: dict[tuple[str, str, str], dict[str, Any]], *, runs_root: Path) -> None:
    for run_dir in sorted(p for p in runs_root.iterdir() if p.is_dir()):
        report = _read_json(run_dir / "outputs" / "proptest" / "result.json")
        if not report:
            continue
        manifest = _read_json(run_dir / "manifest.json") or {}
        implementation = _manifest_impl(manifest) or "amaru"
        for check in report.get("check_reports") or []:
            target_id = f"proptest::{check.get('package')}::{check.get('filter')}"
            protocol = _infer_protocol(text=f"{check.get('package')} {check.get('filter')}") or "unknown"
            _upsert_cell(
                cells,
                target_id=target_id,
                implementation=implementation,
                protocol=protocol,
                run_id=run_dir.name,
                metrics={
                    "properties_run": check.get("properties_run"),
                    "properties_failed": check.get("properties_failed"),
                },
            )


def build_report(*, runs_root: Path, manifests_dir: Path, state_dir: Path) -> dict[str, Any]:
    manifest_index = _target_manifest_index(manifests_dir)
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    _collect_aflpp_cells(cells, runs_root=runs_root, manifest_index=manifest_index)
    _collect_cargo_fuzz_cells(cells, runs_root=runs_root, manifest_index=manifest_index)
    _collect_campaign_cells(cells, runs_root=runs_root, manifest_index=manifest_index)
    _collect_property_cells(cells, runs_root=runs_root)
    rows = sorted(cells.values(), key=lambda item: (item["protocol"], item["implementation"], item["target_id"]))
    return {
        "schema_version": "v1",
        "generated_at_utc": utc_now_iso(),
        "runs_root": str(runs_root),
        "manifests_dir": str(manifests_dir),
        "output_path": str(state_dir / "coverage-rollup.json"),
        "cell_count": len(rows),
        "cells": rows,
        "protocols": sorted({row["protocol"] for row in rows}),
        "implementations": sorted({row["implementation"] for row in rows}),
        "targets": sorted({row["target_id"] for row in rows}),
    }


def write_report(*, report: dict[str, Any], state_dir: Path) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    out_path = state_dir / "coverage-rollup.json"
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-root", default=str(DWARF_ROOT / "runs"))
    parser.add_argument("--manifests-dir", default=str(DWARF_ROOT / "targets" / "manifests"))
    parser.add_argument("--state-dir", default=str(DWARF_ROOT / "state"))
    args = parser.parse_args(argv)
    report = build_report(
        runs_root=Path(args.runs_root),
        manifests_dir=Path(args.manifests_dir),
        state_dir=Path(args.state_dir),
    )
    path = write_report(report=report, state_dir=Path(args.state_dir))
    print(json.dumps({"coverage_rollup": str(path), "cell_count": report["cell_count"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
