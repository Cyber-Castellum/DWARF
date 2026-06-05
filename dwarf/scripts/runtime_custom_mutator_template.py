#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cargo_fuzz_campaign


def _count_seed_inputs(seed_dirs: list[Path]) -> int:
    count = 0
    for seed_dir in seed_dirs:
        if not seed_dir.is_dir():
            continue
        count += sum(1 for path in seed_dir.rglob("*") if path.is_file())
    return count


def write_template_report(
    *,
    output_dir: Path,
    fuzz_dir: Path,
    target_name: str,
    dict_path: Path | None,
    seed_input_count: int,
    seconds: int,
    replay_harness: str,
    replay_target_id: str,
) -> Path:
    artifact_summary = cargo_fuzz_campaign.summarize_campaign_output(output_dir)
    execution = {}
    execution_path = output_dir / "execution.json"
    if execution_path.is_file():
        execution = json.loads(execution_path.read_text(encoding="utf-8"))
    libfuzzer_stats = execution.get("libfuzzer_stats") or {}
    queue_count = int(artifact_summary.get("queue_count", 0))
    report = {
        "template_id": "runtime-custom-mutator-template",
        "fuzz_dir": str(fuzz_dir),
        "target_name": target_name,
        "queue_count": queue_count,
        "crash_count": int(artifact_summary.get("crash_count", 0)),
        "hang_count": int(artifact_summary.get("hang_count", 0)),
        "seed_input_count": int(seed_input_count),
        "novel_queue_count": max(queue_count - int(seed_input_count), 0),
        "structural_mutator_len_control": cargo_fuzz_campaign.custom_mutator_len_control_for_fuzz_dir(fuzz_dir),
        "dictionary_path": str(dict_path) if dict_path else None,
        "seconds": seconds,
        "replay_harness": replay_harness,
        "replay_target_id": replay_target_id,
        "libfuzzer_stats": libfuzzer_stats,
        "average_exec_per_sec": float(libfuzzer_stats.get("average_exec_per_sec", 0.0)),
        "number_of_executed_units": int(
            libfuzzer_stats.get(
                "number_of_executed_units",
                libfuzzer_stats.get("number_of_executed_units_estimate", 0),
            )
        ),
    }
    report_path = output_dir / "template-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def run_template_with_metadata(
    *,
    working_dir: Path,
    fuzz_dir: Path,
    target_name: str,
    seed_dirs: list[Path],
    output_dir: Path,
    seconds: int,
    target_implementation: str,
    replay_harness: str,
    replay_target_id: str,
    replay_targets: list[str],
    toolchain: str,
    dict_path_override: Path | None = None,
    extra_libfuzzer_args: list[str] | None = None,
) -> int:
    seed_input_count = _count_seed_inputs(seed_dirs)
    exit_code = cargo_fuzz_campaign.run_campaign_with_metadata(
        working_dir=working_dir,
        fuzz_dir=fuzz_dir,
        target_name=target_name,
        seed_dirs=seed_dirs,
        output_dir=output_dir,
        seconds=seconds,
        target_implementation=target_implementation,
        replay_harness=replay_harness,
        replay_target_id=replay_target_id,
        replay_targets=replay_targets,
        toolchain=toolchain,
        dict_path_override=dict_path_override,
        extra_libfuzzer_args=extra_libfuzzer_args,
    )
    dict_path = dict_path_override or cargo_fuzz_campaign.grammar_dict_path_for_fuzz_dir(fuzz_dir)
    report_path = write_template_report(
        output_dir=output_dir,
        fuzz_dir=fuzz_dir,
        target_name=target_name,
        dict_path=dict_path,
        seed_input_count=seed_input_count,
        seconds=seconds,
        replay_harness=replay_harness,
        replay_target_id=replay_target_id,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                f"queue_count={report['queue_count']}",
                f"novel_queue_count={report['novel_queue_count']}",
                f"execs_per_sec={report['average_exec_per_sec']}",
                f"report_relpath={report_path.name}",
            ]
        )
    )
    return exit_code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-dir", required=True)
    parser.add_argument("--fuzz-dir", required=True)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--seed-dir", dest="seed_dirs", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--toolchain", default=cargo_fuzz_campaign.DEFAULT_TOOLCHAIN)
    parser.add_argument("--dict-path")
    parser.add_argument("--libfuzzer-arg", dest="extra_libfuzzer_args", action="append", default=[])
    parser.add_argument("--target-implementation", default="amaru")
    parser.add_argument("--replay-harness", required=True)
    parser.add_argument("--replay-target-id", required=True)
    parser.add_argument("--replay-target", dest="replay_targets", action="append", required=True)
    args = parser.parse_args(argv)
    return run_template_with_metadata(
        working_dir=Path(args.working_dir),
        fuzz_dir=Path(args.fuzz_dir),
        target_name=args.target_name,
        seed_dirs=[Path(path) for path in args.seed_dirs],
        output_dir=Path(args.output_dir),
        seconds=args.seconds,
        target_implementation=args.target_implementation,
        replay_harness=args.replay_harness,
        replay_target_id=args.replay_target_id,
        replay_targets=args.replay_targets,
        toolchain=args.toolchain,
        dict_path_override=Path(args.dict_path) if args.dict_path else None,
        extra_libfuzzer_args=list(args.extra_libfuzzer_args),
    )


if __name__ == "__main__":
    raise SystemExit(main())
