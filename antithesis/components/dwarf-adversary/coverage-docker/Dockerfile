# syntax=docker/dockerfile:1
# DWARF Haskell coverage-fuzzer image.
# Builds the cardano-node Conway decode/ledger/mini-protocol surfaces with
# native LLVM SanitizerCoverage edge instrumentation (GHC -fllvm + a new-PM
# pass plugin) and runs them under AFL++ — coverage-guided fuzzing of the real
# Haskell node, reproducibly, in Docker. Build context = the dwarf-adversary
# package root. Target platform linux/amd64 (LLVM-15 + cardano C deps).
ARG UBUNTU=ubuntu:24.04

# ───────────────────────── builder ─────────────────────────
FROM --platform=linux/amd64 ${UBUNTU} AS builder
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential autoconf automake libtool pkg-config git curl ca-certificates xz-utils \
      libffi-dev libgmp-dev zlib1g-dev libtinfo-dev libncurses-dev libnuma-dev \
      llvm-15 llvm-15-dev clang-15 \
 && rm -rf /var/lib/apt/lists/*

# Cardano native C deps (pinned to the versions cardano-ledger resolves against).
ARG LIBSODIUM_REV=dbb48cce5429cb6585c9034f002568964f1ce567
ARG SECP256K1_REV=v0.3.2
ARG BLST_REV=v0.3.14
RUN git clone https://github.com/intersectmbo/libsodium && cd libsodium \
 && git checkout $LIBSODIUM_REV && ./autogen.sh -s && ./configure && make -j"$(nproc)" && make install && ldconfig && cd / && rm -rf libsodium
RUN git clone https://github.com/bitcoin-core/secp256k1 && cd secp256k1 \
 && git checkout $SECP256K1_REV && ./autogen.sh && ./configure --enable-module-schnorrsig --enable-experimental \
 && make -j"$(nproc)" && make install && ldconfig && cd / && rm -rf secp256k1
RUN git clone https://github.com/supranational/blst && cd blst && git checkout $BLST_REV && ./build.sh \
 && cp libblst.a /usr/local/lib/ && cp bindings/blst.h bindings/blst_aux.h /usr/local/include/ \
 && mkdir -p /usr/local/lib/pkgconfig \
 && printf 'prefix=/usr/local\nexec_prefix=${prefix}\nlibdir=${exec_prefix}/lib\nincludedir=${prefix}/include\n\nName: libblst\nDescription: blst\nVersion: 0.3.14\nLibs: -L${libdir} -lblst\nCflags: -I${includedir}\n' > /usr/local/lib/pkgconfig/libblst.pc \
 && cd / && rm -rf blst
ENV LD_LIBRARY_PATH=/usr/local/lib PKG_CONFIG_PATH=/usr/local/lib/pkgconfig

# GHC 9.6.7 + cabal 3.16.0.0 via ghcup (pinned).
ENV BOOTSTRAP_HASKELL_NONINTERACTIVE=1 \
    BOOTSTRAP_HASKELL_GHC_VERSION=9.6.7 \
    BOOTSTRAP_HASKELL_CABAL_VERSION=3.16.0.0 \
    BOOTSTRAP_HASKELL_INSTALL_NO_STACK=1 \
    GHCUP_INSTALL_BASE_PREFIX=/opt
RUN curl -sSf https://get-ghcup.haskell.org | sh
ENV PATH=/opt/.ghcup/bin:${PATH}

# SanCov toolchain + AFL runtime object. The afl-compiler-rt.o + afl-fuzz/showmap
# are the PROVEN afl.rs 4.40c binaries (vendored in build context) — a fresh
# `git clone` of AFL main + a manual rt compile fails the forkserver handshake
# against the huge whole-tree coverage map; the vendored 4.40c rt+fuzzer match
# and work. The rt is linked into the harness; the fuzzer ships in the runtime.
COPY coverage-docker/sancov/GhcSancov.cpp coverage-docker/sancov/libsancovrt.c coverage-docker/sancov/afl-compiler-rt.o /opt/dwarf-sancov/
RUN cd /opt/dwarf-sancov \
 && g++ -fPIC -shared GhcSancov.cpp -o GhcSancov.so $(llvm-config-15 --cxxflags) $(llvm-config-15 --ldflags) \
 && gcc -shared -fPIC -O2 -o libsancovrt.so libsancovrt.c
RUN D=/opt/dwarf-sancov; G=/opt/.ghcup/bin/ghc-9.6.7; \
    printf '#!/bin/bash\nset -e\nout="";prev=""\nfor a in "$@";do [ "$prev" = "-o" ] && out="$a";prev="$a";done\nopt-15 "$@"\nopt-15 -load-pass-plugin=%s/GhcSancov.so -passes=ghc-sancov "$out" -o "$out"\n' "$D" > $D/optwrap.sh && \
    printf '#!/bin/bash\nfor a in "$@";do [ "$a" = "-shared" ] && exec gcc "$@";done\nexec gcc -no-pie "$@" %s/afl-compiler-rt.o -ldl -lpthread\n' "$D" > $D/linkwrap.sh && \
    printf '#!/bin/bash\nREAL=%s\nif "$REAL" -fllvm -pgmlo %s/optwrap.sh -pgmlc llc-15 -pgml %s/linkwrap.sh "$@";then exit 0;fi\nexec "$REAL" "$@"\n' "$G" "$D" "$D" > $D/ghcw.sh && \
    chmod +x $D/optwrap.sh $D/linkwrap.sh $D/ghcw.sh

# Extra cardano pkg-config sys dep (cardano-lmdb needs lmdb). Late layer so the
# expensive toolchain layers above stay cached.
RUN apt-get update && apt-get install -y --no-install-recommends liblmdb-dev \
 && rm -rf /var/lib/apt/lists/*

# Build the instrumented harnesses against the whole instrumented cardano dep tree.
WORKDIR /src
COPY *.cabal cabal.project ./
COPY src ./src
COPY app ./app
COPY fuzz ./fuzz
COPY fuzz-one ./fuzz-one
COPY fuzz-surface ./fuzz-surface
COPY test ./test
COPY LICENSE CHANGELOG.md ./
RUN printf 'with-compiler: /opt/dwarf-sancov/ghcw.sh\nwith-hc-pkg:   /opt/.ghcup/bin/ghc-pkg-9.6.7\n\nshared: False\nexecutable-dynamic: False\n\npackage *\n  ghc-options: -optP-DDWARF_SANCOV\n' > cabal.project.local
ARG JOBS=2
RUN cabal update && LD_PRELOAD=/opt/dwarf-sancov/libsancovrt.so cabal build dwarf-decode-any dwarf-decode-one -j${JOBS}
RUN cp "$(LD_PRELOAD=/opt/dwarf-sancov/libsancovrt.so cabal list-bin dwarf-decode-any)" /opt/dwarf-decode-any \
 && cp "$(LD_PRELOAD=/opt/dwarf-sancov/libsancovrt.so cabal list-bin dwarf-decode-one)" /opt/dwarf-decode-one

# ───────────────────────── runtime ─────────────────────────
FROM --platform=linux/amd64 ${UBUNTU} AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends libgmp10 libnuma1 libtinfo6 python3 libpython3.12 libexpat1 zlib1g liblmdb0 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/libsodium.so* /usr/local/lib/libsecp256k1.so* /usr/local/lib/
RUN ldconfig
COPY coverage-docker/sancov/afl-fuzz coverage-docker/sancov/afl-showmap /usr/local/bin/
COPY --from=builder /opt/dwarf-decode-any /opt/dwarf-decode-one /usr/local/bin/
COPY coverage-docker/run.sh /usr/local/bin/dwarf-cov-run
COPY corpora /opt/corpora
COPY ledger-genesis /opt/ledger-genesis
COPY tools/gen-cov-corpora.py /opt/gen-cov-corpora.py
RUN chmod +x /usr/local/bin/dwarf-cov-run
ENV AFL_SKIP_CPUFREQ=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 AFL_NO_AFFINITY=1
ENTRYPOINT ["/usr/local/bin/dwarf-cov-run"]
CMD ["applytx", "60"]
