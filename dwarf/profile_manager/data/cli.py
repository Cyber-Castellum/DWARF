"""Hand-curated CLI catalogue for /learn/cli.

This file is the single source of truth for the operator-facing CLI
documentation page. It does NOT introspect the argparse parser at runtime
(the parser is large and our presentation needs are richer than argparse
metadata): each entry is hand-written prose + a worked example or two.

The `groups` list is what the page renders. Each group has subcommands
keyed by their full invocation. Anchor paths point at the parser stanza
so the page can deep-link readers to the source of truth.

Discipline (slice 26): no fabricated commands. Every entry below maps
to a parser entry in dwarf/profile_manager/cli.py. Structural rails in
test_cli_catalog enforce slug uniqueness, anchor-path existence, and
example-command shape; semantic accuracy of the prose is reviewed by
hand at slice authoring.
"""
from __future__ import annotations

from typing import Any


CLI_GROUPS: list[dict[str, Any]] = [
    {
        "slug": "scenario",
        "title": "scenario",
        "summary": (
            "Define and execute scenario YAMLs. Scenarios are the atomic unit "
            "of test work — one YAML binds a target, a runtime, a sequence of "
            "primitives, and the assertions the runner expects to record. "
            "Each execution produces one forensic bundle under "
            "<code>dwarf/runs/&lt;run-id&gt;/</code>."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile scenario run <path>",
                "summary": "Run one scenario end to end and emit a forensic bundle.",
                "examples": [
                    {"label": "Library-tier CBOR fuzz", "command": "cardano-profile scenario run dwarf/scenarios/amaru-cbor-tx-body-fuzz.yaml"},
                    {"label": "Devnet runtime", "command": "cardano-profile scenario run dwarf/scenarios/m3-runtime-blockfetch-port-delay-bounded-success.yaml"},
                ],
            },
            {
                "name": "cardano-profile scenario list",
                "summary": "List every scenario currently visible (live corpus + pending).",
                "examples": [
                    {"label": "All scenarios", "command": "cardano-profile scenario list"},
                ],
            },
            {
                "name": "cardano-profile scenario validate <path>",
                "summary": "Validate a scenario YAML against the dwarf v1 schema without running it.",
                "examples": [
                    {"label": "Single file", "command": "cardano-profile scenario validate dwarf/scenarios/pending/my-draft.yaml"},
                ],
            },
            {
                "name": "cardano-profile scenario promote --id <id>",
                "summary": "Promote a pending scenario into the live corpus once its declared promotion blockers clear.",
                "examples": [
                    {"label": "Promote by id", "command": "cardano-profile scenario promote --id my-draft"},
                ],
            },
        ],
    },
    {
        "slug": "compare",
        "title": "compare",
        "summary": (
            "Differential testing. Run the same scenario against both Amaru "
            "and cardano-node with the same seed, then emit a "
            "<code>cross-impl-comparison.json</code> in the cardano-node-side "
            "bundle. Divergence between two implementations on identical "
            "inputs is the signal."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile compare <path>",
                "summary": "Run a scenario through both implementations and write the comparison report.",
                "examples": [
                    {"label": "Library-tier compare", "command": "cardano-profile compare dwarf/scenarios/amaru-cardano-differential-tx-body-fuzz.yaml"},
                ],
            },
        ],
    },
    {
        "slug": "bundle",
        "title": "bundle",
        "summary": (
            "Inspect, verify, sign, and chain forensic bundles. Each bundle is "
            "tamper-evident: <code>chain.json</code> links to the previous run's "
            "hash, and <code>verify</code> recomputes the manifest hash and "
            "validates the chain end-to-end."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile bundle list",
                "summary": "List preserved bundles in the catalog.",
                "examples": [
                    {"label": "All bundles", "command": "cardano-profile bundle list"},
                ],
            },
            {
                "name": "cardano-profile bundle inspect <run-id>",
                "summary": "Print the manifest, hash chain, assertions, and resource snapshot for one run.",
                "examples": [
                    {"label": "Inspect a run", "command": "cardano-profile bundle inspect 20260427T100000Z-abc123"},
                ],
            },
            {
                "name": "cardano-profile bundle promote <run-id>",
                "summary": "Move a run's bundle into the curated set under <code>dwarf/bundles/</code>.",
                "examples": [
                    {"label": "Promote to curated", "command": "cardano-profile bundle promote 20260427T100000Z-abc123"},
                ],
            },
            {
                "name": "cardano-profile bundle audit-trail <run-id> [--json] [--runs-dir <path>]",
                "summary": "Walk the chain-of-custody for a run: prior runs in the hash chain, attestation signers, replay/diff/export descendants. <code>--json</code> emits the same audit-trail as a machine-readable record for evidence pipelines.",
                "examples": [
                    {"label": "Human-readable", "command": "cardano-profile bundle audit-trail 20260427T100000Z-abc123"},
                    {"label": "JSON for tooling", "command": "cardano-profile bundle audit-trail 20260427T100000Z-abc123 --json"},
                ],
            },
        ],
    },
    {
        "slug": "fuzz",
        "title": "fuzz",
        "summary": (
            "Run a fuzz campaign against a registered target. In this V3 "
            "delivery, registered targets are the retained M2 decoder "
            "manifests under <code>dwarf/targets/manifests/</code>."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile fuzz run --target <id>",
                "summary": "Run a fuzz campaign against a registered target.",
                "examples": [
                    {"label": "CBOR tx-body", "command": "cardano-profile fuzz run --target amaru-cbor-decode-tx-body"},
                ],
            },
        ],
    },
    {
        "slug": "target",
        "title": "target",
        "summary": (
            "Inspect registered fuzz targets. Each target is a per-implementation "
            "shim that reads bytes from stdin, invokes one upstream parser or "
            "decoder, and reports the outcome. Manifests live under "
            "<code>dwarf/targets/manifests/</code>."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile target list",
                "summary": "List registered targets.",
                "examples": [
                    {"label": "All targets", "command": "cardano-profile target list"},
                ],
            },
            {
                "name": "cardano-profile target inspect <id>",
                "summary": "Print one target's manifest fields (implementation, language, upstream commit, invariants).",
                "examples": [
                    {"label": "Inspect tx-body shim", "command": "cardano-profile target inspect amaru-cbor-tx-body"},
                ],
            },
        ],
    },
    {
        "slug": "primitive",
        "title": "primitive",
        "summary": (
            "List and describe primitives — the typed building blocks scenarios "
            "reference by name (setup, load, probe, assertion, fault, teardown). "
            "The registry under <code>dwarf/primitives/registry.json</code> is "
            "the canonical mapping. Each entry below is one primitive plus a "
            "scenario that uses it; copy-paste the second example to run the "
            "primitive end-to-end through the scenario harness."
        ),
        "anchor_path": "dwarf/primitives/registry.json",
        "commands": [
            {
                "name": "cardano-profile primitive list",
                "summary": "List every primitive in the registry, grouped by family.",
                "examples": [
                    {"label": "All primitives", "command": "cardano-profile primitive list"},
                ],
            },
            {
                "name": "cardano-profile primitive describe <name>",
                "summary": "Print one primitive's parameter schema and supported runtimes.",
                "examples": [
                    {"label": "Describe a fault primitive", "command": "cardano-profile primitive describe runtime_local_port_delay"},
                ],
            },
            # ---- Bundle-workflow primitives (post-run evidence operations) ----
            {
                "name": "cardano-profile primitive describe runtime_bundle_attestation",
                "summary": "Sign a bundle's manifest with a per-actor key and write the signature into the run dir. Establishes provenance: who staged this bundle, when, and against what manifest hash.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_bundle_attestation"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-bundle-attestation-example-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_bundle_chain_verify",
                "summary": "Walk the hash chain from a target run back to genesis and assert every link's manifest_hash recomputes. Mirrors what <code>cardano-profile verify</code> does, but is invokable as a scenario step so the verdict is itself a forensic bundle.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_bundle_chain_verify"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-bundle-chain-verify-example-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_bundle_replay",
                "summary": "Re-execute a previously recorded run against the current target, write the replay's outputs into a new bundle, and surface a side-by-side compare with the original. Default <code>compare_relpaths</code> covers manifest, assertions, and probes.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_bundle_replay"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-bundle-replay-example-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_bundle_diff",
                "summary": "Diff two bundles by run-id pair (left/right) across configurable relpaths. Produces a structured diff artifact in the new bundle.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_bundle_diff"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-bundle-diff-example-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_bundle_export_sarif",
                "summary": "Render a bundle's findings into SARIF v2.1.0 for upstream tooling (GitHub code scanning, Sonar, etc.). Schema path is configurable; defaults to the v2.1.0 schema bundled with Dwarf.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_bundle_export_sarif"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-bundle-export-sarif-example-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_bundle_timeline",
                "summary": "Roll a set of bundles into a single chronological timeline document — useful for post-incident review or evidence package authoring. Filters by scenario id and/or signature token.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_bundle_timeline"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-bundle-timeline-example-smoke.yaml"},
                ],
            },
            # ---- Coverage primitives ----
            {
                "name": "cardano-profile primitive describe runtime_coverage_report",
                "summary": "Aggregate coverage from one or more AFL++ campaign bundles into an HTML report. <code>merge_mode</code> picks per-input replay, file-level bitmap merge, or both.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_coverage_report"},
                    {"label": "File-level AFL++ merge example", "command": "cardano-profile scenario run dwarf/scenarios/runtime-coverage-report-file-level-aflpp-example-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_aggregate_coverage",
                "summary": "Roll coverage across many bundles (cargo-fuzz + AFL++ campaign IDs) into one merged report. Complements runtime_coverage_report by spanning bundle boundaries.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_aggregate_coverage"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-aggregate-coverage-example-smoke.yaml"},
                ],
            },
            # ---- Fuzz / campaign primitives ----
            {
                "name": "cardano-profile primitive describe runtime_aflpp_campaign",
                "summary": "Drive an AFL++ persistent-mode campaign for a configured target binary, with optional sanitizer toolchain and seed corpus directories. Outputs land in the bundle's outputs/ tree.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_aflpp_campaign"},
                    {"label": "Run an AFL++ smoke", "command": "cardano-profile scenario run dwarf/scenarios/amaru-cargo-fuzz-blockfetch-aflpp-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_fuzz_campaign",
                "summary": "Multi-target × multi-engine campaign orchestrator. Schedules sub-campaigns within a total time budget (cargo-fuzz, AFL++, custom mutator) and writes a unified summary.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_fuzz_campaign"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-fuzz-campaign-example-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_long_campaign",
                "summary": "Long-running campaign harness with periodic checkpoint exports — emits intermediate bundles every <code>checkpoint_seconds</code> so progress is preserved across day-or-multi-day runs.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_long_campaign"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-long-campaign-example-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_crash_triage",
                "summary": "Walk a bundle's crash directory, classify and minimize each input (afl-tmin or libFuzzer-min), and emit a triage record with reproduction commands.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_crash_triage"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_afl_corpus_min",
                "summary": "Run <code>afl-cmin</code> against an AFL++ queue and write the minimised corpus to an output directory. Smaller, equivalent-coverage corpus for downstream campaigns.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_afl_corpus_min"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-afl-corpus-min-example-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_custom_mutator_template",
                "summary": "Drive a libFuzzer custom-mutator harness when a structural mutator implementation is available. Enforces the structural-mutator pattern.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_custom_mutator_template"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-custom-mutator-template-block-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_differential_rule_harness",
                "summary": "Run a differential rule-execution harness — feed one input through both a target and a reference implementation and assert behavioural equivalence.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_differential_rule_harness"},
                ],
            },
            # ---- Static-analysis primitives ----
            {
                "name": "cardano-profile primitive describe runtime_static_analysis_clippy",
                "summary": "Run <code>cargo clippy</code> against a configured crate directory and write the report into the bundle's outputs/. The report itself is the evidence; the harness does not promote any clippy warning into a verdict.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_static_analysis_clippy"},
                    {"label": "Run the example scenario", "command": "cardano-profile scenario run dwarf/scenarios/runtime-static-analysis-clippy-example-smoke.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_static_analysis_audit",
                "summary": "Run <code>cargo audit</code> for advisory-database checks on a crate. Captures the JSON report and the advisory IDs as evidence.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_static_analysis_audit"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_static_analysis_deny",
                "summary": "Run <code>cargo deny</code> against a crate's policy (licences, advisories, banned crates, sources) and write the report.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_static_analysis_deny"},
                ],
            },
            # ---- Extraction primitives ----
            {
                "name": "cardano-profile primitive describe runtime_cardano_lsq_extract",
                "summary": "Drive the LSQ (LocalStateQuery) extractor against a running cardano-node socket; capture <code>DebugEpochState</code> and friends as a JSON record. Era-specific; supply <code>era</code> + <code>network_magic</code>.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_cardano_lsq_extract"},
                    {"label": "Extract DebugEpochState", "command": "cardano-profile scenario run dwarf/scenarios/cardano-lsq-extract-debug-epoch-state.yaml"},
                ],
            },
            {
                "name": "cardano-profile primitive describe runtime_corpus_synthesize",
                "summary": "Generate structurally-valid CBOR seed inputs for a target by combining a grammar dictionary with a structure spec. Output count and selection strategy are configurable.",
                "examples": [
                    {"label": "Describe the schema", "command": "cardano-profile primitive describe runtime_corpus_synthesize"},
                    {"label": "Synthesize blockfetch seeds", "command": "cardano-profile scenario run dwarf/scenarios/runtime-corpus-synthesize-blockfetch-smoke.yaml"},
                ],
            },
        ],
    },
    {
        "slug": "dashboard",
        "title": "dashboard",
        "summary": (
            "Generate the static dashboard or serve it over HTTP. Live mode "
            "polls the configured target host over read-only SSH; "
            "mutating endpoints (run, compare, paste, promote) require the "
            "dashboard token and are serialised by a global lock."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile dashboard generate",
                "summary": "Render the dashboard HTML to disk and exit.",
                "examples": [
                    {"label": "Default output", "command": "cardano-profile dashboard generate"},
                ],
            },
            {
                "name": "cardano-profile dashboard serve",
                "summary": "Start the dashboard HTTP server with live SSH polling.",
                "examples": [
                    {"label": "Local dev", "command": "cardano-profile dashboard serve --bind 127.0.0.1 --port 8787 --token dwarf"},
                    {"label": "Public bind", "command": "cardano-profile dashboard serve --bind 0.0.0.0 --port 8787 --token \"$(cat ~/.dwarf/token)\""},
                ],
            },
            {
                "name": "cardano-profile dashboard status",
                "summary": "Print the on-disk dashboard state, profile catalogue, and config presence summary.",
                "examples": [
                    {"label": "Status", "command": "cardano-profile dashboard status"},
                ],
            },
        ],
    },
    {
        "slug": "moog",
        "title": "moog",
        "summary": (
            "Read-only Moog deployment checks and local requester workflow "
            "planning for Cardano Preprod. These helpers validate Dwarf-side "
            "state, local asset directories, and future create-test commands "
            "without submitting transactions or launching Antithesis."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile moog bootstrap --json",
                "summary": "Show the opt-in Moog bootstrap plan. Without <code>--approve</code>, this changes no remote state; with approval, it creates only the safe deploy/secrets directory skeleton and writes an operator plan file.",
                "examples": [
                    {"label": "Plan only", "command": "cardano-profile moog bootstrap --json"},
                    {"label": "Approved skeleton setup", "command": "cardano-profile moog bootstrap --approve --json"},
                ],
            },
            {
                "name": "cardano-profile moog status --json",
                "summary": "Check Moog binary, deploy directories, public wallet metadata, MPFS/token config, and oracle unit state without reading wallet secrets.",
                "examples": [
                    {"label": "Deployment health", "command": "cardano-profile moog status --json"},
                    {"label": "Command preview", "command": "cardano-profile moog status --dry-run"},
                ],
            },
            {
                "name": "cardano-profile moog asset scaffold --to <dir> --json",
                "summary": "Create a target-agnostic local compose asset skeleton. The scaffold intentionally does not embed PATs, wallet paths, Moog token values, Docker auth, Antithesis credentials, or target repo details.",
                "examples": [
                    {"label": "Create local asset skeleton", "command": "cardano-profile moog asset scaffold --to /tmp/moog-asset --json"},
                ],
            },
            {
                "name": "cardano-profile moog asset validate --asset-dir <dir> --json",
                "summary": "Validate local asset structure: directory, compose file, services section, and secret-like filenames.",
                "examples": [
                    {"label": "Validate local assets", "command": "cardano-profile moog asset validate --asset-dir /tmp/moog-asset --json"},
                ],
            },
            {
                "name": "cardano-profile moog readiness --repo <org/repo> --github-user <user> --json",
                "summary": "Read-only requester readiness check: requester wallet metadata/funding, GitHub profile vkey and CODEOWNERS, Moog user/role facts, and whitelist facts.",
                "examples": [
                    {"label": "Requester readiness", "command": "cardano-profile moog readiness --repo example-org/example-repo --github-user example-user --json"},
                ],
            },
            {
                "name": "cardano-profile moog registration-plan --repo <org/repo> --github-user <user> --json",
                "summary": "Plan requester registration steps and show the required moog.vkey content, CODEOWNERS line, and requester commands without submitting.",
                "examples": [
                    {"label": "Registration plan", "command": "cardano-profile moog registration-plan --repo example-org/example-repo --github-user example-user --json"},
                ],
            },
            {
                "name": "cardano-profile moog create-test-plan --asset-dir <dir> --repo <org/repo> --github-user <user> --directory <path> --commit <sha> --json",
                "summary": "Generate the future <code>moog requester create-test</code> command and validate required metadata. This is dry-run planning only.",
                "examples": [
                    {"label": "Create-test dry run", "command": "cardano-profile moog create-test-plan --asset-dir /tmp/moog-asset --repo example-org/example-repo --github-user example-user --directory antithesis --commit abc123 --json"},
                ],
            },
            {
                "name": "cardano-profile moog preflight --asset-dir <dir> --repo <org/repo> --github-user <user> --directory <path> --commit <sha> --json",
                "summary": "Run the combined readiness view: Moog health, requester readiness, local asset validation, and create-test command planning. It still performs no live submission.",
                "examples": [
                    {"label": "Combined preflight", "command": "cardano-profile moog preflight --asset-dir /tmp/moog-asset --repo example-org/example-repo --github-user example-user --directory antithesis --commit abc123 --json"},
                ],
            },
            {
                "name": "cardano-profile moog create-test --repo <org/repo> --github-user <user> --directory <dir> --commit <sha> [--try <N>] [--duration <hours>] [--no-faults] [--approve]",
                "summary": "Submit a live Antithesis test run through Moog (the same call CF's cardano-node workflow uses). Without <code>--approve</code> it prints the exact <code>moog requester create-test</code> command (dry-run); with <code>--approve</code> it submits the on-chain transaction (the wallet passphrase + GitHub PAT are sourced from on-host files, never logged). The Moog oracle validates registration + whitelist, then CF's agent launches it on Antithesis.",
                "examples": [
                    {"label": "Dry-run (no submission)", "command": "cardano-profile moog create-test --repo Cyber-Castellum/DWARF --github-user J-GainSec --directory antithesis/cardano_node_dwarf --commit <sha> --no-faults --json"},
                    {"label": "Live no-faults smoke (1h)", "command": "cardano-profile moog create-test --repo Cyber-Castellum/DWARF --github-user J-GainSec --directory antithesis/cardano_node_dwarf --commit <sha> --duration 1 --no-faults --approve --json"},
                ],
            },
            {
                "name": "cardano-profile moog test-status <test-run-id> --json",
                "summary": "Poll a submitted run's on-chain phase via <code>moog facts test-runs</code> (pending → accepted → terminal). The full triage/findings live in the Antithesis tenant dashboard; this reports the Moog-side phase.",
                "examples": [
                    {"label": "Check phase", "command": "cardano-profile moog test-status e39d2ddf... --json"},
                ],
            },
        ],
    },
    {
        "slug": "antithesis",
        "title": "antithesis",
        "summary": (
            "Render a profile into a hermetic Antithesis test bundle — the second "
            "execution backend alongside the local devnet, from one profile "
            "definition. A single-node profile (e.g. closed Amaru) yields one node "
            "+ the workload; a mixed profile yields Haskell <code>cardano-node</code> "
            "+ Amaru + the workload, where the workload drives the same fuzzed CBOR "
            "at both and asserts they agree (the cross-implementation differential). "
            "The emitted directory is the same asset-dir that <code>moog asset "
            "validate</code> and <code>moog preflight</code> consume. Building a "
            "bundle submits nothing and launches nothing; it stops at ready-to-submit."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile antithesis build <profile> [--scenario <s>] [--out <dir>] [--registry <ref>] [--tag <tag>] [--json]",
                "summary": "Render the Antithesis bundle for a profile: <code>config/docker-compose.yaml</code> (registry images, <code>platform: linux/amd64</code>, <code>init</code>, healthchecks), <code>setup-complete.sh</code>, an <code>antithesis/test/</code> command, and a README. Single-node profiles run the <code>drive-once</code> driver; mixed profiles add a Haskell <code>cardano-node-devnet</code> service (devnet env baked in) and run the <code>drive-differential</code> driver. Defaults write under <code>antithesis/</code> using the registry from Moog config.",
                "examples": [
                    {"label": "Single closed-Amaru bundle", "command": "cardano-profile antithesis build profile-l-amaru-closed-devnet --out antithesis/amaru-single --json"},
                    {"label": "Mixed Haskell+Amaru bundle (differential)", "command": "cardano-profile antithesis build profile-c-mixed-haskell-amaru-minimal --out antithesis/mixed-haskell-amaru --json"},
                    {"label": "Pin registry and tag", "command": "cardano-profile antithesis build profile-l-amaru-closed-devnet --registry us-central1-docker.pkg.dev/molten-verve-216720/cardano-repository --tag v1 --json"},
                ],
            },
        ],
    },
    {
        "slug": "testcase",
        "title": "testcase",
        "summary": (
            "Triage state machine commands. Cluster runs into buckets, manage "
            "the replay and compare queues, run minimization, and promote a "
            "case from candidate to confirmed-anomaly."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile testcase list",
                "summary": "List every catalogued test case across buckets.",
                "examples": [
                    {"label": "All cases", "command": "cardano-profile testcase list"},
                ],
            },
            {
                "name": "cardano-profile testcase buckets list",
                "summary": "List bucket groupings (by classification, triage reason, target).",
                "examples": [
                    {"label": "Buckets", "command": "cardano-profile testcase buckets list"},
                ],
            },
            {
                "name": "cardano-profile testcase replay-queue list",
                "summary": "Show pending replay work.",
                "examples": [
                    {"label": "Pending replays", "command": "cardano-profile testcase replay-queue list"},
                ],
            },
        ],
    },
]


def cli_groups() -> list[dict[str, Any]]:
    """Return a defensive copy of the CLI catalogue."""
    return [dict(g, commands=[dict(c) for c in g["commands"]]) for g in CLI_GROUPS]
