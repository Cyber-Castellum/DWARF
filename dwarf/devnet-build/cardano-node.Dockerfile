# Multi-stage Dockerfile that builds cardano-node + cardano-cli + cardano-testnet
# from a local source checkout.
#
# Build context: codebases/cardano-node on the build host.
# Output image:  dwarf/cardano-node:<git-sha>
#
# Usage (from profiles.deploy_command):
#   docker build \
#     --file dwarf/devnet-build/cardano-node.Dockerfile \
#     --tag dwarf/cardano-node:<git-sha> \
#     codebases/cardano-node
#
# The build takes a long time (~30-45 minutes cold) because it's a full Haskell
# source build. Docker's layer cache makes incremental rebuilds fast once the
# first build has completed on a given host.

FROM haskell:9.6.7 AS build
WORKDIR /src

# System dependencies required by cardano-node's Haskell build.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config libffi-dev libgmp-dev libssl-dev \
    libtinfo-dev libsystemd-dev zlib1g-dev libsecp256k1-dev \
    libsodium-dev liblmdb-dev liburing-dev git wget \
    && rm -rf /var/lib/apt/lists/*

# Copy the source tree in one shot. Requires the build context to be the
# cardano-node checkout.
COPY . .

# Build the three binaries we actually need in the image.
RUN cabal update && \
    cabal build --enable-executable-dynamic \
      :pkg:cardano-node:exe:cardano-node \
      :pkg:cardano-cli:exe:cardano-cli \
      :pkg:cardano-testnet:exe:cardano-testnet && \
    mkdir -p /out && \
    cp $(cabal list-bin :pkg:cardano-node:exe:cardano-node)        /out/cardano-node && \
    cp $(cabal list-bin :pkg:cardano-cli:exe:cardano-cli)          /out/cardano-cli && \
    cp $(cabal list-bin :pkg:cardano-testnet:exe:cardano-testnet)  /out/cardano-testnet

# Runtime image.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgmp10 libssl3 libtinfo6 libsodium23 libsecp256k1-1 liblmdb0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/cardano-node    /usr/local/bin/cardano-node
COPY --from=build /out/cardano-cli     /usr/local/bin/cardano-cli
COPY --from=build /out/cardano-testnet /usr/local/bin/cardano-testnet

# The container expects a volume at /env containing a fully-generated
# cardano-testnet environment (config, genesis, keys, topology).
# CARDANO_NODE_SOCKET_PATH is set per-service in the compose file.
WORKDIR /env
ENTRYPOINT ["/usr/local/bin/cardano-node", "run"]
