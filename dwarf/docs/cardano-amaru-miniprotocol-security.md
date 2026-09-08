# Mixed Cardano/Amaru mini-protocol security scenario

## Current result

The additive `cardano_amaru_miniprotocol_security` package passed an exact,
fresh-volume, end-to-end run through DWARF on `cardano-box` using the dedicated
mini-protocol workload image. This is local runtime proof, not an Antithesis
campaign result and not authorization for another paid submission.

- DWARF run: `20260908T100054Z-80498541`
- Compose project: `dwarf-mixed-sm-20260908100054`
- Runtime result: pass in 895 seconds
- Explicit seed: `0x20260907`
- Dedicated workload image:
  `ghcr.io/j-gainsec/dwarf-sm@sha256:9fcea8709a6426c84fca6008435ae26a292734484b2006db6f99a331110bfb5a`
- Evidence directory on `cardano-box`:
  `/home/nigel/dwarf-v4/dwarf/runs/20260908T100054Z-80498541/outputs/cardano-amaru-miniprotocol-security`
- Further Antithesis submission authorized: `false`

## What was mechanically proved

The immutable workload image was inspected through a stopped container before
its entrypoint ran. `/opt/antithesis/test/v1` contained exactly one template,
`mixed-miniprotocol-security`, with exactly one driver, one anytime command, and
one eventual command, all mode `0755`; there were zero KES paths. The running
container used the normal `python3 /workload/workload_entrypoint.py` entrypoint,
with no discovery-root bind mount or startup catalog mutation.

Both fault-excluded SP4 processes used the same pinned binary, explicit seed,
single worker, connection budget, and protocol schedule. Their pre-fault common
prefix contained 94 identical generated cases, no mismatch, and all 24 cells:
ChainSync, BlockFetch, TxSubmission2, and KeepAlive crossed with WrongAgency,
OutOfState, PrematureTerminal, PostTerminal, Flood, and Duplicate.

After setup, Cardano received 2,600 classifiable cases and Amaru received 910.
Both endpoints remained reachable; both victims were healthy with restart count
zero, no OOM, and no classified fatal signal. Honest chain progress continued,
unrelated peer sessions remained usable, both victims and the isolated
Amaru-fed consumer recovered, and p1/p2/p3/Cardano/Amaru/consumer converged at
slot 1613, block 288, hash
`5fcdd2d268addee3dd7a82dc837393f8fac549a008ea92006e7c041bdd705b7d`.

All three commands returned zero and passed semantic checks:

- `parallel_driver_fuzz_observe.py`
- `anytime_containment.py`
- `eventually_recovery.py`

The focused package suite passed 18/18 tests including an opt-in Docker test
that builds and inspects the stopped image. Snouty 0.6.1 then started the
production Compose by its pinned digest, detected setup completion only after
the full gate, and reported exactly `0 first, 1 driver, 1 anytime, 1 eventually,
0 finally`. Snouty did not execute those commands; the DWARF run above supplies
that runtime evidence.

## Why the two live attempts are invalid

The first attempt, Antithesis run
`45500e6525fc1fad21f85f1af2d5ee17-60-7`, discovered the intended commands from
the submitted tree but browser/Windows publication stored them as `100644`;
invocation failed with `Permission denied`.

The runtime-copy repair passed locally, but retry
`239fd7d2ad494119b76bea20f1a38460-60-7` conclusively showed that live Composer
cataloged the immutable base image before the entrypoint ran. It reported
`found_directories=['mixed-kes-security']`, configured the three inherited KES
commands, and reported zero intended mini-protocol command paths. The mixed
network and both SP4 fuzzers ran, but the security test template did not. Neither
live attempt supports a security conclusion.

The durable repair is a new additive image rather than another runtime
workaround. Its clean Dockerfile pins both base images, installs
`antithesis==0.3.1`, and uses `COPY --chmod=0755` for only the intended template.

## Provenance and novelty boundary

- SP4 linux/amd64 manifest:
  `sha256:c5e35065b9a58c337cd770ef0317bf11c1057a5869d654f7873275d3c8fe1aa1`
- Proven mixed substrate revision:
  `cardano-node-antithesis@fe039ac5582081297b38709a6862083cc2fb6c00`
- Audited current upstream mixed revision:
  `cardano-node-antithesis@6711c4ab8fe02f6418897b73bf42ca41e6efa697`
- Audited Amaru main: `835e32c62321571fc6571de87ca548eebfc0d43a`
- Audited Cardano-node master: `c2ebdc87dfe07706a83e52f219e712c60d1b0a56`

Prior audits found Cardano-only SP4 work and mixed bootstrap, consensus, CBOR,
fee, and KES work, but not this paired illegal mini-protocol state-transition
differential. The known Amaru listener `EADDRINUSE` failure remains classified
background and must not be reported as a discovery from this scenario.

## Remaining gates

The new `dwarf-sm` package currently returns `unauthorized` to an anonymous GHCR
manifest request and must be made public. The repaired public-repository files
must then be published and byte/mode checked at a new commit. After anonymous
digest pull, exact stopped-image catalog inspection, full preflight, and a final
Moog plan review, any paid retry still requires explicit user approval.
