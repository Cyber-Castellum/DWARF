#!/usr/bin/env python3
"""Continuously sample containment without treating injected downtime as panic."""

import json

from antithesis.assertions import always, sometimes
from miniprotocol_observer import observe_runtime


result = observe_runtime()
inputs = result["inputs"]
details = {
    "post_setup_counts": inputs["post_setup_counts"],
    "targets_reachable": inputs["targets_reachable"],
    "fatal": inputs["fatal"],
}
always(
    not inputs["fatal"]["cardano"],
    "mixed_sm_cardano_no_panic_or_fatal_termination",
    details,
)
always(
    not inputs["fatal"]["amaru"],
    "mixed_sm_amaru_no_panic_or_fatal_termination",
    details,
)
sometimes(
    min(inputs["post_setup_counts"].values()) > 0
    and all(inputs["targets_reachable"].values()),
    "mixed_sm_illegal_sessions_contained_and_later_sessions_usable",
    details,
)
sometimes(
    inputs["control_progress"],
    "mixed_sm_honest_control_progress_continued",
    details,
)
sometimes(
    inputs["unrelated_peers_usable"],
    "mixed_sm_unrelated_peer_sessions_usable",
    details,
)
print(json.dumps(result, sort_keys=True), flush=True)

