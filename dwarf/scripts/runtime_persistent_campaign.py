#!/usr/bin/env python3

import argparse
import json
import os
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import fuzz_campaign_orchestrator
import long_campaign_orchestrator
import runtime_aflnet_campaign
import runtime_cargo_mutants_campaign
import runtime_miri_campaign
import runtime_proptest_campaign
import runtime_symbolic_execution_campaign

REPO_ROOT = SCRIPT_DIR.parent


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_schedule(value: str | None) -> dict:
    if value is None or value == "":
        return {"kind": "manual", "expression": None}
    if value in {"hourly", "daily"}:
        return {"kind": value, "expression": None}
    if value.startswith("cron "):
        expression = value[5:].strip()
        if not expression:
            raise ValueError("cron schedule requires a cron expression")
        return {"kind": "cron", "expression": expression}
    raise ValueError("schedule must be one of hourly, daily, or 'cron <expr>'")


def _runner_mapping():
    return {
        "runtime_fuzz_campaign": fuzz_campaign_orchestrator.run_campaign,
        "runtime_long_campaign": long_campaign_orchestrator.run_long_campaign,
        "runtime_aflnet_campaign": runtime_aflnet_campaign.run_aflnet_campaign,
        "runtime_miri_campaign": runtime_miri_campaign.run_miri_campaign,
        "runtime_proptest_campaign": runtime_proptest_campaign.run_proptest_campaign,
        "runtime_cargo_mutants_campaign": runtime_cargo_mutants_campaign.run_cargo_mutants_campaign,
        "runtime_symbolic_execution_campaign": runtime_symbolic_execution_campaign.run_symbolic_execution_campaign,
    }


def _default_runner(config: dict) -> Path:
    runner_type = str(config["runner_type"])
    mapping = _runner_mapping()
    if runner_type not in mapping:
        raise ValueError(f"unsupported runner_type: {runner_type}")
    child_config = _normalize_child_config_paths(dict(config["child_config"]))
    child_config["output_dir"] = config["child_output_dir"]
    return Path(mapping[runner_type](child_config))


def _normalize_path_string(value: str) -> str:
    candidate = Path(value)
    if candidate.exists():
        return str(candidate)
    if candidate.is_absolute():
        marker = "/dwarf-fw/"
        text = str(candidate)
        if marker in text:
            suffix = text.split(marker, 1)[1]
            remapped = REPO_ROOT / suffix
            if remapped.exists():
                return str(remapped)
    return value


def _normalize_child_config_paths(value):
    if isinstance(value, dict):
        return {key: _normalize_child_config_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_child_config_paths(item) for item in value]
    if isinstance(value, str):
        return _normalize_path_string(value)
    return value


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _extract_metrics(*, runner_type: str, child_output_dir: Path, report: dict) -> dict:
    metrics = {
        "execs_per_sec": None,
        "bitmap_cvg": None,
        "queue_count": None,
        "crash_count": None,
        "hang_count": None,
        "assertion_summary": report.get("assertion_summary"),
    }
    if runner_type == "runtime_fuzz_campaign":
        aggregated = _load_json(child_output_dir / "aggregated-stats.json")
        metrics["queue_count"] = int(report.get("total_queue_count", 0))
        metrics["crash_count"] = int(report.get("total_crash_count", 0))
        metrics["hang_count"] = int(report.get("total_hang_count", 0))
        metrics["execs_per_sec"] = float(report.get("throughput", {}).get("average_afl_execs_per_sec", 0.0))
        bitmap_values = []
        for entry in aggregated.get("subcampaigns", []):
            stats = entry.get("stats") or {}
            raw = stats.get("bitmap_cvg")
            if isinstance(raw, str) and raw.endswith("%"):
                try:
                    bitmap_values.append(float(raw[:-1]))
                except ValueError:
                    pass
            elif isinstance(raw, (int, float)):
                bitmap_values.append(float(raw))
        metrics["bitmap_cvg"] = max(bitmap_values) if bitmap_values else None
        return metrics
    if runner_type == "runtime_long_campaign":
        totals = report.get("totals") or {}
        metrics["queue_count"] = int(totals.get("total_queue_count", 0))
        metrics["crash_count"] = int(totals.get("total_crash_count", 0))
        metrics["hang_count"] = int(totals.get("total_hang_count", 0))
        metrics["execs_per_sec"] = None
        metrics["bitmap_cvg"] = float(totals.get("max_bitmap_cvg", 0.0))
        return metrics
    if runner_type == "runtime_aflnet_campaign":
        metrics["queue_count"] = int(report.get("states_visited", 0))
        metrics["crash_count"] = int(report.get("crashes", 0))
        metrics["hang_count"] = int(report.get("hangs", 0))
        metrics["execs_per_sec"] = float(report.get("execs_per_sec", 0.0))
        return metrics
    if runner_type == "runtime_cargo_mutants_campaign":
        metrics["queue_count"] = int(report.get("candidate_count", 0))
        metrics["crash_count"] = int(report.get("survived_count", 0))
        return metrics
    if runner_type == "runtime_miri_campaign":
        metrics["queue_count"] = int(report.get("tests_run", 0))
        metrics["crash_count"] = int(report.get("ub_findings", {}).get("count", 0))
        return metrics
    if runner_type == "runtime_proptest_campaign":
        metrics["queue_count"] = int(report.get("properties_run", 0))
        metrics["crash_count"] = int(report.get("properties_failed", 0))
        return metrics
    if runner_type == "runtime_symbolic_execution_campaign":
        metrics["queue_count"] = int(report.get("novel_inputs_generated", 0))
        metrics["crash_count"] = int(report.get("crashes", 0))
        metrics["hang_count"] = int(report.get("hangs", 0))
        return metrics
    return metrics


def _build_regressions(*, previous_run: dict | None, current_metrics: dict, coverage_drop_threshold_pct: float) -> list[dict]:
    if not previous_run:
        return []
    regressions = []
    previous_metrics = previous_run.get("metrics") or {}

    previous_execs = previous_metrics.get("execs_per_sec")
    current_execs = current_metrics.get("execs_per_sec")
    if isinstance(previous_execs, (int, float)) and previous_execs > 0 and isinstance(current_execs, (int, float)):
        drop_pct = ((float(previous_execs) - float(current_execs)) / float(previous_execs)) * 100.0
        if drop_pct > coverage_drop_threshold_pct:
            regressions.append(
                {
                    "kind": "throughput_drop",
                    "previous": float(previous_execs),
                    "current": float(current_execs),
                    "drop_pct": drop_pct,
                }
            )

    previous_bitmap = previous_metrics.get("bitmap_cvg")
    current_bitmap = current_metrics.get("bitmap_cvg")
    if isinstance(previous_bitmap, (int, float)) and isinstance(current_bitmap, (int, float)):
        if float(current_bitmap) < float(previous_bitmap):
            regressions.append(
                {
                    "kind": "bitmap_coverage_drop",
                    "previous": float(previous_bitmap),
                    "current": float(current_bitmap),
                    "drop": float(previous_bitmap) - float(current_bitmap),
                }
            )

    previous_crashes = int(previous_metrics.get("crash_count", 0) or 0)
    current_crashes = int(current_metrics.get("crash_count", 0) or 0)
    if current_crashes > previous_crashes:
        regressions.append(
            {
                "kind": "novel_crash_signature",
                "previous": previous_crashes,
                "current": current_crashes,
            }
        )

    previous_assertions = previous_metrics.get("assertion_summary")
    current_assertions = current_metrics.get("assertion_summary")
    if previous_assertions is not None and current_assertions is not None and previous_assertions != current_assertions:
        regressions.append(
            {
                "kind": "assertion_population_shift",
                "previous": previous_assertions,
                "current": current_assertions,
            }
        )

    return regressions


def _sarif_result(entry: dict) -> dict:
    return {
        "ruleId": f"DWARF-{entry['kind'].replace('_', '-').upper()}",
        "level": "warning",
        "message": {"text": json.dumps(entry, sort_keys=True)},
    }


def _write_sarif(*, output_dir: Path, campaign_id: str, regressions: list[dict]) -> Path:
    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Dwarf Persistent Campaign",
                        "informationUri": "https://gainsec.example/dwarf",
                        "rules": [
                            {
                                "id": "DWARF-FUZZ-REGRESSION",
                                "name": "Fuzz regression detected",
                                "shortDescription": {"text": "Persistent campaign regression signal"},
                            }
                        ],
                    }
                },
                "automationDetails": {"id": campaign_id},
                "results": [_sarif_result(item) for item in regressions],
            }
        ],
    }
    path = output_dir / "regressions.sarif"
    path.write_text(json.dumps(sarif, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _run_upload_hook(*, upload_command, sarif_path: Path) -> dict | None:
    if not upload_command:
        return None
    command = [part.replace("{sarif_path}", str(sarif_path)) for part in upload_command]
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-1024:],
        "stderr_tail": (proc.stderr or "")[-1024:],
    }


def run_persistent_campaign(config: dict, runner=None) -> Path:
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    state_dir = Path(config["state_dir"])
    state_root = state_dir / "persistent-campaigns" / str(config["campaign_id"])
    state_root.mkdir(parents=True, exist_ok=True)
    history_path = state_root / "history.json"
    history = _load_json(history_path) if history_path.exists() else {"campaign_id": config["campaign_id"], "runs": []}
    previous_run = history["runs"][-1] if history.get("runs") else None

    runner = runner or _default_runner
    child_output_dir = output_dir / "current-run"
    runner_config = {
        "campaign_id": config["campaign_id"],
        "runner_type": config["runner_type"],
        "child_config": _normalize_child_config_paths(dict(config["child_config"])),
        "output_dir": str(child_output_dir),
        "child_output_dir": str(child_output_dir),
    }
    report_path = Path(runner(runner_config))
    child_report = json.loads(report_path.read_text(encoding="utf-8"))

    schedule = parse_schedule(config.get("schedule"))
    metrics = _extract_metrics(
        runner_type=str(config["runner_type"]),
        child_output_dir=child_output_dir,
        report=child_report,
    )
    regressions = _build_regressions(
        previous_run=previous_run,
        current_metrics=metrics,
        coverage_drop_threshold_pct=float(config.get("coverage_drop_threshold_pct", 0.0)),
    )
    sarif_path = _write_sarif(output_dir=output_dir, campaign_id=str(config["campaign_id"]), regressions=regressions)
    upload = _run_upload_hook(upload_command=config.get("sarif_upload_command"), sarif_path=sarif_path)

    run_id = _utc_now_iso().replace(":", "").replace("-", "")
    report = {
        "campaign_id": str(config["campaign_id"]),
        "run_id": run_id,
        "run_index": len(history["runs"]) + 1,
        "previous_run_id": previous_run.get("run_id") if previous_run else None,
        "runner_type": str(config["runner_type"]),
        "schedule": schedule,
        "state_root": str(state_root),
        "child_output_dir": str(child_output_dir),
        "child_report_path": str(report_path),
        "metrics": metrics,
        "regressions": regressions,
        "sarif_path": str(sarif_path),
        "sarif_upload": upload,
        "completed_at": _utc_now_iso(),
    }
    report_path = output_dir / "campaign-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    history["runs"].append(
        {
            "run_id": run_id,
            "completed_at": report["completed_at"],
            "report_path": str(report_path),
            "metrics": metrics,
        }
    )
    history_path.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report_path = run_persistent_campaign(config)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                "persistent_campaign_completed=true",
                f"campaign_id={shlex.quote(report['campaign_id'])}",
                f"run_index={report['run_index']}",
                f"regressions={len(report['regressions'])}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
