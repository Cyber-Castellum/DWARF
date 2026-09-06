# Mixed Cardano/Amaru hot-KES security package

This additive Antithesis package retains the proven
`cardano_amaru_relay_bootstrap_control` topology and adds two isolated
ChainSync paths that deliver the same signature-only hot-KES mutation to a
Cardano node and an Amaru node.

## Proven local contract

The proxies use seed `20260906`, keep all headers honest before slot `1800`,
then deterministically select and flip one bit within the final 448-byte KES
signature. Both victims depend on the same completed Cardano seed snapshot so
their streams overlap before mutation is enabled.

DWARF fresh-volume runs:

- `20260906T054611Z-54d146dd`: pass in 997 seconds; paired mutation at slot
  1837; both victims rejected it; 25 control matches; all three command
  semantics true.
- `20260906T060356Z-555ea49f`: independent pass in 984 seconds; paired mutation
  at slot 1817; both victims rejected it; 24 control matches; all three command
  semantics true; raw evidence retained automatically.

The second bundle contains `raw-cardano-proxy-events.jsonl`,
`raw-amaru-proxy-events.jsonl`, and `raw-amaru-victim.log`. The Amaru log
explicitly classifies the paired hash as `Invalid KES signature` and
`outcome="invalid_header"`.

## Local execution

Run through DWARF, not as an untracked Compose smoke:

```text
cardano-profile scenario run dwarf/scenarios/cardano-amaru-kes-security-local.yaml
```

The local scenario creates a unique Compose project and retains its volumes
and DWARF evidence bundle after stopping services.

## Fail-closed gates

- Bootstrap gets up to 30 minutes; the inherited mixed bootstrap normally
  takes about 11–13 minutes and must not be killed by a five-minute wrapper.
- The pinned Cardano image does not contain `find`; seed cleanup uses its
  available POSIX shell tools.
- The victim topology overlays the existing read-only
  `/configs/configs/topology.json` mountpoint.
- Both victims start only after `kes-cardano-seed` exits successfully.
- Both proxies must produce the same mutation identity, not merely one
  mutation each.
- Command output is parsed; exit code zero alone is not accepted.
- Missing endpoints, missing explicit Amaru classification, or unavailable
  tips cannot count as rejection.

## Antithesis boundary

The main Compose intentionally requires `DWARF_KES_IMAGE` and
`DWARF_KES_WORKLOAD_IMAGE`. Before submission, both must be public,
anonymous-pullable, digest-pinned images built from the locally proven source.
No Antithesis or Moog run is authorized by this package or the local proof.
