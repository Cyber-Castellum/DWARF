# Mixed Cardano/Amaru mini-protocol security scenario

## Result

The additive `cardano_amaru_miniprotocol_security` package passed an exact,
fresh-volume, end-to-end local run through DWARF on `cardano-box`. This is local
runtime proof, not an Antithesis campaign result and not authorization to submit
a paid run.

- DWARF run: `20260908T045616Z-4603c8e1`
- Compose project: `dwarf-mixed-sm-20260908045616`
- Runtime result: pass in 895 seconds
- Explicit seed: `0x20260907`
- Rendered Compose SHA-256:
  `254deec04f790f0586b67dd1dd57a6618c8e049edeb7a2c034997cc1b48db7e6`
- Evidence directory on `cardano-box`:
  `/home/nigel/dwarf-v4/dwarf/runs/20260908T045616Z-4603c8e1/outputs/cardano-amaru-miniprotocol-security`
- Antithesis submitted: `false`

## What was mechanically proved

The two fault-excluded SP4 processes used the same pinned binary, explicit seed,
single-worker selector order, connection budget, and protocol schedule. Each
started only after its target passed semantic health checks. Before
`setup_complete`, their ordered common transcript prefix contained 94 identical
generated cases, had no mismatch, and covered every one of the 24 cells formed
by ChainSync, BlockFetch, TxSubmission2, and KeepAlive crossed with WrongAgency,
OutOfState, PrematureTerminal, PostTerminal, Flood, and Duplicate.

After setup the Cardano victim received 2,604 more classifiable cases and the
Amaru victim received 912. Both target endpoints remained reachable, both
victim containers were running and healthy with restart count zero and no OOM,
and neither target log contained a classified panic or fatal termination. The
honest control chain continued, unrelated peer sessions remained usable, both
victims recovered, and the isolated Amaru-fed consumer converged. The final
sample placed p1, p2, p3, the Cardano victim, Amaru victim, and consumer at the
same slot 1,614, block 285, and hash
`cd3863bd186dc4c035540f27b745e38c3cbed4b229fb37c815bb3bdb6d1e936b`.

All three installed workload commands returned zero and passed their semantic
output checks:

- `parallel_driver_fuzz_observe.py`
- `anytime_containment.py`
- `eventually_recovery.py`

The combined new-package and inherited mixed bootstrap/KES regression selection
passed: 57 tests. Both production and local-overlay Compose configurations
rendered successfully, and DWARF semantic validation returned `OK` for
`cardano-amaru-miniprotocol-security-local`.

Official Snouty 0.6.1 validation on Linux x86-64 also passed from freshly
deleted volumes. It detected setup completion only after the full gate and
discovered exactly `0 first, 1 driver, 1 anytime, 1 eventually, 0 finally`
commands. An earlier validation revealed that mounting this suite alongside the
published base image's KES commands produced two commands of each kind; the
final Compose file mounts the whole scenario test root to isolate discovery.

## Frozen provenance and novelty boundary

- SP4 linux/amd64 manifest:
  `sha256:c5e35065b9a58c337cd770ef0317bf11c1057a5869d654f7873275d3c8fe1aa1`
- Proven mixed substrate revision:
  `cardano-node-antithesis@fe039ac5582081297b38709a6862083cc2fb6c00`
- Audited current upstream mixed revision:
  `cardano-node-antithesis@6711c4ab8fe02f6418897b73bf42ca41e6efa697`
- Audited Amaru main:
  `835e32c62321571fc6571de87ca548eebfc0d43a`
- Audited Cardano-node master:
  `c2ebdc87dfe07706a83e52f219e712c60d1b0a56`

The source/wiki/issues/PR and W30-W35 report audit found Cardano-only SP4 work
and mixed bootstrap, consensus, CBOR, fee, and KES work, but not this paired
illegal mini-protocol state-transition differential. Amaru networking PRs
#1319-#1327 are adjacent rather than duplicate coverage. The previously reported
Amaru listener `EADDRINUSE` failure remains a classified background signal and
must not be reported as a discovery from this scenario.

## Preflight status

The implementation, topology, artifact, workload, property, exact local-runtime,
anonymous-image, secret-hygiene, and Snouty gates pass. The corrected Compose
mount and its regression/documentation update must be published and hash-checked
before the final Moog payload can pass. A paid Antithesis/Moog submission remains
explicitly blocked pending that publication evidence and fresh user approval.

The first compatibility run, `20260907T104847Z-b0ab5290`, is not proof: it
correctly exposed paired-selector drift caused by starting fuzzers at container
startup rather than target readiness. The final package fixes that by using
target health checks and `service_healthy`; only the later run above is the
acceptance evidence.
