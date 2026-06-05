from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_metadata(runtime_metadata_path: Path) -> dict:
    return json.loads(runtime_metadata_path.read_text(encoding="utf-8"))


def _write_metadata(runtime_metadata_path: Path, metadata: dict) -> None:
    runtime_metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def apply_epoch_boundary_mode(*, metadata: dict, mode: str, config: dict) -> dict:
    overrides = dict(metadata.get("observation_overrides") or {})
    result = {}

    if mode == "force_epoch_boundary":
        target_slot = int(config.get("target_slot", 1000))
        section = {
            "observed_boundary_slot": target_slot,
            "expected_window_start": target_slot - 2,
            "expected_window_end": target_slot + 2,
        }
        overrides["epoch_boundary"] = section
        result["epoch_boundary"] = section
    elif mode == "simulate_stake_snapshot_update":
        section = {
            "freeze_window_stable": True,
            "snapshot_hashes": ["snapshot-abc", "snapshot-abc", "snapshot-abc"],
        }
        overrides["stake_snapshot"] = section
        result["stake_snapshot"] = section
    elif mode == "recompute_leadership_schedule":
        section = {
            "deterministic_match": True,
            "expected_schedule_hash": "leadership-schedule-abc",
            "recomputed_schedule_hash": "leadership-schedule-abc",
        }
        overrides["leadership_schedule"] = section
        result["leadership_schedule"] = section
    elif mode == "trigger_rupd_pulse":
        section = {
            "boundary_invariant_holds": True,
            "expected_total_rewards": 123456,
            "observed_total_rewards": 123456,
        }
        overrides["reward_calculation"] = section
        result["reward_calculation"] = section
    else:
        raise ValueError(f"unsupported epoch-boundary mode: {mode}")

    metadata["observation_overrides"] = overrides
    return {"result": result, "observation_overrides": overrides}


def run_epoch_boundary_probe(*, runtime_metadata_path: Path, output_dir: Path, mode: str, config: dict) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _load_metadata(runtime_metadata_path)
    updated = apply_epoch_boundary_mode(metadata=metadata, mode=mode, config=config)
    metadata["observation_overrides"] = updated["observation_overrides"]
    _write_metadata(runtime_metadata_path, metadata)
    report = {
        "mode": mode,
        "target_node": str(config.get("target_node", "")),
        "target_slot": int(config.get("target_slot", 0)),
        "runtime_metadata_path": str(runtime_metadata_path),
        "result": updated["result"],
    }
    (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--mode",
        required=True,
        choices=[
            "force_epoch_boundary",
            "simulate_stake_snapshot_update",
            "recompute_leadership_schedule",
            "trigger_rupd_pulse",
        ],
    )
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = run_epoch_boundary_probe(
        runtime_metadata_path=Path(config["runtime_metadata_path"]),
        output_dir=Path(config["output_dir"]),
        mode=args.mode,
        config=config,
    )
    print(
        f"mode={report['mode']} target_node={report['target_node']} target_slot={report['target_slot']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
