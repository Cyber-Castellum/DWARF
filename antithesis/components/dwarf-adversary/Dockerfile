# Thin runtime image for dwarf-adversary.
#
# The dwarf-adversary binary is built on the host (build-host: Ubuntu
# 24.04, GHC 9.6.7 via ghcup, CHaP) — see build-image.sh, which stages
# the binary plus the IOG-patched C libs into ./dist before this build.
# We deliberately do NOT recompile the Cardano stack inside Docker (that
# takes ~45 min and a separate cabal store); we ship the already-linked
# binary on a glibc-matching ubuntu:24.04 base. (The original full
# nix/cabal multi-stage build is preserved in git history if a fully
# self-contained image build is ever needed.)
FROM ubuntu:24.04

ENV TZ=UTC
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      liblmdb0 libgmp10 zlib1g libstdc++6 jq ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# IOG-patched C libraries the binary links against (from the build host).
COPY dist/libsodium.so.23 /usr/local/lib/
COPY dist/libsecp256k1.so.2 /usr/local/lib/
RUN ldconfig

# The pre-built dwarf-adversary binary. --chmod sets the mode in the COPY
# layer itself; a separate `RUN chmod` would rewrite the whole ~140MB binary
# into a second layer (doubling the image). Debug symbols are kept (not
# stripped) — they aid post-mortem analysis on any finding.
COPY --chmod=0755 dist/dwarf-adversary /usr/local/bin/dwarf-adversary

# Also ship the in-process decoder-fuzz harness (#4/FU5) + its baked seed corpus
# in this SAME (public) image, so the Antithesis decoder-fuzz workload reuses this
# package rather than a separate ghcr package (new packages default to private and
# are unreachable by the Antithesis image builder). The decoder-fuzz compose
# service overrides ENTRYPOINT to /usr/local/bin/dwarf-decoder-fuzz.
COPY --chmod=0755 dist/dwarf-decoder-fuzz /usr/local/bin/dwarf-decoder-fuzz
COPY dist/corpus /corpus
# Genesis (Shelley/Alonzo/Conway) for the decoder-fuzz applyblock target, which
# builds an initial Conway NewEpochState from it. Other targets ignore it. The
# applyblock decoder-fuzz compose service sets DWARF_GENESIS_DIR=/ledger-genesis.
COPY dist/ledger-genesis /ledger-genesis
COPY dist/corpus-conway /corpus-conway

# No Antithesis composer scripts: dwarf-adversary is a self-driving
# daemon and emits all its SDK assertions itself (dwarf_fuzz_server_*,
# dwarf_base_header_obtained, dwarf_node_connected, dwarf_served_mutated_header).
# A lone finally_ command was flagged "not started/finished" by the test
# templates, so we ship none — the testnet's other components drive the test.

# Run the chain-sync fuzz server as the container command; the compose
# service supplies the args (--serve --fuzz --network-magic N
# --listen-port 3001 --mutation-rate R --upstream HOST:PORT --seed S).
ENTRYPOINT ["/usr/local/bin/dwarf-adversary"]
