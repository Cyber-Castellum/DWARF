#!/usr/bin/env python3
"""One Antithesis-chosen, replay-safe negative fee observation."""

import json
import os

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
    )
    result = observe_differential(fixture.payload, transports)
    emit_assertions(fixture, result)
    print(json.dumps(public_case_result(fixture, result), sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        report_command_error("parallel_driver_underfee_corpus", error)
