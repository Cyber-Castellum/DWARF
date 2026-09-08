#!/usr/bin/env python3
"""Stop faults, then require both victims and the Amaru-fed consumer to recover."""

import json
import os
import subprocess
import time

from antithesis.assertions import sometimes
from miniprotocol_observer import observe_runtime


stop_faults = os.environ.get("ANTITHESIS_STOP_FAULTS")
if stop_faults:
    subprocess.run([stop_faults, "180"], check=False, timeout=20)

deadline = time.monotonic() + 150
result = observe_runtime()
recovered = False
while time.monotonic() < deadline:
    inputs = result["inputs"]
    recovered = (
        all(inputs["recovered"].values())
        and inputs["converged"]
        and inputs["control_progress"]
        and min(inputs["post_setup_counts"].values()) > 0
        and not any(inputs["fatal"].values())
    )
    if recovered:
        break
    time.sleep(2)
    result = observe_runtime()

details = {
    "recovered": result["inputs"]["recovered"],
    "converged": result["inputs"]["converged"],
    "post_setup_counts": result["inputs"]["post_setup_counts"],
    "tips": result["tips"],
}
sometimes(recovered, "mixed_sm_targets_and_consumer_recovered", details)
sometimes(
    result["inputs"]["converged"],
    "mixed_sm_control_and_consumer_converged",
    details,
)
print(json.dumps({**result, "recovered": recovered}, sort_keys=True), flush=True)

