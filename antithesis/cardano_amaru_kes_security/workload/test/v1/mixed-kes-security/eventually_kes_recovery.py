#!/usr/bin/env python3
"""Require control progress and a classifiable KES encounter after faults stop."""

import json
import os
import time

from antithesis.assertions import sometimes
from kes_observer import control_tips, run_bounded


if __name__ == "__main__":
    before_p1, before_consumer = control_tips()
    result = run_bounded(
        "eventually", float(os.environ.get("KES_RECOVERY_SECONDS", "120"))
    )
    deadline = time.monotonic() + float(os.environ.get("KES_CONTROL_SECONDS", "120"))
    after_p1, after_consumer = control_tips()
    while time.monotonic() < deadline:
        if (
            after_p1.get("status") == "ok"
            and after_consumer.get("status") == "ok"
            and after_consumer.get("slot", -1) > before_consumer.get("slot", -1)
        ):
            break
        time.sleep(2)
        after_p1, after_consumer = control_tips()
    recovered = (
        result["evaluation"]["classifiable"]
        and after_p1.get("status") == "ok"
        and after_consumer.get("status") == "ok"
        and after_consumer.get("slot", -1) > before_consumer.get("slot", -1)
    )
    details = {
        "recovered": recovered,
        "before_p1": before_p1,
        "before_consumer": before_consumer,
        "after_p1": after_p1,
        "after_consumer": after_consumer,
    }
    sometimes(
        recovered,
        "mixed_kes_control_recovers_and_attack_remains_classifiable",
        details,
    )
    print(json.dumps(details, sort_keys=True), flush=True)
