# Mixed Cardano/Amaru Harness Full Repair Design

## Goal

Repair `antithesis/cardano_amaru_adversarial` so a run proves that Amaru advances on a chain shared with Cardano, receives mutated blocks from DWARF, and reports safety, liveness, and infrastructure failures without vacuous or misleading properties.

## Evidence driving the repair

Run `bb2ba66e1d863efbad2e0666e27d9276-59-13` successfully exercised the fee differential, but did not exercise Amaru's block decoder. Both Amaru relays remained at the baked slot-1189 tip. Their ChainSync sessions with the live Cardano relays and DWARF ended at `intersect not found`. DWARF ingested a different live Cardano lineage and never emitted `dwarf_served_mutated_block`.

The same run exposed three reporting defects:

- The oracle image did not install a discoverable assertion catalog.
- A conditional `always(False)` command-error assertion appeared as a failing 0/0 property when no command error occurred.
- `tracer-sidecar` was restarted and failed reopening a locked SDK output file, while services carrying the repository's fault-exclusion label were still selected for faults.

## Selected architecture

Use the existing digest-pinned `cardano-phase1-reference` state as the canonical source for the mixed block path. It was produced with the baked Amaru state and already serves the fee workload. DWARF's upstream, the Amaru target's honest peer, and the Amaru control peer will all use `cardano-phase1-reference.example:3901`.

The live `p1/p2/p3/relay1/relay2` Cardano cluster remains in the bundle for its existing Cardano consensus properties, but it is no longer part of the Amaru/DWARF block path. This separates two independent experiments and prevents the freshly generated cluster lineage from being mistaken for the baked mixed lineage.

This architecture has a hard feasibility gate. Before the Compose wiring is accepted, local evidence must prove:

1. The reference node recognizes the Amaru slot-1189 point.
2. The reference state contains blocks after the baked Amaru tip.
3. The control Amaru relay adopts a tip after slot 1189.
4. DWARF emits `dwarf_served_mutated_block`.
5. The target Amaru relay emits a decoder rejection or another explicit mutated-block outcome.

If either of the first two conditions is false, implementation stops. The fallback is to bake a Cardano producer/reference state and an earlier Amaru state from the same test chain. Runtime Amaru bootstrap is not the fallback because it adds multi-epoch startup and snapshot-generation failure modes to every Antithesis environment.

## Property model

The oracle will own Amaru-log-derived assertions. DWARF continues to own its existing server, decoder reachability, and served-mutation assertions.

Required coverage properties are:

- The control Amaru relay advances beyond slot 1189.
- The target Amaru relay advances beyond slot 1189.
- DWARF serves a mutated block.
- The target Amaru relay observes a mutated-block decoder outcome.

Safety properties are evaluated only after the coverage prerequisites are observable:

- At an equal block height, the target and control tip hashes agree.
- The target does not advance materially beyond the control.
- Neither Amaru relay emits a fatal or panic signature.

The oracle calls assertion sites from startup onward so every intended property is cataloged. A missing event therefore becomes a visible coverage failure instead of disappearing from the report.

The phase-1 command-error property becomes an `unreachable` assertion with the identity `mixed phase-1 command error`. Each successful command emits a separate reachable success property. Exceptions remain bounded and visible without creating a false 0/0 `Always` failure.

## Image and catalog contract

The oracle image follows the workload image's reproducibility contract:

- digest-pinned Python base;
- `antithesis==0.2.0`;
- no permissive install fallback;
- assertion source copied into `/opt/antithesis/catalog`;
- runtime source copied separately to `/oracle.py`;
- an image test verifies both paths and imports the SDK.

New image digests are written literally into Compose only after local tests and anonymous registry pulls pass. No credentials, `.env`, signing keys, or `._*` files enter the public commit.

## Fault and observability contract

The current `com.antithesis.exclude_from_faults` label is not considered effective merely because it renders in Compose. The implementation will inspect the MOOG launch path and actual Antithesis fault events to establish the tenant-supported exclusion mechanism. No `snouty` commands are used.

The intended faultable set is the Cardano and Amaru SUT nodes. The oracle, workload, submit API, tracer pipeline, configurator, sidecars, and DWARF control process are infrastructure unless a specific experiment deliberately includes them.

If the `amaru-cardano` tenant cannot exclude those services, infrastructure containers must be restart-safe before a paid run. In particular, the tracer must either use a version that can reopen its SDK sink or be replaced/wrapped with a verified restart-safe implementation. A Compose label-only test is insufficient; the gate is the observed fault target list and clean restart behavior.

## Verification and launch gate

Verification is layered:

1. Python unit tests for oracle parsing/state and phase-1 assertion semantics.
2. Bundle contract tests for canonical peer wiring, catalog paths, pinned dependencies, literal images, and public-safety rules.
3. Compose rendering and isolated local startup.
4. A local mixed-path smoke proving post-1189 adoption, mutation served, and decoder outcome.
5. Forced local restarts of oracle, workload, tracer, and submit API with no locked-file or assertion-catalog errors.
6. Image inspection and anonymous registry pulls.
7. MOOG launch-payload/fault-scope inspection using the release CLI and existing run APIs.
8. Public-commit cleanliness check.

A paid MOOG request remains blocked until the local gates pass and the supported fault-scope mechanism is identified. Launching requires separate explicit approval.
