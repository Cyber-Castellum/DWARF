# Clean mixed-runtime proof

Date: 2026-08-23

Project: `dwarf-runtime807-proof-tail-fixed-20260823`

This proof used new Docker volumes for every Cardano, bootstrap, Amaru, relay,
and consumer store. It used local runtime image ID
`sha256:7512e0cca44682ce5b6780c49c0e670d1befa85bfbc4e93abb8ba8001060e221`
and the pinned DWARF image recorded in `runtime-proof.json`.

The condition-driven runner completed with exit code 0:

```text
INTERNAL_NETWORK=false python3 tests/prove_runtime.py \
  --project dwarf-runtime807-proof-tail-fixed-20260823 \
  --compose-file docker-compose.yaml \
  --timeout 1800 --interval 5 --epoch-length 400 \
  --evidence proof/runtime-proof.json
```

The retained evidence shows:

- bootstrap exited 0 and produced native chain schema v5 state;
- both Amaru relays advanced from height 286/slot 1311 across epoch 4;
- the target reached at least height 347/slot 1612 and recorded one DWARF-path
  decoder rejection;
- the honest control reached height 350/slot 1616 with zero decoder or
  consensus rejections;
- the isolated Cardano consumer matched all three producers at height 350,
  hash `6b802a62343f44c63fe23fcc4cd5b696930f57398140a0ad8e2096fe528ae7d8`;
- both Amaru restart counts remained zero; and
- no panic, schema, reward-discrepancy, or fatal signature appeared.

After the runner completed, both relays continued adopting the same honest
headers through at least slot 1644. This is a local readiness proof only. The
runtime/oracle images are not authorized for publication and no Antithesis run
was submitted.
