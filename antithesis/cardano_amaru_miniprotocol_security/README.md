# Mixed Cardano/Amaru mini-protocol state-machine security

This additive package preserves the proven Cardano/Amaru relay-bootstrap
control and adds two isolated, honestly-fed victims. The unchanged DWARF SP4
0.25.0 engine dials each victim and generates well-formed N2N frames in illegal
ChainSync, BlockFetch, TxSubmission2, and KeepAlive protocol states.

Both SP4 processes use the same pinned binary, explicit seed, one worker, and
connection budget. Their ordered injection transcripts are retained separately.
Before the sidecar may emit `setup_complete`, the workload requires an identical
common transcript that covers every combination of four protocols and six
departure classes: WrongAgency, OutOfState, PrematureTerminal, PostTerminal,
Flood, and Duplicate. A failed handshake or divergent stream blocks setup.

After setup, bounded Antithesis commands verify continued non-vacuous injection,
connection-local containment, honest chain progress, unrelated control-peer
usability, absence of fatal target evidence, and recovery/convergence of both
victims and the Amaru-fed isolated consumer.

The production workload uses the purpose-specific, digest-pinned
`ghcr.io/j-gainsec/dwarf-sm` image. Its immutable layers contain the observer,
workload entrypoint, and exactly three mode-`0755` commands under the single
`mixed-miniprotocol-security` template. It does not inherit the KES workload,
mount the discovery root, or alter the catalog at container startup. This makes
Antithesis discovery independent of both entrypoint timing and Git executable
bits, including browser/Windows uploads that store scripts as mode `100644`.

The Amaru supervised-listener `EADDRINUSE` failure is classified as a known
background signal. Reproducing it is not a new finding.

## Local proof

Build the workload runtime on `cardano-box`, then run the scenario through DWARF:

```bash
docker build -t dwarf-miniprotocol-workload:local \
  -f antithesis/cardano_amaru_miniprotocol_security/workload/Dockerfile \
  antithesis/cardano_amaru_miniprotocol_security

/home/nigel/dwarf-v4/dwarf/cardano-profile scenario run \
  /home/nigel/dwarf-v4/dwarf/scenarios/cardano-amaru-miniprotocol-security-local.yaml
```

Static validation, Compose rendering, and container startup are not runtime
proof. A successful proof must come from the exact DWARF scenario with fresh
volumes and retain the paired transcript, sample counts, tips, fatal scan,
container states, test-command results, revisions, and image digests.

The earlier publication-mode repair was proved through DWARF on 2026-09-08 in run
`20260908T075347Z-0bdfaaec` (`exit_status: pass`, 895.965 seconds). From fresh
volumes, the paired pre-fault transcript matched for 94 cases and covered all
24 protocol/class cells. The final observation recorded 2,603 Cardano and 911
Amaru post-setup injections, no fatal target signal, continued control-peer
progress, and recovery/convergence of both victims plus the Amaru-fed consumer
at slot 1601. All three staged Antithesis commands returned zero and passed
their semantic checks. The exact repaired Compose SHA-256 was
`bf7a5730f56c88069eefe294cf347a87f1ba393ab475f85949db6cf6256e7c5e`.

Live retry `239fd7d2ad494119b76bea20f1a38460-60-7` then proved that
entrypoint staging was too late: Composer found only the base image's inherited
`mixed-kes-security` template and zero intended mini-protocol commands. That run
is an invalid harness run, not a security result. The dedicated image repair is
mechanically gated by inspecting a stopped container before its entrypoint and
requiring one template, the exact three executable command files, and no KES
path. Its pinned manifest is
`sha256:9fcea8709a6426c84fca6008435ae26a292734484b2006db6f99a331110bfb5a`.

The dedicated-image scenario then passed exact fresh-volume DWARF run
`20260908T100054Z-80498541` in 895 seconds. The 94-case pre-fault prefix matched
and covered all 24 cells; Cardano received 2,600 and Amaru 910 post-setup cases;
neither target emitted a fatal signal, restarted, or OOMed; honest/control
traffic continued; both victims and the Amaru-fed consumer recovered; and all
six tips converged at slot 1613. All three commands returned zero with semantic
checks true. Supporting Snouty validation against the production digest found
exactly one driver, one anytime, and one eventual command after the full setup
gate.

This package does not authorize a Moog or Antithesis submission.
