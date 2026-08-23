#!/usr/bin/env python3
"""Readiness-gated, exactly-once valid fee-boundary observations."""

import json
import os

from mixed_phase1 import (
    default_transports,
    emit_assertions,
    emit_readiness_assertion,
    load_corpus,
    probe_valid_boundaries,
    public_case_result,
    report_command_error,
)


def main() -> None:
    corpus = load_corpus(os.environ.get("PHASE1_FIXTURE_DIR", "/fixture"))
    transports = default_transports(
        os.environ.get(
            "AMARU_SUBMIT_URL", "http://amaru-relay-1.example:3011/api/submit/tx"
        ),
        os.environ.get(
            "CARDANO_SUBMIT_URL", "http://cardano-submit-api.example:8090/api/submit/tx"
        ),
        timeout=2.0,
    )
    execution = probe_valid_boundaries(
        corpus,
        transports,
        readiness_attempts=int(os.environ.get("PHASE1_READY_ATTEMPTS", "10")),
    )

    readiness_fixture, readiness_result = execution["readiness"]
    emit_readiness_assertion(readiness_fixture, readiness_result)
    emit_assertions(readiness_fixture, readiness_result)
    print(
        json.dumps(
            {"phase": "readiness", **public_case_result(readiness_fixture, readiness_result)},
            sort_keys=True,
        ),
        flush=True,
    )

    for fixture, result in execution["valid"]:
        emit_assertions(fixture, result)
        print(
            json.dumps(
                {"phase": "valid-boundary", **public_case_result(fixture, result)},
                sort_keys=True,
            ),
            flush=True,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        report_command_error("first_fee_valid_boundaries", error)
