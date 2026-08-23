FROM rust:bookworm@sha256:e70e2eec3d495fd5c8e0be74adda86507dfac7f51a724fbf9813ff59b2b247c7 AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        libclang-dev \
        libssl3 \
        llvm-14-runtime \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone https://github.com/pragma-org/amaru.git amaru \
    && cd amaru \
    && git checkout --detach 493bffba0cc4db2291643cdd6698197c374958b3

COPY patches /patches
WORKDIR /src/amaru
RUN for patch in /patches/*.patch; do \
        git apply --check "$patch"; \
        git apply "$patch"; \
    done
RUN cargo build --release --locked --bin amaru

FROM ghcr.io/intersectmbo/cardano-node@sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c AS cardano

FROM python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7 AS runtime

COPY --from=cardano /nix/store /nix/store
COPY --from=cardano /usr/local/bin/db-analyser /usr/local/bin/db-analyser
COPY --from=cardano /usr/local/bin/cardano-cli /usr/local/bin/cardano-cli
COPY --from=builder /src/amaru/target/release/amaru /usr/local/bin/amaru
COPY resolve_snapshot_points.py /usr/local/bin/resolve_snapshot_points.py
COPY bootstrap.py /usr/local/bin/bootstrap.py

ENTRYPOINT ["/usr/local/bin/amaru"]
