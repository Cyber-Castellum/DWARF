"""Hand-authored glossary catalog for /learn/concepts.

Each entry is anchored to a real project path or marked needs_source=True.
The discipline carried from the slice-4 design: do not fabricate. If a
term lacks a project-anchored definition at authoring time, set
needs_source=True and leave anchor_path/anchor_symbol empty.

Entry shape:
    {
        "slug":         kebab-case fragment used in URL hash and HTML id
        "term":         display name, capitalized first letter
        "definition":   one-paragraph plain-English text; may contain
                        in-prose <a href="#other-slug">other-term</a> markup
        "anchor_path":  repo-relative path to the canonical file, or ""
        "anchor_symbol": optional symbol name (function/class/key) within
                        the file; or "" when the file itself is the anchor
        "needs_source": True iff anchor_path is empty
    }
"""

CONCEPTS: list[dict] = [
    {
        "slug": "bucket",
        "term": "Bucket",
        "definition": (
            "A bucket groups related test cases together inside the "
            "<a href=\"#lifecycle\">lifecycle</a> view. The bucket id "
            "carries a classification (such as runtime anomaly), a "
            "triage reason, and a target implementation; cases that "
            "share these attributes land in the same bucket so triage "
            "happens at the family level rather than the individual run."
        ),
        "anchor_path": "dwarf/profile_manager/data/lifecycle.py",
        "anchor_symbol": "_summarize_testcase_state",
        "needs_source": False,
    },
    {
        "slug": "bundle",
        "term": "Bundle",
        "definition": (
            "A bundle is the forensic artifact produced by every "
            "<a href=\"#scenario\">scenario</a> execution. It packages "
            "the manifest, recorded "
            "<a href=\"#observer-event\">observer events</a>, "
            "<a href=\"#target-event\">target events</a>, captured "
            "stdout/stderr, and resource snapshots into a tar.gz that is "
            "reproducible, signable, and replayable. Bundles are the "
            "evidence the rest of the framework references."
        ),
        "anchor_path": "dwarf/profile_manager/forensic.py",
        "anchor_symbol": "export_bundle",
        "needs_source": False,
    },
    {
        "slug": "differential-testing",
        "term": "Differential testing",
        "definition": (
            "Differential testing runs the same "
            "<a href=\"#scenario\">scenario</a> against both Cardano "
            "implementations (Amaru and cardano-node), produces a "
            "<a href=\"#bundle\">bundle</a> for each, and compares the "
            "outcomes. Divergence between implementations is the signal: "
            "two parsers should agree on whether bytes are valid, two "
            "consensus paths should agree on whether a block is "
            "accepted. The compare step is followed by "
            "<a href=\"#normalized-compare\">normalized compare</a> for "
            "lifecycle bookkeeping."
        ),
        "anchor_path": "dwarf/profile_manager/scenario.py",
        "anchor_symbol": "compare_run",
        "needs_source": False,
    },
    {
        "slug": "fault-family",
        "term": "Fault family",
        "definition": (
            "A fault family is one of the canonical "
            "<a href=\"#primitive\">primitive</a> categories — alongside "
            "setup, load, probe, assertion, and teardown — that "
            "represents an injected adversarial condition. Concrete "
            "faults today include partitions, drops, delays, and "
            "per-port variants; each has a JSON schema under "
            "<code>dwarf/primitives/fault/</code> that scenarios "
            "reference by name."
        ),
        "anchor_path": "dwarf/primitives/registry.json",
        "anchor_symbol": "entry_schema.family",
        "needs_source": False,
    },
    {
        "slug": "fuzzer-backend",
        "term": "Fuzzer backend",
        "definition": (
            "A fuzzer backend is a per-implementation shim: a small "
            "wrapper around an upstream parser, decoder, or transition "
            "function in Amaru, cardano-node, or another target. Each "
            "shim reads input bytes from stdin, invokes one upstream "
            "function, and reports the outcome. Backends are catalogued "
            "by manifest under <code>dwarf/targets/manifests/</code> "
            "and driven as black boxes by primitives such as "
            "<code>cbor_fuzz_target</code>."
        ),
        "anchor_path": "dwarf/targets/README.md",
        "anchor_symbol": "",
        "needs_source": False,
    },
    {
        "slug": "helper",
        "term": "Helper",
        "definition": (
            "Helpers are shared subprocess-driver modules used by the "
            "runtime check scripts under <code>dwarf/scripts/</code>. "
            "They handle the repetitive plumbing of querying tip, "
            "fetching ranges, waiting for nodes to recover, and "
            "measuring directory size — so each per-runtime check "
            "script can stay focused on the specific behavior it "
            "asserts."
        ),
        "anchor_path": "dwarf/scripts/runtime_common.py",
        "anchor_symbol": "",
        "needs_source": False,
    },
    {
        "slug": "lifecycle",
        "term": "Lifecycle",
        "definition": (
            "The lifecycle is the post-run bookkeeping that turns "
            "individual <a href=\"#bundle\">bundles</a> into a triage "
            "queue. It clusters runs into "
            "<a href=\"#bucket\">buckets</a>, tracks pending "
            "<a href=\"#normalized-compare\">normalized compare</a> and "
            "replay work, surfaces runtime anomalies, and records "
            "minimization state. The dashboard reads its summary from "
            "<code>state/testcases/</code>."
        ),
        "anchor_path": "dwarf/profile_manager/data/lifecycle.py",
        "anchor_symbol": "_summarize_testcase_state",
        "needs_source": False,
    },
    {
        "slug": "normalized-compare",
        "term": "Normalized compare",
        "definition": (
            "Normalized compare reduces a "
            "<a href=\"#differential-testing\">differential test</a> "
            "result to a small set of stable fields — outcome, "
            "per-implementation run outcomes, behavior signatures, "
            "resource signatures — so two runs of the same "
            "<a href=\"#scenario\">scenario</a> can be compared without "
            "false-positive diffs from timestamps, paths, or other "
            "incidental noise. The lifecycle uses these normalized "
            "fields for bucketing."
        ),
        "anchor_path": "dwarf/profile_manager/testcase_lifecycle.py",
        "anchor_symbol": "_normalized_compare_outcome",
        "needs_source": False,
    },
    {
        "slug": "observer-event",
        "term": "Observer event",
        "definition": (
            "Observer events are records emitted by "
            "<a href=\"#primitive\">primitives</a> during a "
            "<a href=\"#scenario\">scenario</a> run — one ndjson line "
            "per event, capturing what the test harness saw at each "
            "step (phase transitions, assertion outcomes, probe "
            "results). Distinct from "
            "<a href=\"#target-event\">target events</a>, which come "
            "from the system under test. Both streams land in the "
            "<a href=\"#bundle\">bundle</a>."
        ),
        "anchor_path": "dwarf/profile_manager/forensic.py",
        "anchor_symbol": "observer_event_log",
        "needs_source": False,
    },
    {
        "slug": "primitive",
        "term": "Primitive",
        "definition": (
            "A primitive is a typed building block that "
            "<a href=\"#scenario\">scenarios</a> reference by name. "
            "Each primitive declares a family (setup, load, probe, "
            "assertion, fault, or teardown), a parameter schema, the "
            "implementations it can drive, and the runtimes it supports. "
            "The registry under <code>dwarf/primitives/registry.json</code> "
            "is the canonical mapping; scenarios cannot add primitives, "
            "only reference registered ones."
        ),
        "anchor_path": "dwarf/primitives/registry.json",
        "anchor_symbol": "entry_schema",
        "needs_source": False,
    },
    {
        "slug": "producer",
        "term": "Producer",
        "definition": (
            "Producer is a label on a candidate test case that records "
            "where the case came from — a <a href=\"#scenario\">scenario</a> "
            "execution by default, or a fuzzer name (e.g. "
            "<code>aflpp</code>, <code>cargo-fuzz</code>) when a backend "
            "discovered the case. The label flows through the "
            "<a href=\"#bundle\">bundle</a> and the "
            "<a href=\"#lifecycle\">lifecycle</a> so triage can group by "
            "origin."
        ),
        "anchor_path": "dwarf/profile_manager/scenario.py",
        "anchor_symbol": "testcase_candidate.producer",
        "needs_source": False,
    },
    {
        "slug": "profile",
        "term": "Profile",
        "definition": (
            "A profile is a named devnet topology — node count, peer "
            "sharing setting, runtime root, implementation mix — that "
            "the dashboard can deploy, inspect, and tear down. The five "
            "current profiles range from a Haskell baseline through "
            "mixed Haskell+Amaru to single-implementation preview-proof "
            "topologies. Profiles are the unit of live-runtime test "
            "deployment."
        ),
        "anchor_path": "dwarf/profile_manager/profiles.py",
        "anchor_symbol": "load_profiles",
        "needs_source": False,
    },
    {
        "slug": "promotion",
        "term": "Promotion",
        "definition": (
            "Promotion is the transition of a draft scenario from "
            "<code>dwarf/scenarios/pending/</code> into the live "
            "<code>dwarf/scenarios/</code> corpus. A pending scenario "
            "must clear its declared promotion blockers — explicit "
            "preconditions the author set when pasting the draft — "
            "before it can be promoted. The dashboard's compare and run "
            "surfaces only see promoted <a href=\"#scenario\">scenarios</a>."
        ),
        "anchor_path": "dwarf/profile_manager/scenario.py",
        "anchor_symbol": "handle_promote",
        "needs_source": False,
    },
    {
        "slug": "scenario",
        "term": "Scenario",
        "definition": (
            "A scenario is a YAML file under <code>dwarf/scenarios/</code> "
            "that defines a sequence of <a href=\"#primitive\">primitives</a> "
            "against a target, plus the "
            "<a href=\"#observer-event\">observer events</a> and "
            "assertions the runner expects to record. The runner produces "
            "one <a href=\"#bundle\">bundle</a> per scenario execution. "
            "Scenarios are the atomic unit of test work."
        ),
        "anchor_path": "dwarf/profile_manager/scenario.py",
        "anchor_symbol": "load_scenario",
        "needs_source": False,
    },
    {
        "slug": "seam",
        "term": "Seam",
        "definition": (
            "Seam is a project-vocabulary term for the architectural "
            "interfaces between framework layers — the boundaries where "
            "adapters can be swapped, such as between a "
            "<a href=\"#fuzzer-backend\">fuzzer backend</a> and the "
            "canonical run format, or between a runtime helper and the "
            "live target-host session. The current set of seams is "
            "discussed in project planning notes; a code-level "
            "enumeration is open."
        ),
        "anchor_path": "",
        "anchor_symbol": "",
        "needs_source": True,
    },
    {
        "slug": "target-event",
        "term": "Target event",
        "definition": (
            "Target events are records emitted by the system under test "
            "during a <a href=\"#scenario\">scenario</a> run — one "
            "ndjson line per event, captured via instrumentation hooks "
            "(target-hooks.ndjson) and direct emissions (target.ndjson). "
            "Distinct from <a href=\"#observer-event\">observer events</a>, "
            "which come from the test harness. Both streams land in the "
            "<a href=\"#bundle\">bundle</a>."
        ),
        "anchor_path": "dwarf/profile_manager/forensic.py",
        "anchor_symbol": "target_event_log",
        "needs_source": False,
    },
]
