#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import aflpp_campaign


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_bitmap_cvg(raw: str | int | float | None) -> float | None:
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


def parse_int(raw: str | int | float | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def load_manifest(run_dir: Path) -> dict | None:
    path = run_dir / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_afl_stats(run_dir: Path) -> dict | None:
    path = run_dir / "outputs" / "aflpp" / "default" / "fuzzer_stats"
    if not path.is_file():
        return None
    return aflpp_campaign.parse_fuzzer_stats(path.read_text(encoding="utf-8", errors="replace"))


def load_afl_summary(run_dir: Path) -> dict | None:
    path = run_dir / "outputs" / "aflpp" / "summary.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def collect_timeseries(
    *,
    runs_root: Path,
    scenario_id_contains: str,
) -> list[dict]:
    entries: list[dict] = []
    for run_dir in sorted(path for path in runs_root.iterdir() if path.is_dir()):
        manifest = load_manifest(run_dir)
        if not manifest:
            continue
        scenario_id = manifest.get("scenario", {}).get("id")
        if not scenario_id or scenario_id_contains not in scenario_id:
            continue
        stats = load_afl_stats(run_dir)
        summary = load_afl_summary(run_dir)
        if not stats or not summary:
            continue
        entries.append(
            {
                "run_id": run_dir.name,
                "scenario_id": scenario_id,
                "bitmap_cvg": parse_bitmap_cvg(stats.get("bitmap_cvg")),
                "queue_count": parse_int(summary.get("queue_count")),
                "execs_done": parse_int(stats.get("execs_done")),
                "execs_per_sec": parse_bitmap_cvg(stats.get("execs_per_sec")),
                "last_find_ts": parse_int(stats.get("last_find")),
                "saved_crashes": parse_int(stats.get("saved_crashes")) or 0,
                "saved_hangs": parse_int(stats.get("saved_hangs")) or 0,
                "corpus_count": parse_int(stats.get("corpus_count")),
                "source_path": str(run_dir),
            }
        )
    previous = None
    for entry in entries:
        current = entry.get("bitmap_cvg")
        if previous is None or current is None or previous.get("bitmap_cvg") is None:
            entry["bitmap_cvg_delta"] = None
            entry["coverage_regressed"] = False
        else:
            delta = round(float(current) - float(previous["bitmap_cvg"]), 2)
            entry["bitmap_cvg_delta"] = delta
            entry["coverage_regressed"] = delta < 0
        previous = entry
    return entries


def build_report(*, runs_root: Path, scenario_id_contains: str) -> dict:
    runs = collect_timeseries(runs_root=runs_root, scenario_id_contains=scenario_id_contains)
    regressions = [entry["run_id"] for entry in runs if entry.get("coverage_regressed")]
    latest = runs[-1] if runs else None
    return {
        "schema_version": "v1",
        "generated_at_utc": utc_now_iso(),
        "runs_root": str(runs_root),
        "scenario_id_contains": scenario_id_contains,
        "run_count": len(runs),
        "regression_run_ids": regressions,
        "latest_bitmap_cvg": latest.get("bitmap_cvg") if latest else None,
        "latest_queue_count": latest.get("queue_count") if latest else None,
        "latest_execs_per_sec": latest.get("execs_per_sec") if latest else None,
        "timeseries": runs,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Corpus Health Report",
        "",
        f"- Scenario filter: `{report['scenario_id_contains']}`",
        f"- Runs summarized: {report['run_count']}",
        f"- Coverage regressions: {len(report['regression_run_ids'])}",
        "",
        "| run_id | scenario_id | bitmap_cvg | delta | queue_count | execs_per_sec | crashes | hangs |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report["timeseries"]:
        lines.append(
            f"| {item['run_id']} | {item['scenario_id']} | {item.get('bitmap_cvg')} | {item.get('bitmap_cvg_delta')} | "
            f"{item.get('queue_count')} | {item.get('execs_per_sec')} | {item.get('saved_crashes')} | {item.get('saved_hangs')} |"
        )
    return "\n".join(lines) + "\n"


def render_html(report: dict) -> str:
    rows = []
    for item in report["timeseries"]:
        regressed = " class='regressed'" if item.get("coverage_regressed") else ""
        rows.append(
            f"<tr{regressed}><td>{item['run_id']}</td><td>{item['scenario_id']}</td>"
            f"<td>{item.get('bitmap_cvg')}</td><td>{item.get('bitmap_cvg_delta')}</td>"
            f"<td>{item.get('queue_count')}</td><td>{item.get('execs_per_sec')}</td>"
            f"<td>{item.get('saved_crashes')}</td><td>{item.get('saved_hangs')}</td></tr>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Dwarf Corpus Health</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;}table{border-collapse:collapse;width:100%;}"
        "th,td{border:1px solid #ddd;padding:8px;text-align:left;}th{background:#f4f4f4;}"
        ".regressed{background:#fff0f0;}</style></head><body>"
        "<h1>Corpus Health Report</h1>"
        f"<p>Scenario filter <code>{report['scenario_id_contains']}</code></p>"
        f"<p>Runs summarized: {report['run_count']}; regressions: {len(report['regression_run_ids'])}</p>"
        "<table><thead><tr><th>run_id</th><th>scenario_id</th><th>bitmap_cvg</th><th>delta</th><th>queue_count</th><th>execs_per_sec</th><th>crashes</th><th>hangs</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></body></html>"
    )


def write_report(*, report: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "corpus-health-report.json"
    md_path = output_dir / "corpus-health-report.md"
    html_path = output_dir / "corpus-health-report.html"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    return json_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-root", required=True)
    parser.add_argument("--scenario-id-contains", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)

    report = build_report(
        runs_root=Path(args.runs_root),
        scenario_id_contains=args.scenario_id_contains,
    )
    path = write_report(report=report, output_dir=Path(args.output_dir))
    print(
        " ".join(
            [
                f"run_count={report['run_count']}",
                f"regression_count={len(report['regression_run_ids'])}",
                f"report_relpath={path.name}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
