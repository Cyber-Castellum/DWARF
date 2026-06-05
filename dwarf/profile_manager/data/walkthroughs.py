"""Hand-curated walkthrough catalog for /learn/walkthroughs.

Four narrated paths from the master spec — Run your first scenario,
Read a bundle, Add a new fuzz target, Compare two implementations.
Each step's prose explains the action; refs link to a real file
(anchor_path; Path.exists() rail) or a real page (url; helper-derived
or generic).

Helper-derived URLs are called at module-load to capture the URL string
at the call site. The URL-helper integrity test catches drift if the
helper's contract changes (or if an author inlines URL strings).

Schema:
    walkthrough = {slug, title, intro, steps: list[step]}
    step        = {number, title, prose, refs: list[ref]}
    ref         = {label, url} | {label, anchor_path}  (xor invariant)
"""
from __future__ import annotations

from typing import Any

from profile_manager.data.compare import _bundle_inspector_url
from profile_manager.data.operate_bundles import _bundle_catalog_url
from profile_manager.data.operate_profiles import _profile_url
from profile_manager.data.operate_targets import _target_url


WALKTHROUGHS: list[dict[str, Any]] = [
    {
        "slug": "run-your-first-scenario",
        "title": "Run your first scenario",
        "intro": "Pick a scenario, run it, open the bundle. The end-to-end loop in three steps.",
        "steps": [
            {
                "number": 1,
                "title": "Pick a scenario",
                "prose": (
                    "Browse the catalog of available scenarios. Each YAML defines one test — "
                    "which target to feed, what kind of inputs, how many iterations, what to assert."
                ),
                "refs": [
                    {"label": "Scenarios catalog", "url": "/operate/scenarios"},
                    {"label": "scenarios directory", "anchor_path": "dwarf/scenarios/"},
                ],
            },
            {
                "number": 2,
                "title": "Run it",
                "prose": (
                    "Run via the CLI: cardano-profile scenario run dwarf/scenarios/<scenario-id>.yaml. "
                    "The framework executes the scenario and writes a forensic bundle to dwarf/runs/<run-id>/."
                ),
                "refs": [
                    {"label": "operator handbook", "anchor_path": "dwarf/docs/operator-handbook.md"},
                    {"label": "scenario runner", "anchor_path": "dwarf/profile_manager/scenario.py"},
                ],
            },
            {
                "number": 3,
                "title": "Open the bundle inspector",
                "prose": (
                    "The run id appears in the recent runs list. Click into the inspector to see the manifest, "
                    "captured events, assertion outcomes, and tamper-check verdict."
                ),
                "refs": [
                    {"label": "Recent runs", "url": "/operate/runs"},
                    {"label": "example inspector URL", "url": _bundle_inspector_url("20260425T110742Z-abc123")},
                ],
            },
        ],
    },
    {
        "slug": "read-a-bundle",
        "title": "Read a bundle",
        "intro": "A bundle is a tar.gz of one run's full evidence. Here's how to inspect it without opening the archive.",
        "steps": [
            {
                "number": 1,
                "title": "Find the bundle",
                "prose": (
                    "Bundles are operator-curated archives at dwarf/bundles/<run-id>.tar.gz. "
                    "The catalog page lists every bundle preserved on this host."
                ),
                "refs": [
                    {"label": "Bundles catalog", "url": "/operate/bundles"},
                ],
            },
            {
                "number": 2,
                "title": "Read the manifest",
                "prose": (
                    "Every run dir at dwarf/runs/<run-id>/ contains manifest.json — scenario id, target, runtime, "
                    "exit status, timestamps, actor, resource snapshot, hash-chain entry."
                ),
                "refs": [
                    {"label": "forensic-bundle-format", "anchor_path": "dwarf/docs/forensic-bundle-format.md"},
                    {"label": "forensic.py export_bundle", "anchor_path": "dwarf/profile_manager/forensic.py"},
                ],
            },
            {
                "number": 3,
                "title": "Inspect events and probes",
                "prose": (
                    "log.ndjson is the structured event log (one event per line). probes/<name>.ndjson holds "
                    "time-series probe samples. The inspector page surfaces both."
                ),
                "refs": [
                    {"label": "primitives reference", "anchor_path": "dwarf/docs/primitives-reference.md"},
                ],
            },
            {
                "number": 4,
                "title": "Verify the chain",
                "prose": (
                    "Each bundle's chain.json links to the previous run's hash. The framework's tamper-check "
                    "rejects any bundle whose hash chain has been altered."
                ),
                "refs": [
                    {"label": "tamper-check overview", "anchor_path": "dwarf/docs/forensic-bundle-format.md"},
                ],
            },
        ],
    },
    {
        "slug": "add-a-new-fuzz-target",
        "title": "Add a new fuzz target",
        "intro": "Fuzz targets are per-implementation shims around an Amaru or cardano-node decoder. Adding one is a manifest plus a small wrapper.",
        "steps": [
            {
                "number": 1,
                "title": "Author the manifest",
                "prose": (
                    "Manifests live at dwarf/targets/manifests/<id>.yaml. They declare the target id, "
                    "implementation, language, upstream commit, input format, and invariants."
                ),
                "refs": [
                    {"label": "Targets catalog", "url": "/operate/targets"},
                    {"label": "manifests directory", "anchor_path": "dwarf/targets/manifests/"},
                ],
            },
            {
                "number": 2,
                "title": "Build the shim",
                "prose": (
                    "Per-implementation harness code lives at dwarf/targets/amaru/ or dwarf/targets/cardano-node/. "
                    "The shim reads input bytes from stdin, invokes one upstream function, and reports the outcome."
                ),
                "refs": [
                    {"label": "amaru harness", "anchor_path": "dwarf/targets/amaru/"},
                    {"label": "cardano-node harness", "anchor_path": "dwarf/targets/cardano-node/"},
                ],
            },
            {
                "number": 3,
                "title": "Author a scenario that uses it",
                "prose": (
                    "Scenario YAML references the new target id. Reuse a primitive (e.g., cbor_fuzz_target) "
                    "or compose a custom sequence."
                ),
                "refs": [
                    {"label": "primitives registry", "anchor_path": "dwarf/primitives/registry.json"},
                    {"label": "scenarios directory", "anchor_path": "dwarf/scenarios/"},
                ],
            },
            {
                "number": 4,
                "title": "Run and inspect",
                "prose": (
                    "Run the new scenario; the bundle records what happened. If it crashes or hangs, "
                    "the framework captures it as a candidate finding for triage."
                ),
                "refs": [
                    {"label": "Recent runs", "url": "/operate/runs"},
                ],
            },
        ],
    },
    {
        "slug": "compare-two-implementations",
        "title": "Compare two implementations",
        "intro": "Differential testing runs the same scenario against Amaru and cardano-node, then compares outcomes. Divergence is the signal.",
        "steps": [
            {
                "number": 1,
                "title": "Pick a comparable scenario",
                "prose": (
                    "Most scenarios in the catalog are impl-neutral and can run against either implementation. "
                    "The comparison primitive feeds identical inputs to both with the same random seed."
                ),
                "refs": [
                    {"label": "Scenarios catalog", "url": "/operate/scenarios"},
                ],
            },
            {
                "number": 2,
                "title": "Run the compare",
                "prose": (
                    "From the dashboard's /operate/compare runner or via cardano-profile compare <scenario-path>. "
                    "Two bundles are produced — one per implementation — plus a comparison artifact in the "
                    "cardano-node-side bundle."
                ),
                "refs": [
                    {"label": "Compare entry page", "url": "/operate/compare"},
                    {"label": "scenario.py compare logic", "anchor_path": "dwarf/profile_manager/scenario.py"},
                ],
            },
            {
                "number": 3,
                "title": "Read the standing dashboard",
                "prose": (
                    "The /operate/compare page is the standing differential-testing surface — the latest "
                    "comparison per scenario, with raw and per-active-peer-normalized metrics, and explicit "
                    "asymmetry markers where one side doesn't emit a metric."
                ),
                "refs": [
                    {"label": "Cross-impl comparison", "url": "/operate/compare"},
                ],
            },
            {
                "number": 4,
                "title": "Drill into the bundles",
                "prose": (
                    "Each comparison row links to its underlying Amaru and cardano-node bundles. "
                    "Open both inspectors side by side to read the raw evidence."
                ),
                "refs": [
                    {"label": "Bundles catalog", "url": "/operate/bundles"},
                ],
            },
        ],
    },
]


def walkthrough_entries() -> list[dict[str, Any]]:
    """Return walkthroughs as render-ready dicts (defensive copy)."""
    return [dict(w) for w in WALKTHROUGHS]
