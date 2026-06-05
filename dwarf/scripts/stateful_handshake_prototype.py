#!/usr/bin/env python3
"""Minimal stateful protocol fuzzing prototype for handshake-style corpora."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable


Runner = Callable[[dict], dict]


def load_state_machine_corpus(path: Path) -> dict:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(corpus, dict):
        raise ValueError("state-machine corpus must be a mapping")
    sequences = corpus.get("sequences")
    if not isinstance(sequences, list) or not sequences:
        raise ValueError("state-machine corpus must contain non-empty sequences")
    return corpus


def _filter_sequences(corpus: dict, sequence_filter: list[str] | None) -> list[dict]:
    sequences = corpus["sequences"]
    if not sequence_filter:
        return sequences
    wanted = set(sequence_filter)
    filtered = [sequence for sequence in sequences if sequence["id"] in wanted]
    missing = sorted(wanted - {sequence["id"] for sequence in filtered})
    if missing:
        raise ValueError(f"sequence_filter references missing sequence ids: {missing}")
    return filtered


def _declared_states(sequences: list[dict]) -> list[str]:
    states: set[str] = set()
    for sequence in sequences:
        states.add(sequence["initial_state"])
        for transition in sequence["transitions"]:
            states.add(transition["from"])
            states.add(transition["to"])
    return sorted(states)


def _default_runner(message: dict) -> dict:
    return {"outcome": message.get("expect") or "ok", "stdout": ""}


def _make_binary_runner(target_binary: Path, timeout_seconds: float) -> Runner:
    def _run(message: dict) -> dict:
        data = bytes.fromhex(message["hex"])
        try:
            proc = subprocess.run(
                [str(target_binary)],
                input=data,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"outcome": "crash", "stdout": "", "returncode": None}
        stdout = proc.stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            outcome = "crash"
        elif "outcome=ok" in stdout or '"outcome":"ok"' in stdout:
            outcome = "ok"
        elif "clean_error" in stdout:
            outcome = "clean_error"
        else:
            outcome = "ok"
        return {"outcome": outcome, "stdout": stdout, "returncode": proc.returncode}

    return _run


def run_state_machine(corpus: dict, runner: Runner, sequence_filter: list[str] | None = None) -> dict:
    sequences = _filter_sequences(corpus, sequence_filter)
    declared_states = _declared_states(sequences)
    visited_states: set[str] = set()
    transitions_covered = 0
    invalid_transitions = 0
    outcome_counts: dict[str, int] = {}
    sequence_reports: list[dict] = []

    for sequence in sequences:
        current_state = sequence["initial_state"]
        visited_states.add(current_state)
        seq_invalid = 0
        seq_transitions: list[dict] = []
        for index, transition in enumerate(sequence["transitions"]):
            message = transition["message"]
            result = runner(message)
            outcome = result["outcome"]
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            state_matches = transition["from"] == current_state
            expected_outcome = message.get("expect")
            outcome_matches = expected_outcome is None or expected_outcome == outcome
            transition_ok = state_matches and outcome_matches
            if state_matches:
                current_state = transition["to"]
                visited_states.add(current_state)
            if transition_ok:
                transitions_covered += 1
            else:
                invalid_transitions += 1
                seq_invalid += 1
            seq_transitions.append(
                {
                    "transition_index": index,
                    "message": message["name"],
                    "from_state": transition["from"],
                    "to_state": transition["to"],
                    "expected_outcome": expected_outcome,
                    "outcome": outcome,
                    "state_matches": state_matches,
                    "outcome_matches": outcome_matches,
                    "transition_ok": transition_ok,
                }
            )
        sequence_reports.append(
            {
                "id": sequence["id"],
                "initial_state": sequence["initial_state"],
                "final_state": current_state,
                "invalid_transitions": seq_invalid,
                "transitions": seq_transitions,
            }
        )

    states_missing = sorted(set(declared_states) - visited_states)
    transitions_declared = sum(len(sequence["transitions"]) for sequence in sequences)
    coverage_pct = 0.0
    if transitions_declared:
        coverage_pct = round((transitions_covered / transitions_declared) * 100.0, 2)
    return {
        "prototype": "stateful-handshake-v1",
        "protocol": corpus.get("protocol", "handshake"),
        "corpus_id": corpus.get("id"),
        "sequence_filter": list(sequence_filter or []),
        "states_declared": declared_states,
        "states_visited": sorted(visited_states),
        "states_missing": states_missing,
        "transitions_declared": transitions_declared,
        "transitions_covered": transitions_covered,
        "transition_coverage_pct": coverage_pct,
        "invalid_transitions": invalid_transitions,
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "sequences": sequence_reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, help="Path to a state-machine corpus JSON file.")
    parser.add_argument("--output", required=True, help="Where to write the JSON report.")
    parser.add_argument("--target-binary", help="Optional decoder binary to execute instead of model-only replay.")
    parser.add_argument("--timeout-seconds", type=float, default=1.0, help="Per-input timeout for target execution.")
    parser.add_argument("--sequence", action="append", default=[], help="Sequence id filter. Repeatable.")
    args = parser.parse_args(argv)

    corpus = load_state_machine_corpus(Path(args.corpus))
    runner = _default_runner if not args.target_binary else _make_binary_runner(Path(args.target_binary), args.timeout_seconds)
    report = run_state_machine(corpus=corpus, runner=runner, sequence_filter=args.sequence or None)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        "protocol={protocol} transitions={transitions_covered}/{transitions_declared} invalid={invalid_transitions}".format(
            **report
        )
    )
    return 0 if report["invalid_transitions"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
