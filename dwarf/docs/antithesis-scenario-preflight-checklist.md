# DWARF Antithesis scenario preflight checklist

**Purpose:** decide whether a new Cardano/Amaru scenario is genuinely ready for
Antithesis, without confusing generator output, setup validation, or an accepted
request with an executed test.

**Applies to:** generated DWARF bundles, hand-built mixed Cardano/Amaru bundles,
and Moog submissions to the `amaru-cardano` tenant.

## The evidence ladder

Every scenario must pass these in order. A later level cannot compensate for a
missing earlier level.

| Level | What it proves | What it does not prove |
|---|---|---|
| Generator/static gate | Scenario semantics, supported fuzz primitive, topology files, assertions, driver shape | Images pull, bootstrap, setup-complete, or workload execution |
| Compose/config gate | YAML resolves and all referenced assets are present | Nodes are ready or the intended path is reachable |
| `snouty validate` | Linux Compose can start, emit `setup_complete`, and expose valid test-template commands | The driver ran, both implementations were contacted, or a property had encounters |
| Fresh local readiness smoke | Exact images/config start; bootstrap, stores, relays, consumer, endpoints, and direct driver work | Antithesis fault exploration or broad campaign coverage |
| Moog/Antithesis build evidence | Public commit/images were accepted and the requested run reached setup | The mixed workload ran or produced meaningful observations |
| First-timeline evidence | Expected services, driver-invoked, endpoint-reachable, and classification/reachability events occurred | A final property result until the run terminates |
| Terminal triage report | Completed campaign and property outcomes | That a green property with zero encounters tested anything |

## A. Scenario identity and scope

- [ ] State the exact question in one sentence. It must name the implementation
  pair, input/fault surface, and observable property.
- [ ] Search prior DWARF reports, findings, local campaigns, and the relevant
  Moog workbench notes. Record what is already proven and what is intentionally
  not repeated.
- [ ] Identify the exact applicable workbench/test launcher and record its ID,
  version, notes, topology contract, and relevant prior runs. Never infer this
  from a similarly named workbench.
- [ ] Classify the scenario as one of: native generator/adversary, upstream
  Amaru-control substrate plus additive workload, baked-state differential, or
  another explicitly documented topology. Do not silently combine contracts.
- [ ] State whether the scenario is a readiness smoke, a semantic differential,
  a protocol/adversary campaign, or a fault/recovery campaign. A readiness smoke
  is not a finding.

## B. Authoritative source and topology review

- [ ] Review the current official Amaru wiki/docs and `pragma-org/amaru` source
  revision for bootstrap, store layout, network addressing, supported startup,
  and snapshot/epoch assumptions.
- [ ] Review the Cardano node / Cardano Foundation Antithesis source revision for
  ChainDB readiness, configurator behavior, tracer output, sidecar setup signal,
  and consumer convergence semantics.
- [ ] For mixed Amaru scenarios, preserve the proven
  `antithesis/upstream_amaru_control` contract unless a new local proof replaces
  it: producer, bootstrap bundle, private relay stores, startup markers,
  Amaru-only consumer path, and sidecar setup-complete gate.
- [ ] Keep the topology minimal. Every extra service must have a stated role in
  the question; every test service must be fault-excluded only when justified.
- [ ] Make readiness explicit. `depends_on: service_started`, an open TCP port,
  and a running container are not node/ChainDB/bootstrap readiness.

## C. Generator and artifact checks

- [ ] Run DWARF semantic validation and the generator's Stage-2 verifier.
- [ ] Confirm the selected fuzz primitive maps to a built adversary mode and the
  generated topology actually routes that adversary to the target.
- [ ] Confirm the generated bundle contains `docker-compose.yaml` at the exact
  expected path, no `build:` contexts, no floating/private image references,
  the required fault label, and at least one non-empty driver/assertion.
- [ ] Confirm command names use supported prefixes: `parallel_driver_`,
  `serial_driver_`, `singleton_driver_`, `anytime_`, `eventually_`, or
  `finally_`. An `eventually` command cannot substitute for a driver.
- [ ] Inspect the immutable workload image through a stopped container, before
  its entrypoint runs. Require exactly the intended directories and commands
  under `/opt/antithesis/test/v1`, executable modes for the image's default
  user, and no inherited templates. A startup `cp`, `chmod`, deletion, or bind
  mount is not pre-entrypoint catalog proof.
- [ ] Confirm the driver does not emit `setup_complete`; setup belongs to the
  system readiness path.
- [ ] Confirm the package is self-contained and has no `.env`, PAT, wallet,
  signing key, PEM, `._*`, `.DS_Store`, or generated bytecode files.
- [ ] Confirm every `image:` reference is anonymously pullable and pinned to a
  verified digest (or an explicitly approved immutable source revision).

## D. Workload and property checks

- [ ] The driver performs a real operation against the SUT; it is not merely a
  heartbeat, fixture load, or assertion emitter.
- [ ] Emit a dedicated reachability event at driver entry, before network calls.
- [ ] Emit reachability evidence that each intended implementation/endpoint was
  contacted and returned an observation.
- [ ] Keep transport failure, timeout, preparation failure, decode rejection,
  semantic rejection, and acceptance as separate classifications.
- [ ] Use `always` for invariants that must never fail, `sometimes` for meaningful
  liveness/coverage encounters, and `reachable` for proving the intended path
  occurred. Do not use a passing `always` property with zero samples as proof.
- [ ] Add a Brown-M&M/control condition: the run must encounter a known-good
  path, such as setup-complete, driver invocation, both endpoints reachable, and
  both results classifiable.
- [ ] For a differential test, submit the same immutable input bytes and compare
  semantically equivalent outcomes. Prove state provenance: a fresh configurator
  ledger must not be paired with an older baked Amaru store.
- [ ] For an adversarial test, prove the adversary is connected and exercising the
  target (not merely present in Compose). For a non-adversarial differential
  test, mark adversary-connection as not applicable rather than inventing it.

## E. Local execution gates

- [ ] Run `INTERNAL_NETWORK=false docker compose ... config --quiet` on the exact
  final package.
- [ ] Run the package contract/unit tests.
- [ ] Run `tools/check_images.sh` and preserve its report.
- [ ] Run a fresh local readiness smoke using the exact final images/config and a
  unique Compose project. Require, as applicable:
  - [ ] producer/bootstrap completion and non-empty committed state;
  - [ ] private Amaru store copies and startup markers;
  - [ ] no unexpected relay restarts during the gate;
  - [ ] Cardano readiness from `cardano-cli query tip` or tracer evidence, not
    node stdout's configuration dump;
  - [ ] Amaru consumer advancement/convergence when the topology includes it;
  - [ ] both intended endpoints reachable;
  - [ ] direct driver invocation and JSON/SDK evidence;
  - [ ] expected classification/agreement properties.
- [ ] Repeat the smoke from a fresh project/state at least once when state leakage
  or bootstrap timing is plausible. Always tear down with `down -v`.
- [ ] Keep readiness and campaign runs separate. Never spend a paid campaign run
  to discover that bootstrap, image visibility, or command discovery is broken.

## F. Snouty validation

- [ ] Run the official Linux-compatible snouty version on the same architecture as
  the Docker host. Verify `snouty --version`.
- [ ] Ensure the executable name expected by the installed snouty version exists
  (`docker-compose` may need a safe shim delegating to `docker compose`).
- [ ] Use a timeout long enough for the topology's real bootstrap/epoch gate;
  180 seconds is not sufficient for the upstream Amaru third-epoch gate.
- [ ] Require `Setup-complete event detected` and the expected driver/eventual
  command count.
- [ ] Record explicitly that snouty validation did **not** execute the driver.

## G. Public/Moog submission gate

- [ ] Commit the exact bundle and record the public commit SHA before submission.
- [ ] Inspect the **public Git tree modes**, not only local filesystem modes or
  raw file bytes. If commands enter the image from browser/Windows-published
  `100644` files, the Dockerfile must set their executable mode in the immutable
  image layer (for example, `COPY --chmod=0755`). Runtime staging before
  setup-complete is too late for the live Composer catalog.
- [ ] Verify all custom images are public, anonymously pullable, and match the
  digest in the Compose file. Do not rely on an authenticated local Docker cache.
- [ ] Validate `com.antithesis.exclude_from_faults` as a comma-separated subset
  of `network,kill,pause,stop`; boolean `true` is invalid and can leave a request
  pending forever before reaching Antithesis.
- [ ] Use release `moog`, not `moog-head`, and verify the exact tenant, repository,
  directory, workbench/test launcher, duration, and fault setting.
- [ ] Perform a dry-run/request-payload check and inspect the generated Compose
  asset before spending a run.
- [ ] Keep secrets out of the commit, tarball, workbench notes, shell output, and
  reports. PATs, wallets, registry credentials, and Antithesis credentials are
  operational inputs, never bundle content.
- [ ] Submit only after every earlier box is checked and explicit paid-run
  approval exists.

## H. Post-launch validity checks

Immediately after launch, verify the run is doing the intended work:

- [ ] Build logs show the expected commit, image digests, and no unauthorized or
  private image pulls.
- [ ] The run reaches setup-complete after the real readiness conditions, not just
  container startup.
- [ ] First-timeline logs contain the driver-invoked event.
- [ ] The expected mixed services are present and the intended endpoint/adversary
  connection is observable.
- [ ] Both implementations produce classifiable observations and the relevant
  `reachable`/`sometimes` properties have encounters.
- [ ] If any of those are absent, classify the run as invalid/incomplete—not as a
  clean test result—and stop spending time on its property summary.
- [ ] Wait for terminal completion before making campaign claims. `accepted`,
  `in_progress`, or a green always-property with zero encounters is not proof.
- [ ] Save run ID, workbench ID/version, commit, image digests, build logs,
  first-timeline evidence, and terminal triage report together.

## Common pitfalls recorded from this project

1. **Minimal generated Amaru scaffold:** bare Amaru with no bootstrap state is
   not a runnable mixed topology. Start from the upstream control substrate.
2. **Fresh-state mismatch:** a transaction signed against a fresh configurator
   UTxO cannot be validated by an older baked Amaru store; this creates a
   preparation failure, not a phase-1 disagreement.
3. **Adversary-only peer topology:** the target stalls at its seed and safety
   properties become vacuous. Use an honest peer plus adversary when that is the
   intended attack model.
4. **Stale trace parser:** renamed Amaru adoption traces can make every oracle
   property evaluate over zero samples while appearing green.
5. **Boolean fault exclusion:** `exclude_from_faults: true` is invalid Moog
   syntax. Use an explicit comma-separated fault-class list or remove the label.
6. **Mutable/private images:** tags, local-only images, and private GHCR packages
   cause build/push failures or silent mismatch between local and live bytes.
7. **Wrong snouty binary/host prerequisite:** macOS ARM binaries do not run on
   Linux x86_64; snouty 0.6.1 also checks for a `docker-compose` executable.
8. **Short readiness timeout:** the upstream bootstrap producer waits for a deep
   epoch boundary; a short snouty timeout reports a setup timeout, not a scenario
   result.
9. **False node-log evidence:** cardano-node stdout mostly contains its config
   dump; use `cardano-cli query tip` and tracer output for runtime evidence.
10. **Filename/path drift:** Antithesis/Moog discovers the expected
    `docker-compose.yaml` and `/opt/antithesis/test/v1` layout; parking the real
    compose under a different name can validate or submit the wrong asset.
11. **One-shot acceptance mistaken for testing:** an accepted Moog request means
    the control plane accepted it, not that Antithesis launched, fuzzed, or
    completed the scenario.
12. **Adding an eventual check without a driver:** eventual/final checks cannot
    prove the workload reached the SUT; keep a direct driver-invoked assertion.
13. **Cardano/Amaru port-name assumption:** current `amaru-relay-bootstrap`
    connects upstream to Cardano on `3001` but serves downstream node-to-node
    clients on `3000`. Read the selected binary's `listen_address` log and probe
    that exact port; copying `3001` into the Amaru-consumer topology creates a
    clean-looking, permanently stalled consumer.
14. **Per-pool dynamic genesis clock race:** the pinned configurator evaluates
    `date` once per pool. If generation crosses a one-second boundary, producers
    can receive different Shelley/Byron starts and form separate chains. Compare
    every pool's `systemStart` and `startTime` before launch, normalize them when
    necessary, and require producer tip/hash convergence.
15. **Single load versus assertion default:** `load_events_are_ok` defaults
    `min_event_count` to `2`. A scenario with one intentional long-running load
    must set both `min_completed: 1` and `min_event_count: 1`; do not add a fake
    second load merely to satisfy the default.
16. **Prefixed adversary output:** container logging or `tee` may prefix an SP4
    record with `dwarf-adversary:`. Parse the structured SP4 payload after the
    prefix and test this exact deployed form; otherwise a live fuzzer can appear
    to have zero cases.
17. **Paired-fuzzer selector drift:** `depends_on: service_started` lets one SP4
    process consume failed-handshake selectors while its peer is still booting.
    For equivalent differential delivery, gate each fuzzer on target-specific
    semantic health, use one worker and the same explicit seed, and fail closed
    before `setup_complete` unless the ordered common transcript prefix is
    identical and covers the complete required matrix.
18. **Inherited/pre-entrypoint test-template leakage:** reusing a workload image
    can silently retain that image's `/opt/antithesis/test/v1` commands. Live run
    `239fd7d2ad494119b76bea20f1a38460-60-7` proved Antithesis cataloged the
    inherited KES suite before the replacement entrypoint ran, even though local
    Compose and Snouty later saw the staged mini-protocol tree. Use a dedicated
    immutable workload image and inspect a stopped container's exact catalog;
    do not rely on runtime mounts, deletion, copies, or chmod.
19. **Stale Snouty validation volumes:** Snouty can remove validation containers
    while leaving named Compose volumes. A later run may consume old transcripts
    or a stale readiness marker and report a false setup success. Before every
    repeat, run scoped `docker compose down -v --remove-orphans`, verify the
    scenario's volume prefix is absent, and then validate from fresh state.
20. **Public Git executable-bit loss:** browser uploads and Windows extraction can
    publish Composer commands as `100644` even when the source archive and local
    validation copy are `0755`. Antithesis may discover the filenames and then
    emit `Permission denied` every time it invokes them. Query the submitted
    commit's Git tree modes. Either publish commands as `100755` or bake them
    into a dedicated workload image with build-layer mode `0755`; do not repair
    executable bits in the entrypoint.

## Decision rule

Mark a scenario **READY FOR PAID ANTITHESIS** only when sections A–G are complete,
the exact final package passes the local smoke, snouty validation succeeds, the
workbench/Moog payload is verified, and all unresolved assumptions are recorded.
Otherwise mark it **BLOCKED** with the first failed gate and do not interpret a
green-looking partial run as evidence.

## Known-good upstream mixed-net diff

The comparison baseline is not theoretical. The Moog workbench records successful
upstream `cardano_amaru` runs using the following mechanics:

| Known-good upstream behavior | Required invariant in a DWARF-derived mixed scenario |
|---|---|
| `bootstrap-producer` waits for deep epoch history, writes `/srv/amaru/testnet_42`, and exits 0 | Do not start/score Amaru before a non-empty bootstrap bundle exists |
| Two Amaru relays consume private bundle copies and remain running with restart count 0 | Never share writable Amaru stores; inspect restart count, not just `docker ps` |
| `amaru-consumer` starts from a seeded tip and advances through only Amaru relays | Query consumer and producer tips; require advancement and matching hash at convergence |
| Sidecar delays `setup_complete` until Amaru startup markers exist | Preserve the upstream sidecar/setup path; workload must not emit setup-complete |
| Consensus differential runs use the upstream mesh and compare Cardano references with an Amaru-fed consumer | Additive workloads must not replace the mesh with a fresh/minimal Amaru scaffold |
| Successful runs report real tip progress and repeated differential iterations | A static assertion or setup-complete event alone is insufficient |
| Prior direct-Amaru runs fixed container-network peer addresses and raised Amaru logs to expose real tips | Use container DNS addresses inside Compose; read tracer/CLI state rather than stdout config dumps |

The exact source records are the Moog objects **“DWARF Consensus T0 —
cardano_amaru bring-up + Amaru bootstrap root-cause + prioritization,” “Consensus
Report — Chainhold (differential under fork/reorg): 80/80,” “Consensus Report —
Epoch-boundary differential: 94/94,”** and **“Consensus Report — Stage-1 direct
native-Amaru differential.”** The local mirror of the substrate is
`antithesis/upstream_amaru_control/`.

For `cardano_amaru_phase1`, the current smoke proves producer completion, relay
markers, endpoint reachability, and direct driver execution. It does **not yet
assert the consumer advancement/convergence row above**. Until that check is
implemented and passes, phase-1 is locally functional but not fully proven against
the complete upstream mixed-net contract.

## Source register

- `AGENTS.md` — mandatory project launch gates and workbench/Moog rules.
- `dwarf/scripts/antithesis_generator.py` — generator mapping, bundle rendering,
  and Stage-2 checks.
- `dwarf/docs/antithesis-amaru-audit.md` — minimal-scaffold failure and upstream
  bootstrap contract.
- `antithesis/upstream_amaru_control/README.md` — bootstrap, startup markers,
  consumer path, and setup-complete semantics.
- `antithesis/cardano_amaru_adversarial/RUN-DESIGN.md` and `SUBMIT-RUNBOOK.md` —
  prior adversarial topology, oracle-vacuity, image, seed, and submission lessons.
- `antithesis/cardano_amaru_phase1/README.dwarf.md`, `tools/smoke_phase1.sh`, and
  `reports/local-smoke-summary.md` — current mixed phase-1 bundle and evidence.
- Moog workbench `moog`: pinned Antithesis run checklist, requester runbook,
  fault-exclusion incident, prior run ledger, and mixed phase-1 differential note.
- Antithesis docs: [Docker Compose setup](https://antithesis.com/docs/setup/docker_compose/),
  [Writing tests](https://antithesis.com/docs/product/writing_tests/),
  [Launching tests](https://antithesis.com/docs/product/launching_tests/),
  [Optimizing for testing](https://antithesis.com/docs/best_practices/optimizing/),
  [Sometimes assertions](https://antithesis.com/docs/best_practices/sometimes_assertions/),
  [Blockchain property catalog](https://antithesis.com/docs/resources/blockchain_property_catalog/),
  and [Testing in the dev loop](https://antithesis.com/docs/workflows/dev-loop-workflow/).
