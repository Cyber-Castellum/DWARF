#!/usr/bin/env python3
"""Bounded post-fault probe for restoration of phase-1 agreement."""

import json
import os
import time

from antithesis.random import random_choice

from mixed_phase1 import (
    default_transports,
    emit_assertions,
    load_corpus,
    observe_differential,
    public_case_result,
    report_command_error,
    select_negative_case,
)


def main() -> None:
    corpus = load_corpus(os.environ.get("PHASE1_FIXTURE_DIR", "/fixture"))
    fixture = select_negative_case(corpus, random_choice)
    transports = default_transports(
        os.environ.get(
            "AMARU_SUBMIT_URL", "http://amaru-relay-1.example:3011/api/submit/tx"
        ),
        os.environ.get(
            "CARDANO_SUBMIT_URL", "http://cardano-submit-api.example:8090/api/submit/tx"
        ),
        timeout=2.0,
    )
    deadline = time.monotonic() + float(os.environ.get("PHASE1_RECOVERY_SECS", "10"))
    result = None
    while time.monotonic() < deadline:
        result = observe_differential(fixture.payload, transports)
        if result["phase1_agreement"] is True:
            break
        time.sleep(0.5)
    if result is None:
        result = observe_differential(fixture.payload, transports)
    emit_assertions(fixture, result, recovery=True)
    print(json.dumps(public_case_result(fixture, result), sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        report_command_error("eventually_underfee_recovery", error)
