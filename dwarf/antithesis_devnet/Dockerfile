# Bake a pre-generated single-node devnet env into the cardano-node image so it
# boots hermetically inside Antithesis (no host mounts, no internet).
# Build context must contain ./env (produced by build.sh via cardano-testnet).
#
# The env (from `cardano-testnet create-env`) places the node config at
# configuration.yaml at the env root (genesis files resolve relative to it) and
# the topology under node-data/node1/. The node runs as a plain relay: it needs
# only to boot and accept node-to-node connections so the workload can drive CBOR.
#
# create-env files are root-owned with restrictive modes and the base image runs
# as a non-root user with no passwd entries (so USER/RUN chown won't work).
# COPY --chmod makes the env world-readable; db + socket live in /tmp (writable).
ARG BASE_IMAGE=dwarf/cardano-node:10.7.1
FROM ${BASE_IMAGE}
COPY --chmod=0777 env /devnet/env
ENTRYPOINT ["cardano-node", "run", \
  "--config", "/devnet/env/configuration.yaml", \
  "--topology", "/devnet/env/node-data/node1/topology.json", \
  "--database-path", "/tmp/db", \
  "--socket-path", "/tmp/node.sock", \
  "--port", "3001", \
  "--host-addr", "0.0.0.0"]
