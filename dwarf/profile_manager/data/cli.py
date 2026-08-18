"""Hand-curated CLI catalogue for /learn/cli.

This file is the single source of truth for the operator-facing CLI
documentation page. It does NOT introspect the argparse parser at runtime
(the parser is large and our presentation needs are richer than argparse
metadata): each entry is hand-written prose + a worked example or two.

The `groups` list is what the page renders. Each group has subcommands
keyed by their full invocation. Anchor paths point at the parser stanza
so the page can deep-link readers to the source of truth.

Discipline (slice 26): no fabricated commands. Every entry below maps
to a real subcommand of dwarf/cardano-profile — verified against the
running parser. Introspection-only surfaces (browsing the full target
and primitive catalogues) live in the dashboard at /operate/targets and
/operate/primitives, not the CLI, and are noted as such.
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
            "<code>dwarf/runs/&lt;run-id&gt;/</code>. Subcommands: "
            "<code>run</code>, <code>new</code>, <code>validate</code>, "
            "<code>verify</code>."
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
                "name": "cardano-profile scenario new --template <template-id> --name <id>",
                "summary": "Scaffold a new scenario YAML from an existing template into the writable catalog.",
                "examples": [
                    {"label": "From a template", "command": "cardano-profile scenario new --template amaru-cbor-tx-body-fuzz --name my-draft"},
                ],
            },
            {
                "name": "cardano-profile scenario validate <path>",
                "summary": "Validate a scenario YAML against the dwarf v1 schema without running it. Add <code>--semantic</code> for deeper checks.",
                "examples": [
                    {"label": "Single file", "command": "cardano-profile scenario validate dwarf/scenarios/amaru-cbor-tx-body-fuzz.yaml"},
                    {"label": "Semantic pass", "command": "cardano-profile scenario validate --semantic dwarf/scenarios/amaru-cbor-tx-body-fuzz.yaml"},
                ],
            },
            {
                "name": "cardano-profile scenario verify <path>",
                "summary": "Re-run a scenario and verify its recorded outputs/assertions reproduce against the current registry.",
                "examples": [
                    {"label": "Verify a scenario", "command": "cardano-profile scenario verify dwarf/scenarios/amaru-cbor-tx-body-fuzz.yaml"},
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
            "Search, verify, sign, and chain forensic bundles. Each bundle is "
            "tamper-evident: <code>chain.json</code> links to the previous run's "
            "hash, and <code>verify</code> recomputes the manifest hash and "
            "validates the chain end-to-end. Full subcommand set: promote, "
            "dedupe, sign, export, import, verify, search, stats, list-promoted, "
            "audit-trail, replay-and-diff, reproduce."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile bundle search [--tag <t>] [--status <s>] [--since <d>] [--until <d>]",
                "summary": "Search runs/bundles by tag, status (signed/unsigned/promoted/deduped), and date window. Use <code>bundle list-promoted</code> for just the curated set and <code>bundle stats</code> for aggregate counts.",
                "examples": [
                    {"label": "Signed bundles", "command": "cardano-profile bundle search --status signed"},
                    {"label": "Curated set", "command": "cardano-profile bundle list-promoted"},
                ],
            },
            {
                "name": "cardano-profile bundle verify <run-id>",
                "summary": "Recompute the manifest hash and validate the tamper-evident chain end-to-end for one run.",
                "examples": [
                    {"label": "Verify a run", "command": "cardano-profile bundle verify 20260427T100000Z-abc123"},
                ],
            },
            {
                "name": "cardano-profile bundle export <run-id> [--to <path>]",
                "summary": "Write the signed bundle as a tar.gz — the archive an audit package ships.",
                "examples": [
                    {"label": "Export a run", "command": "cardano-profile bundle export 20260427T100000Z-abc123 --to /tmp/run.tar.gz"},
                ],
            },
            {
                "name": "cardano-profile bundle promote <run-id> --reason-code <c> --reason-text <t>",
                "summary": "Promote a run into the curated set with governance metadata (reason code + text).",
                "examples": [
                    {"label": "Promote to curated", "command": "cardano-profile bundle promote 20260427T100000Z-abc123 --reason-code finding --reason-text \"tx-3element divergence\""},
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
            "Run a fuzz campaign against a registered fuzz id. In this delivery "
            "the ids are the retained M2 decoder manifests under "
            "<code>dwarf/targets/manifests/</code>. Browse the full target "
            "catalogue in the dashboard at <code>/operate/targets</code>."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile fuzz list",
                "summary": "List the registered fuzz ids.",
                "examples": [
                    {"label": "All fuzz ids", "command": "cardano-profile fuzz list"},
                ],
            },
            {
                "name": "cardano-profile fuzz run <fuzz-id> [--dry-run] [--approve]",
                "summary": "Run a fuzz campaign against a registered fuzz id. The id is a positional argument.",
                "examples": [
                    {"label": "CBOR tx-body", "command": "cardano-profile fuzz run amaru-cbor-decode-tx-body"},
                    {"label": "Preview only", "command": "cardano-profile fuzz run amaru-cbor-decode-tx-body --dry-run"},
                ],
            },
        ],
    },
    {
        "slug": "primitive",
        "title": "primitive",
        "summary": (
            "Scaffold new primitives — the typed building blocks scenarios "
            "reference by name. Family is one of setup, load, probe, assertion, "
            "fault, teardown; the registry under "
            "<code>dwarf/primitives/registry.json</code> is the canonical "
            "mapping. The CLI exposes scaffolding only; browse and inspect the "
            "full catalogue of 206 primitives in the dashboard at "
            "<code>/operate/primitives</code>."
        ),
        "anchor_path": "dwarf/primitives/registry.json",
        "commands": [
            {
                "name": "cardano-profile primitive new --family <family> --name <name>",
                "summary": "Scaffold a new primitive: writes the params schema under <code>dwarf/primitives/&lt;family&gt;/</code>, a <code>dwarf/scripts/runtime_&lt;name&gt;.py</code> helper, and a <code>tests/test_runtime_&lt;name&gt;.py</code> test, and appends the entry to the registry.",
                "examples": [
                    {"label": "New load primitive", "command": "cardano-profile primitive new --family load --name my_new_primitive"},
                    {"label": "New assertion", "command": "cardano-profile primitive new --family assertion --name my_invariant_holds"},
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
            "dashboard token and are serialised by a global lock. "
            "Subcommands: status, generate, serve."
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
            "case from candidate to confirmed-anomaly. Subcommands: replay, "
            "minimize, compare, ingest-run, replay-queue, compare-queue, "
            "promote, buckets, repair-state."
        ),
        "anchor_path": "dwarf/profile_manager/cli.py",
        "commands": [
            {
                "name": "cardano-profile testcase replay --target {amaru,cardano-node}",
                "summary": "Replay a recorded testcase against one implementation and record the outcome.",
                "examples": [
                    {"label": "Replay against Amaru", "command": "cardano-profile testcase replay --target amaru"},
                ],
            },
            {
                "name": "cardano-profile testcase minimize / compare / promote",
                "summary": "Minimize a crashing input, compare two implementations' outcomes, and promote a candidate to a confirmed anomaly.",
                "examples": [
                    {"label": "Promote a case", "command": "cardano-profile testcase promote <case-id>"},
                ],
            },
            {
                "name": "cardano-profile testcase buckets summary",
                "summary": "Summarize the triage buckets (by classification, reason, target).",
                "examples": [
                    {"label": "Bucket summary", "command": "cardano-profile testcase buckets summary"},
                ],
            },
            {
                "name": "cardano-profile testcase replay-queue run",
                "summary": "Drain the pending replay queue.",
                "examples": [
                    {"label": "Run the replay queue", "command": "cardano-profile testcase replay-queue run"},
                ],
            },
        ],
    },
]


def cli_groups() -> list[dict[str, Any]]:
    """Return a defensive copy of the CLI catalogue."""
    return [dict(g, commands=[dict(c) for c in g["commands"]]) for g in CLI_GROUPS]
