#!/usr/bin/env python3
"""Bounded active-fault observation of continuing paired-target SP4 fuzzing."""

import json
import time

from miniprotocol_observer import emit_assertions, observe_runtime


deadline = time.monotonic() + 60
result = observe_runtime()
while (
    min(result["inputs"]["post_setup_counts"].values()) < 4
    and time.monotonic() < deadline
):
    time.sleep(1)
    result = observe_runtime()
emit_assertions(result, "parallel-driver")
print(json.dumps(result, sort_keys=True), flush=True)

