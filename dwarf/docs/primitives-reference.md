# Primitives Reference

This document will be auto-regenerated from `dwarf/primitives/registry.json` later. For now it lists what's registered.

## Registered (23 primitives)

### Load (`family: load`)

| Name | Runtimes | Supports | Purpose |
|---|---|---|---|
| `cbor_edge_cases` | library | cardano-node, amaru | Feed named CBOR edge-case byte strings to a shim and classify each case. |
| `cbor_fuzz_target` | library | cardano-node, amaru | Generate random bytes; feed to a target shim binary; classify each iteration as `ok` / `clean_error` / `crash` per the shim outcome contract. |
| `cbor_fuzz_structured` | library | cardano-node, amaru | Generate shape-constrained CBOR, optionally mutate it, feed to a target shim, and classify each iteration. |
| `disk_fill` | devnet | cardano-node, amaru | `docker exec <container> dd if=/dev/zero ...` to fill disk toward a cgroup/volume limit. Resource-abuse load. |
| `load_shell_command` | library, single-node, devnet | cardano-node, amaru | Run a host shell command and record its completion outcome into the forensic bundle. |
| `mini_protocol_state_machine` | library | cardano-node, amaru | Replay a declarative mini-protocol transition corpus, emit a state trace, and classify each transition outcome. |
| `mini_protocol_sequence_target` | library | cardano-node, amaru | Replay named mini-protocol message sequences from a JSON corpus through an existing shim target; emits per-message and per-iteration outcome events. |
| `sync_replay` | devnet | cardano-node, amaru | Stop target container, wipe its DB dir, restart — forces chain re-sync from peers. Resource-abuse load. |

### Setup (`family: setup`)

| Name | Runtimes | Supports | Purpose |
|---|---|---|---|
| `deploy_profile` | devnet | cardano-node | Wraps `profiles.deploy_command`; deploys a devnet profile via SSH + docker-compose. Raises on non-zero exit. |
| `wait_for_tip` | devnet | cardano-node | Polls `inspect_health_command` until the tip JSON appears; raises TimeoutError on deadline. |
| `start_node_process` | single-node | cardano-node, amaru | Launches a node binary as a subprocess; records pid in shared state for later primitives to read. |

### Probe (`family: probe`)

| Name | Runtimes | Supports | Purpose |
|---|---|---|---|
| `parser_exit_status` | library | cardano-node, amaru | Per-input probe; records each iteration's outcome to `probes/parser_exit_status.ndjson`. |
| `process_rss` | single-node, devnet | cardano-node, amaru | Samples resident-set-size for a node process tracked in shared state via `ps -o rss= -p <pid>`. |

### Assertion (`family: assertion`)

| Name | Runtimes | Supports | Purpose |
|---|---|---|---|
| `parse_succeeds_or_clean_error` | library | cardano-node, amaru | Pass iff every iteration's outcome is `ok` or `clean_error`. Any `crash` fails. |
| `load_events_are_ok` | library, single-node, devnet | cardano-node, amaru | Pass iff all load-phase completion events report `outcome=ok`. Useful for runtime shell/inventory checks. |
| `roundtrip_equals_original` | library | cardano-node, amaru | For every parsed (`ok`) outcome, the shim's re-encoded bytes must equal the input bytes. Vacuous pass if no parsed outcomes. |
| `state_machine_trace_valid` | library | cardano-node, amaru | Pass iff every logged `mini_protocol_state_machine` transition matched the declared state graph and expected outcome. |

### Fault (`family: fault`)

Faults apply before load and remove after, in LIFO order. The runner guarantees best-effort remove even if load errors.

| Name | Runtimes | Supports | Purpose |
|---|---|---|---|
| `fault_delay` | devnet | cardano-node, amaru | Spawns a pumba sidecar in target container's netns + pidns, runs `tc netem delay` scoped to that container. Duration-bounded. |
| `fault_drop` | devnet | cardano-node, amaru | Same pumba sidecar pattern, `tc netem loss --percent N --correlation N`. |
| `fault_local_port_drop` | devnet | cardano-node, amaru | Uses host `iptables` rules to drop loopback traffic to one target port for the duration of the load. Intended for host-based devnet layouts where Docker netem faults do not apply. |
| `fault_local_port_delay` | devnet | cardano-node, amaru | Uses host `tc netem` on loopback, scoped by port filters, to inject bounded latency against one local listener. Intended for host-based devnet layouts. |
| `fault_partition` | devnet | cardano-node, amaru | `docker network disconnect <network> <container>` on apply, `docker network connect` on remove. No pumba needed. |

### Teardown (`family: teardown`)

| Name | Runtimes | Supports | Purpose |
|---|---|---|---|
| `stop_node_process` | single-node | cardano-node, amaru | Terminates a previously-started node process and reaps it. |

## Runtime requirements

- **Library-tier primitives** — need only the shim binaries referenced from their target manifests. No docker, no SSH, no node process.
- **Single-node primitives** — need the target node binary available on the host where Dwarf's runner executes.
- **Devnet primitives** — need (a) docker on the Linux target host, (b) the `dwarf/cardano-node` image built, (c) for `fault_delay`/`fault_drop` specifically: `gaiaadm/pumba:latest` image pulled on the Linux target host. All three are checked by `cardano-profile prereq-check`.
- **Host fault note** — `fault_local_port_drop` requires passwordless `sudo` and host `iptables` on the runtime host. It is the correct fault path for the current host-loopback Profile A layout.
- **Host delay note** — `fault_local_port_delay` requires passwordless `sudo`, host `tc`, and a loopback root qdisc state that is still `noqueue` before the fault is applied.

## The runner lifecycle

Given a scenario with setup + faults + load + probes + assertions + teardown, the runner executes:

1. `setup` primitives in order. Any failure aborts before load.
2. `fault` primitives' `apply()` in order. Any failure aborts and triggers LIFO removal of already-applied faults + teardown.
3. `load` primitives in order.
4. `fault` primitives' `remove()` in LIFO order. Best-effort; failures logged but don't fail the run.
5. Probes with `sample_for_input` are called once per iteration outcome from the load event log.
6. `assertion` primitives evaluate against collected outcomes; overall `pass`/`fail` folds across all assertions.
7. `teardown` primitives in order. Always runs; failures logged.

## Adding a new primitive

This is a code change, not a scenario change.

1. Create a Python module under `dwarf/primitives/<family>/<name>.py` (or add to `dwarf/profile_manager/primitives.py` for framework-owned primitives).
2. Implement the appropriate base class interface (`LoadPrimitive.run`, `ProbePrimitive.sample`/`sample_for_input`, `AssertionPrimitive.evaluate_outcomes`, `FaultPrimitive.apply`/`remove`).
3. Add a parameter JSON Schema at `dwarf/primitives/<family>/<name>.schema.json` and keep it in lockstep with the registry entry.
4. Register the primitive in `dwarf/primitives/registry.json` with name, module, class, version, family, supports, runtimes, params_schema.
5. Bump the primitive's `version` if behavior changed; bundles will record which version produced their result.
6. Update this document.

The set of registered primitives is the framework's safety boundary: pasted scenarios cannot add primitives, only reference them.
