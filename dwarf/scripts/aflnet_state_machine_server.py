#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import signal
import socketserver
import subprocess
from pathlib import Path


def load_protocol_model(corpus_path: Path) -> dict:
    body = json.loads(corpus_path.read_text(encoding="utf-8"))
    transitions: dict[tuple[str, str], tuple[str, str]] = {}
    known_states = {"idle"}
    states_with_outgoing: set[str] = set()
    declared_transition_labels: list[str] = []
    for sequence in body.get("sequences", []):
        current_state = str(sequence.get("initial_state", "idle"))
        known_states.add(current_state)
        for transition in sequence.get("transitions", []):
            next_state = str(transition["to"])
            message = dict(transition["message"])
            message_hex = str(message["hex"]).lower()
            message_name = str(message.get("name") or message_hex)
            transitions[(current_state, message_hex)] = (next_state, message_name)
            declared_transition_labels.append(f"{current_state}->{next_state}:{message_name}")
            states_with_outgoing.add(current_state)
            current_state = next_state
            known_states.add(next_state)
    return {
        "protocol": str(body.get("protocol", "unknown")),
        "transitions": transitions,
        "states": sorted(known_states),
        "terminal_states": sorted(state for state in known_states if state not in states_with_outgoing),
        "declared_transition_labels": sorted(set(declared_transition_labels)),
    }


def decode_message(target_binary: Path, payload: bytes) -> bool:
    proc = subprocess.run(
        [str(target_binary)],
        input=payload,
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def build_response_codes(states: list[str]) -> dict[str, bytes]:
    ordered_states = sorted(set(states))
    if {"idle", "proposed", "accepted", "refused"}.issubset(set(ordered_states)):
        return {
            "idle": b"220 idle\r\n",
            "proposed": b"250 proposed\r\n",
            "accepted": b"251 accepted\r\n",
            "refused": b"550 refused\r\n",
        }

    codes: dict[str, bytes] = {}
    base = 220
    for index, state in enumerate(ordered_states):
        codes[state] = f"{base + index} {state}\r\n".encode("ascii")
    return codes


def write_state_report(report_path: Path, state: dict) -> None:
    transitions_declared = len(state["declared_transition_labels"])
    transitions_covered = len(state["covered_transition_labels"])
    transition_coverage_pct = round((transitions_covered / transitions_declared) * 100.0, 2) if transitions_declared else 0.0
    report = {
        "protocol": state["protocol"],
        "states_declared": sorted(state["states_declared"]),
        "states_visited": sorted(state["states_visited"]),
        "state_count": len(state["states_visited"]),
        "novel_state_count": max(0, len(state["states_visited"]) - 1),
        "transitions_declared": transitions_declared,
        "transitions_covered": transitions_covered,
        "transition_coverage_pct": transition_coverage_pct,
        "covered_transition_labels": sorted(state["covered_transition_labels"]),
        "declared_transition_labels": sorted(state["declared_transition_labels"]),
        "sessions": state["sessions"],
        "invalid_messages": state["invalid_messages"],
        "response_codes": dict(sorted(state["response_codes"].items())),
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        shared = self.server.shared_state  # type: ignore[attr-defined]
        shared["sessions"] += 1
        current_state = "idle"
        shared["states_visited"].add(current_state)
        greeting = shared["state_codes"].get(current_state, b"220 idle\r\n")
        greeting_code = greeting[:3].decode("ascii", errors="ignore") or "220"
        shared["response_codes"][greeting_code] = shared["response_codes"].get(greeting_code, 0) + 1
        self.wfile.write(greeting)
        self.wfile.flush()

        while True:
            line = self.rfile.readline()
            if not line:
                break
            text = line.strip().decode("ascii", errors="ignore").lower()
            if not text:
                continue
            try:
                payload = bytes.fromhex(text)
            except ValueError:
                shared["invalid_messages"] += 1
                shared["response_codes"]["500"] = shared["response_codes"].get("500", 0) + 1
                self.wfile.write(b"500 invalid-hex\r\n")
                self.wfile.flush()
                break
            if not decode_message(shared["target_binary"], payload):
                shared["invalid_messages"] += 1
                shared["response_codes"]["500"] = shared["response_codes"].get("500", 0) + 1
                self.wfile.write(b"500 invalid-decode\r\n")
                self.wfile.flush()
                break

            next_state_info = shared["transitions"].get((current_state, text))
            if next_state_info is None:
                shared["invalid_messages"] += 1
                shared["response_codes"]["500"] = shared["response_codes"].get("500", 0) + 1
                self.wfile.write(b"500 invalid-transition\r\n")
                self.wfile.flush()
                break

            next_state, message_name = next_state_info
            shared["states_visited"].add(next_state)
            shared["covered_transition_labels"].add(f"{current_state}->{next_state}:{message_name}")
            current_state = next_state
            response = shared["state_codes"].get(current_state, b"599 unknown-state\r\n")
            code = response[:3].decode("ascii")
            shared["response_codes"][code] = shared["response_codes"].get(code, 0) + 1
            self.wfile.write(response)
            self.wfile.flush()
            if current_state in shared["terminal_states"]:
                break


def serve(*, port: int, target_binary: Path, state_corpus: Path, state_report: Path) -> int:
    model = load_protocol_model(state_corpus)
    shared_state = {
        "protocol": model["protocol"],
        "target_binary": target_binary,
        "transitions": model["transitions"],
        "states_declared": set(model["states"]),
        "states_visited": set(),
        "declared_transition_labels": set(model["declared_transition_labels"]),
        "covered_transition_labels": set(),
        "sessions": 0,
        "invalid_messages": 0,
        "response_codes": {},
        "state_codes": build_response_codes(model["states"]),
        "terminal_states": set(model["terminal_states"]),
    }

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    with Server(("127.0.0.1", port), Handler) as server:
        server.shared_state = shared_state  # type: ignore[attr-defined]
        server.timeout = 0.5
        shared_state["stop_requested"] = False

        def _shutdown(*_args):
            shared_state["stop_requested"] = True

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
        try:
            while not shared_state["stop_requested"]:
                server.handle_request()
        finally:
            write_state_report(state_report, shared_state)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--target-binary", required=True)
    parser.add_argument("--state-corpus", required=True)
    parser.add_argument("--state-report", required=True)
    args = parser.parse_args(argv)
    return serve(
        port=args.port,
        target_binary=Path(args.target_binary),
        state_corpus=Path(args.state_corpus),
        state_report=Path(args.state_report),
    )


if __name__ == "__main__":
    raise SystemExit(main())
