FROM ghcr.io/j-gainsec/dwarf-adversary:0.10.0
USER root
# python3 + the Antithesis SDK purely so the entrypoint can source a platform-controlled
# seed; the adversary itself is unchanged.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-minimal python3-pip ca-certificates \
 && pip3 install --no-cache-dir --break-system-packages antithesis \
 && apt-get purge -y python3-pip && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
COPY seed-entrypoint.sh /usr/local/bin/seed-entrypoint.sh
RUN chmod +x /usr/local/bin/seed-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/seed-entrypoint.sh"]
