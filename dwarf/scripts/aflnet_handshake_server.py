#!/usr/bin/env python3

import argparse
import json
import signal
import socketserver
import subprocess
from pathlib import Path


def load_transition_map(corpus_path: Path) -> tuple[dict[tuple[str, str], str], set[str]]:
    body = json.loads(corpus_path.read_text(encoding="utf-8"))
    transitions: dict[tuple[str, str], str] = {}
    known_states = {"idle"}
    for sequence in body.get("sequences", []):
        current_state = str(sequence.get("initial_state", "idle"))
        known_states.add(current_state)
        for transition in sequence.get("transitions", []):
            next_state = str(transition["to"])
            message_hex = str(transition["message"]["hex"]).lower()
            transitions[(current_state, message_hex)] = next_state
            current_state = next_state
            known_states.add(next_state)
    return transitions, known_states


def decode_message(target_binary: Path, payload: bytes) -> bool:
    proc = subprocess.run(
        [str(target_binary)],
        input=payload,
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def response_for_state(state: str) -> bytes:
    codes = {
        "proposed": b"220 proposed\r\n",
        "accepted": b"250 accepted\r\n",
        "refused": b"550 refused\r\n",
    }
    return codes.get(state, b"500 invalid-transition\r\n")


def write_state_report(report_path: Path, state: dict) -> None:
    report = {
        "states_visited": sorted(state["states_visited"]),
        "state_count": len(state["states_visited"]),
        "novel_state_count": max(0, len(state["states_visited"]) - 1),
        "transitions_executed": state["transitions_executed"],
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
                continue
            if not decode_message(shared["target_binary"], payload):
                shared["invalid_messages"] += 1
                shared["response_codes"]["500"] = shared["response_codes"].get("500", 0) + 1
                self.wfile.write(b"500 invalid-decode\r\n")
                self.wfile.flush()
                continue

            next_state = shared["transitions"].get((current_state, text))
            if next_state is None:
                shared["invalid_messages"] += 1
                shared["response_codes"]["500"] = shared["response_codes"].get("500", 0) + 1
                self.wfile.write(b"500 invalid-transition\r\n")
                self.wfile.flush()
                continue

            shared["transitions_executed"] += 1
            current_state = next_state
            shared["states_visited"].add(current_state)
            response = response_for_state(current_state)
            code = response[:3].decode("ascii")
            shared["response_codes"][code] = shared["response_codes"].get(code, 0) + 1
            self.wfile.write(response)
            self.wfile.flush()


def serve(*, port: int, target_binary: Path, state_corpus: Path, state_report: Path) -> int:
    transitions, _known_states = load_transition_map(state_corpus)
    shared_state = {
        "target_binary": target_binary,
        "transitions": transitions,
        "states_visited": set(),
        "transitions_executed": 0,
        "sessions": 0,
        "invalid_messages": 0,
        "response_codes": {},
    }

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    with Server(("127.0.0.1", port), Handler) as server:
        server.shared_state = shared_state  # type: ignore[attr-defined]

        def _shutdown(*_args):
            write_state_report(state_report, shared_state)
            server.shutdown()

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
        try:
            server.serve_forever()
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
