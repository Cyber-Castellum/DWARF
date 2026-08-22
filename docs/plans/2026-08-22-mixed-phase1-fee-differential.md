# Mixed Cardano/Amaru Phase-1 Fee Differential Implementation Record

**Goal:** exercise a new, non-duplicative phase-1 fee boundary through MOOG and the
`amaru-cardano` Antithesis tenant.

## Completed

1. Reviewed prior DWARF reports, project notes, Amaru wiki/issues, and existing
   assertions to exclude already-covered fork, rollback, decoder, reconnect,
   local-diffusion, and phase-2 work.
2. Implemented strict same-byte dual submission and five response classes in
   `workload/mixed_phase1.py`.
3. Added Antithesis driver and eventual commands under
   `/opt/antithesis/test/v1/mixed-phase1/` and cataloged assertions.
4. Reproduced the initial fresh-state failure: Cardano reached `FeeTooSmallUTxO`, but
   Amaru returned a preparation failure because its baked store did not contain the
   fresh configurator UTxO.
5. Proved the original shared genesis UTxO exists in the retained Cardano block-216
   snapshot and in Amaru's baked store.
6. Built a static `minimum - 1` fixture using the matching key in a temporary private
   directory, retained only the signed envelope and public metadata, and removed the
   runtime key/fixture-builder path.
7. Added the public baked Cardano reference image and wired its socket to
   `cardano-submit-api` through a named volume.
8. Verified the same transaction reaches validation on both implementations and both
   reject it.
9. Added a regression for Antithesis SDK stdout contaminating the adversary seed and
   fixed the wrapper to select only a complete unsigned integer.
10. Published all new images, made them anonymously pullable, and pinned Compose to
    immutable digests.
11. Passed 22 unit/contract tests and official `snouty validate` against the public
    images.

## Remaining

1. Update scratchbook, runbook, and MOOG workbench note with the final evidence.
2. Run final secret and macOS metadata scans; verify no `._*` files.
3. Commit the implementation on `cardano-box` and push the public branch/commit.
4. Submit with release MOOG to `Cyber-Castellum/DWARF`, directory
   `antithesis/cardano_amaru_adversarial`, tenant `amaru-cardano`.
5. Track the MOOG request through Antithesis creation, then triage the completed run.

## Required verification commands

```bash
python3 -m unittest discover \
  -s antithesis/cardano_amaru_adversarial/workload/tests -v

INTERNAL_NETWORK=false docker compose \
  -f antithesis/cardano_amaru_adversarial/docker-compose.yaml config --quiet

snouty validate antithesis/cardano_amaru_adversarial --timeout 180
```

Never classify `failed to prepare transaction ... for validation` as fee agreement,
never regenerate the fixture from a fresh configurator state, and never include or
mount the recovered genesis signing key.
