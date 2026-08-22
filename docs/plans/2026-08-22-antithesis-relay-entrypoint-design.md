# Antithesis Relay Entrypoint Execution Design

## Context

Antithesis run `e512a7b37d7d23b7b8a5dfe6e908d9c3b5794f64b861a0e2372a9862b77c3d3f`
started the mixed phase-1 workload and the Cardano reference correctly, but both
Amaru relay services entered restart loops. The live logs reported:

```text
exec: /usr/local/bin/dwarf-amaru-entrypoint.sh: Permission denied
```

The submitted Git tree records `relay-image/entrypoint.sh` as mode `100755`.
The failure is therefore at the Antithesis bind-mount execution boundary: a
repository file mounted into the container cannot be assumed to remain directly
executable.

## Decision

Keep the existing read-only bind mount, but invoke the mounted script through
the shell already used as the service entrypoint:

```sh
exec /bin/sh /usr/local/bin/dwarf-amaru-entrypoint.sh
```

Apply the same command to `amaru-relay-1` and `amaru-relay-2`. This preserves
the script's existing final `exec /usr/local/bin/amaru ...` process replacement
while removing dependence on the bind-mounted file's executable bit or mount
execution policy.

## Alternatives considered

- Bake the relay entrypoint into a new Amaru image. This is robust but requires
  another image build, publication, digest update, and public-visibility check.
- Copy the bind-mounted script into a writable filesystem and `chmod +x` it.
  This adds mutable startup state and still solves a problem the shell
  interpreter can avoid directly.

The interpreter invocation is the smallest sufficient change.

## Regression coverage

Extend `workload/tests/test_bundle_contract.py` to require both relay commands
to contain the interpreter invocation and to reject the direct-exec form that
failed in Antithesis. Run the new test before implementation to demonstrate the
current bundle fails the contract, then rerun it after the Compose change.

## Verification and rollout

Run the complete workload unit suite, Compose rendering, whitespace checks,
and the existing secret/key scans on `cardano-box`. Commit and push only the
intended public-safe files. Submit a new one-hour `try 1` through release MOOG
with faults enabled, then confirm in Antithesis logs that both Amaru processes
start and that the mixed classifiability assertion is reachable.
