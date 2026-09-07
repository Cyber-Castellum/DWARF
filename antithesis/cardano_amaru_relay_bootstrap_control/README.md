# DWARF Cardano–Amaru relay-bootstrap control

This is a new additive control package. It does not replace any existing DWARF
or Cardano Foundation scenario.

The package combines three Cardano block producers, two Cardano relays, two
relay-only Amaru nodes, and one isolated Cardano consumer. Each Amaru relay uses
the current upstream `amaru-relay-bootstrap` entrypoint and independently
derives its bootstrap state from its paired producer's live ChainDB:

```text
p1 ChainDB -> amaru-relay-1 --\
                                -> isolated amaru-consumer
p2 ChainDB -> amaru-relay-2 --/
```

The consumer topology contains only the two Amaru relays. A one-shot seed
service waits for relay 1's real `.bootstrap-complete` sentinel, copies the
then-current p1 ChainDB into a private consumer volume, and exits. The consumer
must subsequently advance beyond that seed through Amaru and converge with a
producer. Starting containers or observing the early relay startup markers is
not sufficient evidence.

The current relay image listens for downstream node-to-node clients on port
`3000`; port `3001` is the Cardano-node producer port used by each relay's own
upstream connection. The configurator wrapper also normalizes the generated
Shelley and Byron start times across all three pools before any node starts.
These are runtime-enforced contracts: the consumer topology test requires port
`3000`, and the probe aborts if any producer has a different genesis clock.

See `UPSTREAM-PROVENANCE.md` for exact revisions, historical mixed-network
proof, current evidence limits, and the immutable image mapping.

## Static validation

```bash
pytest -q tests/test_cardano_amaru_relay_bootstrap_control.py
INTERNAL_NETWORK=false docker compose \
  -f antithesis/cardano_amaru_relay_bootstrap_control/docker-compose.yaml \
  config --quiet
```

## Required runtime proof

A successful local control must be started through DWARF on `cardano-box` with
fresh dedicated volumes and must retain evidence for all of these conditions:

1. both relays extract three nonempty completed-epoch target rows;
2. all producer Shelley/Byron genesis clocks are identical;
3. both relays create `.bootstrap-complete` and exec `amaru run`;
4. both Amaru processes advance beyond their independent bootstrap anchors;
5. both survive at least one post-bootstrap epoch boundary without a fatal
   consensus, rewards, nonce, VRF, or rollback error; and
6. the isolated consumer advances beyond its seed and reaches a producer tip.

No Moog request or Antithesis launch is authorized by this package.

The control deliberately uses `AMARU_LOG=debug` so its mechanical probe can
extract independently accepted roll-forward slots from both relays. A later
security package may narrow logging only after replacing that proof with an
equally strong observable.
