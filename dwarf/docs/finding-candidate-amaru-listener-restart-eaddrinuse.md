# Finding candidate — Amaru supervised listener restart can terminate consensus with EADDRINUSE

**From:** DWARF mixed Cardano/Amaru hot-KES Antithesis run  
**Date:** 2026-09-06  
**Status:** reproduced twice in one completed Antithesis run; upstream novelty not yet confirmed  
**Impact:** node availability, not evidence of consensus-safety failure

## Summary

Under Antithesis network fault injection, Amaru can receive an error from its
inbound accept effect, terminate the supervised accept stage, and attempt the
designed listener restart. The restart path logs
`connection.listener_restart`, but rebinding the same address can immediately
fail with `Address already in use (os error 98)`. The manager treats that bind
failure as fatal, terminates the consensus stage graph, and the container exits
with code 1.

This is distinct from the hot-KES property being tested. The KES properties
passed, and the failure occurred in the inherited Amaru relay control path.

## Exact run

- MOOG test run:
  `97a7c8d7fdd38026274aff3acd9c13272017d53e8c4c31d062a5a4bab91641c1`
- Antithesis run: `8417206dcfc6e6c97dc31e0c11a96bcb-60-7`
- Public DWARF commit: `59b2a0ece5f70710b6a15e288a6d7ee2cf53def8`
- Amaru image:
  `ghcr.io/lambdasistemi/amaru-bootstrap-producer@sha256:aabaf9e1fc1f58045329e14c1127c5424ba4794855d39bce05e3b426b7025c36`
- Amaru binary revision:
  `ea1f34e42c7a1806d8ee60b3f512e58daae7ccc1`

The failure occurred once on `amaru-relay-1` and once on `amaru-relay-2`.

## Representative causal sequence

At virtual time `1407.013054` on `amaru-relay-1`:

1. Antithesis applied network disruption to the relay path.
2. Amaru logged `connection.accept_failed reason="error" error="not connected"`.
3. Supervised stage `accept-11` terminated.
4. The manager issued `ListenEffect { addr: 0.0.0.0:3000 }`.
5. The network layer logged
   `connection.listener_restart address="0.0.0.0:3000"`.
6. The manager logged
   `manager.listen.failed ... "ListenError: Address already in use (os error 98)"`.
7. `manager-1` terminated, followed by `lifecycle.consensus_died` and
   `consensus stage graph terminated unexpectedly`.
8. The container died with exit code 1 and Docker restarted it.

The second relay followed the same terminal EADDRINUSE path at virtual time
`1640.810456`.

## Source analysis

In the exact exercised revision,
`crates/amaru-protocols/src/accept.rs` terminates the accept stage for an
`AcceptError::Other`. The stage is supervised with a
`ManagerMessage::Listen(listen_addr)` tombstone in
`crates/amaru-protocols/src/manager/mod.rs`.

`TokioConnections::listen()` in
`crates/amaru-network/src/connection.rs` explicitly says an existing listener
is aborted and awaited so supervised restarts work correctly. Its tests also
describe this call as idempotent. Despite reaching that exact restart branch in
the live run, the following bind failed with EADDRINUSE. The manager's error
branch calls `eff.terminate()`, escalating a transient listener-restart failure
into total consensus shutdown.

This source path is unchanged on Amaru `main` as checked on 2026-09-06.

## Prior-report audit

Searches of the current Amaru repository, issues, and pull requests found no
report containing the current `manager.listen.failed` supervised-restart
sequence. [Amaru issue #219](https://github.com/pragma-org/amaru/issues/219)
contains an older address-in-use symptom from the 2025 daemon/gasket startup
stack; it does not establish that this 2026 pure-stage/Tokio restart mechanism
is known or fixed.

Until maintainers confirm otherwise, describe this as a **candidate new
availability finding**, not a confirmed novel vulnerability.

## Recommended next verification

Add an upstream Tokio-backed regression test that forces a real accept failure
while the listener is active, then verifies repeated supervised restarts cannot
return EADDRINUSE or terminate the manager. A product fix should serialize the
listener replacement and retry transient bind failures rather than terminate
the entire consensus graph.

