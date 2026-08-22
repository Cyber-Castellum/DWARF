#!/usr/bin/env python3
"""One mixed phase-1 differential observation during fault injection."""

import json
import os

from mixed_phase1 import (
    default_transports,
    emit_assertions,
    load_fixture,
    observe_differential,
    public_result,
)


def main() -> None:
    fixture = load_fixture(os.environ.get("PHASE1_FIXTURE_DIR", "/fixture"))
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
    print(json.dumps(public_result(result), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
