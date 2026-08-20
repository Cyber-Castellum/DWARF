FROM ghcr.io/pragma-org/amaru:v10.11.20260807
USER root
# patched binary (local-manifest bootstrap + tvar definite-map fix)
COPY amaru /usr/local/bin/amaru
COPY store /opt/amaru-baked/store
COPY entrypoint.sh /usr/local/bin/dwarf-amaru-entrypoint.sh
RUN chmod +x /usr/local/bin/amaru /usr/local/bin/dwarf-amaru-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/dwarf-amaru-entrypoint.sh"]
CMD []
