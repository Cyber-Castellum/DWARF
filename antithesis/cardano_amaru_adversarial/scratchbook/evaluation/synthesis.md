---
sut_path: /Users/nigel/dwarf-project/dwarf-v4/antithesis/cardano_amaru_adversarial
updated: 2026-08-22
external_references:
  - path: https://github.com/pragma-org/amaru/wiki
    why: Amaru architecture and operating model.
  - path: https://bench.gainpalfam.com/wb/moog
    why: Prior test decisions and operational state.
---

# Evaluation Synthesis

## Final refinements

- The initial fresh configurator fixture was rejected because it did not share ledger
  state with the baked Amaru store.
- The final fixture and Cardano oracle are derived from the exact state used to build
  Amaru's store.
- Preparation failure remains `unknown`; only verified validation-layer responses are
  phase-1 rejection.
- Transport failures are inconclusive, while any acceptance remains a safety failure.
- Reachability and `Sometimes` assertions prevent vacuous green results.
- The static fixture preserves the fixed point `actual_fee = minimum_fee - 1` and
  removes all signing-key requirements from launch.

## Evidence resolved

The shared-ledger reachability question is closed. Cardano and Amaru both evaluated
transaction `40b279...ccc17` past preparation and rejected it. Cardano exposed the
exact fee mismatch; Amaru exposed its intentionally generic validation error.

Published digest-pinned images passed official local `snouty validate`, and anonymous
manifest retrieval returns HTTP 200. The adversary's SDK
stdout/seed contamination was also reproduced, regression-tested, and fixed before
the final validation.

## Remaining action

Push the exact commit, launch through release MOOG in `amaru-cardano`, and triage the
paid run. No architecture, registry, or ledger-reachability
blocker remains.

## Deferred property families

Minimum output, validity interval, value conservation, witnesses, and input-set
differentials remain valuable follow-ons, but mixing them into this fixture would
reduce causal clarity.
