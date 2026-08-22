---
sut_path: /Users/nigel/dwarf-project/dwarf-v4/antithesis/cardano_amaru_adversarial
commit: b2ed2450ebb95e460e7c64f0d2578102be4d7663
updated: 2026-08-22
external_references:
  - path: https://github.com/pragma-org/amaru/wiki
    why: Amaru architecture and operating model.
  - path: https://bench.gainpalfam.com/wb/moog
    why: Prior test decisions and deployment state.
  - path: /Users/nigel/dwarf-project/dwarf-v4/dwarf/docs
    why: Prior findings and exclusions.
  - path: /Users/nigel/dwarf-project/dwarf-v4/reports
    why: Prior evidence bundles.
  - path: https://github.com/pragma-org/amaru/issues/1265
    why: Excluded reconnect behavior.
  - path: https://github.com/pragma-org/amaru/issues/1156
    why: Excluded diffusion behavior.
  - path: https://github.com/pragma-org/amaru/issues/1004
    why: Excluded phase-2 behavior.
---

# Implementability Evaluation

Exact-byte submission is implemented with an immutable signed fixture baked into the
workload image. The selected fixture is invalid and therefore replay-safe. HTTP
responses make the property externally observable, so no SUT instrumentation is
required for the first run.

State compatibility is resolved by the paired baked Cardano reference snapshot. The
classifier refuses to count preparation failures or `BadInputsUTxO` as fee
reachability. Live dual-response evidence and digest-pinned `snouty validate` both pass.

## Assumptions

- Relative mounts and named volumes work in the MOOG config layout.

## Remaining gate

- Commit the public-safe bundle and run it through release MOOG in `amaru-cardano`.
