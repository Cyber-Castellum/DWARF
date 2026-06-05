"""Hand-curated architecture-diagram catalog for /learn/architecture.

The discipline carried from the slice-4 design: do not fabricate. Each
node is anchored to a real on-disk path or marked needs_source=True.
The integrity test test_anchor_paths_exist_on_disk catches drift.

Entry shape — NODES:
    {
        "slug":          kebab-case fragment for URL hash and HTML id
        "label":         display name (e.g., "Scenario runner")
        "role":          "input" | "runtime" | "output" | "triage"
        "description":   one-paragraph plain-English text
        "anchor_path":   repo-relative path to canonical file or dir, or ""
        "anchor_symbol": optional symbol within the file; "" otherwise
        "needs_source":  True iff anchor_path is empty
        "concept_slug":  /learn/concepts entry slug for cross-link, or None
    }

Entry shape — EDGES:
    {
        "from":   source node slug
        "to":     target node slug
        "label":  edge label (empty if unlabeled)
        "branch": "amaru" | "cardano-node" | "compare" | None
    }

The differential-testing dimension is rendered via two Runner->Bundle
edges (branch="amaru" and branch="cardano-node") plus a Bundle->Lifecycle
merge edge (branch="compare"). The test test_edges_include_differential_fork
guards against any of those three edges being accidentally removed.
"""
from __future__ import annotations


NODES: list[dict] = [
    {
        "slug": "corpora",
        "label": "Corpora",
        "role": "input",
        "description": (
            "Seed inputs for M2 serialization/deserialization work. "
            "The retained corpora under dwarf/corpora/ contain CBOR and "
            "mini-protocol byte examples organized by target family."
        ),
        "anchor_path": "dwarf/corpora/",
        "anchor_symbol": "",
        "needs_source": False,
        "concept_slug": None,
    },
    {
        "slug": "targets",
        "label": "Targets",
        "role": "input",
        "description": (
            "Per-implementation manifests describing the retained M2 "
            "decoder targets. Each manifest names a small shim around an "
            "Amaru or cardano-node parser/decoder and the input format it "
            "expects."
        ),
        "anchor_path": "dwarf/targets/manifests/",
        "anchor_symbol": "",
        "needs_source": False,
        "concept_slug": "fuzzer-backend",
    },
    {
        "slug": "primitives",
        "label": "Primitives",
        "role": "input",
        "description": (
            "Reusable scenario building blocks: setup, load, probe, "
            "assertion, fault, teardown. Each primitive has a JSON "
            "schema; scenarios reference primitives by name. The "
            "primitive registry is the central catalog."
        ),
        "anchor_path": "dwarf/primitives/registry.json",
        "anchor_symbol": "",
        "needs_source": False,
        "concept_slug": "primitive",
    },
    {
        "slug": "scenarios",
        "label": "Scenarios",
        "role": "input",
        "description": (
            "Declarative YAML test definitions. A scenario binds a "
            "target, a runtime, a sequence of primitives, and the "
            "assertions that determine pass/fail. Scenarios are the "
            "operator-authorable surface of the framework."
        ),
        "anchor_path": "dwarf/scenarios/",
        "anchor_symbol": "",
        "needs_source": False,
        "concept_slug": "scenario",
    },
    {
        "slug": "runner",
        "label": "Scenario runner",
        "role": "runtime",
        "description": (
            "Loads a scenario, resolves its primitives, drives the "
            "target through setup -> load -> probe -> assertion -> "
            "teardown, and emits a forensic bundle. Drives both "
            "single-target runs and the cross-implementation "
            "comparison path that produces two bundles for one scenario."
        ),
        "anchor_path": "dwarf/profile_manager/scenario.py",
        "anchor_symbol": "",
        "needs_source": False,
        "concept_slug": "differential-testing",
    },
    {
        "slug": "fault_injection",
        "label": "Fault injection",
        "role": "runtime",
        "description": (
            "Injected adversarial conditions: partitions, packet "
            "drops, port-level delay/drop variants. Fault primitives "
            "are scheduled by the runner and applied via the operating "
            "system or a sidecar process during the load phase."
        ),
        "anchor_path": "dwarf/primitives/fault/",
        "anchor_symbol": "",
        "needs_source": False,
        "concept_slug": "fault-family",
    },
    {
        "slug": "telemetry",
        "label": "Telemetry capture",
        "role": "runtime",
        "description": (
            "Host, process, network, disk, and runtime observation. "
            "Target-side counters and structured events are recorded "
            "alongside externally-observed metrics. Raw streams are "
            "preserved verbatim in the bundle by default."
        ),
        "anchor_path": "dwarf/profile_manager/telemetry.py",
        "anchor_symbol": "",
        "needs_source": False,
        "concept_slug": "observer-event",
    },
    {
        "slug": "bundle",
        "label": "Forensic bundle",
        "role": "output",
        "description": (
            "Tamper-evident artifact for one run: manifest, observer "
            "events, target events, captured stdout/stderr, resource "
            "snapshots. Reproducible, signable, replayable. Cross-impl "
            "compare emits a comparison artifact in the cardano-node-side "
            "bundle that references both runs."
        ),
        "anchor_path": "dwarf/profile_manager/forensic.py",
        "anchor_symbol": "export_bundle",
        "needs_source": False,
        "concept_slug": "bundle",
    },
    {
        "slug": "lifecycle",
        "label": "Lifecycle",
        "role": "triage",
        "description": (
            "Triage state machine across many bundles. Buckets group "
            "related cases by classification, triage reason, and "
            "target implementation. Replay, compare, and minimization "
            "states track progress without conflating evidence states."
        ),
        "anchor_path": "dwarf/profile_manager/testcase_lifecycle.py",
        "anchor_symbol": "",
        "needs_source": False,
        "concept_slug": "lifecycle",
    },
    {
        "slug": "dashboard",
        "label": "Dashboard",
        "role": "triage",
        "description": (
            "Read-only presentation surface. Routes derive their "
            "state from the runs, bundles, scenarios, and "
            "currentstatus documents already on disk; no curated "
            "lists, no fabrication. This page is rendered by the "
            "dashboard."
        ),
        "anchor_path": "dwarf/profile_manager/dashboard.py",
        "anchor_symbol": "",
        "needs_source": False,
        "concept_slug": None,
    },
]


EDGES: list[dict] = [
    {"from": "corpora", "to": "scenarios", "label": "", "branch": None},
    {"from": "targets", "to": "scenarios", "label": "", "branch": None},
    {"from": "primitives", "to": "scenarios", "label": "", "branch": None},
    {"from": "scenarios", "to": "runner", "label": "", "branch": None},
    {"from": "fault_injection", "to": "runner", "label": "", "branch": None},
    {"from": "telemetry", "to": "bundle", "label": "", "branch": None},
    {"from": "runner", "to": "bundle", "label": "amaru", "branch": "amaru"},
    {"from": "runner", "to": "bundle", "label": "cardano-node", "branch": "cardano-node"},
    {"from": "bundle", "to": "lifecycle", "label": "compare", "branch": "compare"},
    {"from": "lifecycle", "to": "dashboard", "label": "", "branch": None},
]


def architecture_nodes() -> list[dict]:
    """Return the architecture node catalog (a copy)."""
    return [dict(n) for n in NODES]


def architecture_edges() -> list[dict]:
    """Return the architecture edge catalog (a copy)."""
    return [dict(e) for e in EDGES]
