FROM ubuntu:24.04

ENV TZ=UTC
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      liblmdb0 libgmp10 zlib1g libstdc++6 ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY dist/libsodium.so.23 /usr/local/lib/
COPY dist/libsecp256k1.so.2 /usr/local/lib/
RUN ldconfig

COPY --chmod=0755 dist/dwarf-kes-adversary /usr/local/bin/dwarf-kes-adversary
ENTRYPOINT ["/usr/local/bin/dwarf-kes-adversary"]
