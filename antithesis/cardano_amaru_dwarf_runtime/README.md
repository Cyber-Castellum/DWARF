# Runtime-bootstrapped Cardano + Amaru + DWARF

> **STATUS: LOCAL COMPOSE PROOF PASSED — DO NOT PUBLISH OR SUBMIT WITHOUT
> SEPARATE APPROVAL.** The source-pinned Amaru 807 image creates native chain
> schema v5 stores from the live Cardano lineage. A clean-volume mixed proof
> crossed the bootstrap epoch and the following nonce transition while both
> Amaru relays and the Cardano consumer advanced. See `proof/README.md` and
> `proof/runtime-proof.json`.

This is a new, self-contained Antithesis package. It does not replace or alter
the existing fee, static-reference, control, or adversarial scenarios.

Its lifecycle follows the proven upstream `cardano_amaru` design used by
completed run `5271f5ea22ddde7a6f674084905aa335-56-17`:

1. `p1`, `p2`, and `p3` produce a fresh Cardano chain.
2. `bootstrap-producer` snapshots the live `p1` ChainDB into an isolated volume
   and creates the Amaru bundle from that exact lineage.
3. Two independent Amaru relays start from that bundle.
4. `amaru-consumer`, a Cardano node with no producer/relay route, can advance
   only through the Amaru relays. This proves Amaru serves the chain.
5. `dwarf-adversary` adds mutated BlockFetch traffic to `amaru-relay-1` only;
   `amaru-relay-2` remains the honest differential control.

The oracle requires liveness from both relays, a decoder rejection proving the
mutation path was exercised, and robustness against panic or fatal failure.
It deliberately does not treat transient equal-height tip differences as DWARF
acceptance: ordinary Cardano forks can produce that observation before rollback.

Infrastructure, Cardano, DWARF, and oracle services carry explicit fault
exclusions and matching `container_name` values. Only the two Amaru relays are
fault targets. This prevents the earlier service-key/runtime-name mismatch.

## Images

DWARF uses the existing immutable Antithesis-aware image by digest. The Amaru
bootstrap and both relays share one new source-pinned image, and the oracle is a
separate new package:

```text
ghcr.io/j-gainsec/dwarf-amaru-807-runtime:0.1.0
ghcr.io/j-gainsec/dwarf-amaru-runtime-oracle:0.1.0
```

The combined image checks out Amaru commit `493bffba`, applies the four locked
public patches, builds the release binary, and copies the exact Cardano 10.7.1
`db-analyser` closure plus `cardano-cli`. `bootstrap.py` accepts only immature
chain history and a live-copy race as retryable; all parser, protocol-state,
snapshot, import, and schema failures are fatal. It writes `READY.v5` last and
atomically commits the bundle. Relays copy that bundle into independent volumes
and never run a schema migration.

### Live nonce handoff

The shallow snapshots end in epoch 2, while Amaru starts in epoch 3. Snapshot
reconstruction alone does not reproduce the nonce used by the already-running
Cardano producers. Bootstrap therefore waits until live `p1` is inside epoch 3,
queries `cardano-cli conway query protocol-state`, and passes the live
`epochNonce` to an opt-in Amaru bootstrap override. The override is accepted
only when the requested target epoch is exactly one after the imported epoch.

Two fields must move together:

- `active` is the live epoch-3 nonce used to verify the first forward header.
- `tail` is reset to the imported epoch-2 tip so the correct ancestor is mixed
  with the stable candidate when deriving the epoch-4 active nonce.

Overriding only `active` is insufficient: it passes epoch 3 but fails at the
next boundary with an honest `Invalid VRF proof`. The clean proof explicitly
crosses epoch 4, requires zero consensus rejections on the untouched control,
and allows decoder rejection only as evidence that the target DWARF path ran.
No VRF or header verification is disabled.

The configurator chooses `systemStart: now`. Bootstrap therefore reads the
generated Shelley genesis and writes its actual millisecond timestamp into the
committed `global-parameters.json`; both relays load that value before startup.
Before submission, publish both new images and replace their tags with immutable
digests. Do not launch while either tag is unpublished.

## Local validation

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s oracle -p 'test_*.py' -v
python3 -m unittest discover -s bootstrap-image -p 'test_*.py' -v
INTERNAL_NETWORK=false docker compose config --quiet
INTERNAL_NETWORK=false docker compose config --format json | python3 tests/check_fault_targets.py
```

No credentials, signing keys, `.env`, or macOS `._*` metadata belong here.
