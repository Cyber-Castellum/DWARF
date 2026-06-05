#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent

_PROTOCOL_BINARIES = {
    "handshake": DWARF_ROOT / "targets" / "amaru-cardano-differential-cargo-fuzz-handshake" / "target" / "release" / "handshake_differential",
    "chainsync": DWARF_ROOT / "targets" / "amaru-cardano-differential-cargo-fuzz-chainsync" / "target" / "release" / "chainsync_differential",
    "blockfetch": DWARF_ROOT / "targets" / "amaru-cardano-differential-cargo-fuzz-blockfetch" / "target" / "release" / "blockfetch_differential",
    "txsubmission": DWARF_ROOT / "targets" / "amaru-cardano-differential-cargo-fuzz-txsubmission" / "target" / "release" / "txsubmission_differential",
}


def _result_relpath(protocol: str) -> Path:
    binary_name = _PROTOCOL_BINARIES[protocol].name
    return Path("corpus") / binary_name / "differential" / "last_result.json"


def _write_summary(output_dir: Path, report: dict) -> None:
    lines = [
        "# Execution Trace Differential",
        "",
        f"- Protocol: `{report['protocol']}`",
        f"- Inputs processed: {report['inputs_processed']}",
        f"- Agreed traces: {report['agreed_count']}",
        f"- Diverged traces: {report['diverged_count']}",
        f"- One-side crashes: {report['one_side_crashed_count']}",
        f"- Equivalent: {report['equivalent']}",
        "",
        "> Current trace granularity is decoder-decision parity per input, not internal protocol spans.",
        "",
    ]
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_execution_trace_differential(config: dict, *, env: dict[str, str] | None = None) -> Path:
    protocol = str(config["protocol"])
    output_dir = Path(config["output_dir"]).resolve()
    corpus_dir = Path(config["corpus_dir"]).resolve()
    differential_binary = Path(config.get("differential_binary") or _PROTOCOL_BINARIES[protocol]).resolve()
    corpus_size = int(config.get("corpus_size", 16))
    output_dir.mkdir(parents=True, exist_ok=True)
    if protocol not in _PROTOCOL_BINARIES:
        raise ValueError(f"unsupported protocol: {protocol}")
    if not differential_binary.is_file():
        raise FileNotFoundError(f"missing differential binary: {differential_binary}")
    if not corpus_dir.is_dir():
        raise FileNotFoundError(f"missing corpus directory: {corpus_dir}")

    built_env = dict(os.environ if env is None else env)
    traces = []
    agreed_count = 0
    diverged_count = 0
    one_side_crashed_count = 0
    result_relpath = _result_relpath(protocol)
    inputs = sorted(path for path in corpus_dir.rglob("*") if path.is_file())[:corpus_size]

    for index, input_path in enumerate(inputs, start=1):
        result_path = output_dir / result_relpath
        if result_path.exists():
            result_path.unlink()
        proc = subprocess.run(
            [str(differential_binary), str(input_path)],
            cwd=output_dir,
            capture_output=True,
            text=True,
            check=False,
            env=built_env,
        )
        record = {}
        if result_path.is_file():
            record = json.loads(result_path.read_text(encoding="utf-8"))
        trace = {
            "input_path": str(input_path),
            "input_name": input_path.name,
            "sequence_index": index,
            "runner_exit_code": proc.returncode,
            "diff_result": record.get("diff_result"),
            "detail_match": record.get("detail_match"),
            "amaru": record.get("amaru"),
            "cardano_node": record.get("cardano_node"),
        }
        traces.append(trace)
        if trace["diff_result"] == "AGREED" and bool(trace["detail_match"]):
            agreed_count += 1
        elif trace["diff_result"] == "ONE_SIDE_CRASHED":
            one_side_crashed_count += 1
        else:
            diverged_count += 1

    report = {
        "protocol": protocol,
        "corpus_dir": str(corpus_dir),
        "differential_binary": str(differential_binary),
        "inputs_processed": len(inputs),
        "agreed_count": agreed_count,
        "diverged_count": diverged_count,
        "one_side_crashed_count": one_side_crashed_count,
        "equivalent": len(inputs) > 0 and diverged_count == 0 and one_side_crashed_count == 0,
        "trace_granularity": "decoder_decision_per_input",
        "traces": traces,
    }
    report_path = output_dir / "result.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_summary(output_dir, report)
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True, choices=sorted(_PROTOCOL_BINARIES))
    parser.add_argument("--corpus-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--differential-binary")
    parser.add_argument("--corpus-size", type=int, default=16)
    args = parser.parse_args(argv)
    report_path = run_execution_trace_differential(
        {
            "protocol": args.protocol,
            "corpus_dir": args.corpus_dir,
            "output_dir": args.output_dir,
            "differential_binary": args.differential_binary,
            "corpus_size": args.corpus_size,
        }
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(
        " ".join(
            [
                "execution_trace_differential_completed=true",
                f"protocol={report['protocol']}",
                f"inputs_processed={report['inputs_processed']}",
                f"equivalent={str(report['equivalent']).lower()}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
