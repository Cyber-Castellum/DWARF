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

This package does not authorize a Moog or Antithesis submission.

