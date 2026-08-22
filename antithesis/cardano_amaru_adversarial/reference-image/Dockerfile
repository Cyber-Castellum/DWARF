FROM ghcr.io/intersectmbo/cardano-node@sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c

COPY configs/ /configs/
COPY state/ /state/

ENTRYPOINT ["/usr/local/bin/cardano-node"]
CMD ["run", "--topology", "/configs/topology.json", "--config", "/configs/config.json", "--database-path", "/state", "--socket-path", "/state/node.socket", "--port", "3901", "--host-addr", "0.0.0.0"]
