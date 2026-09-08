FROM ghcr.io/intersectmbo/cardano-node@sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c AS cardano

FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

COPY --from=cardano /nix/store /nix/store
COPY --from=cardano /usr/local/bin/cardano-cli /usr/local/bin/cardano-cli
RUN pip install --no-cache-dir antithesis==0.3.1

WORKDIR /workload
COPY workload/miniprotocol_observer.py /workload/miniprotocol_observer.py
COPY workload/workload_entrypoint.py /workload/workload_entrypoint.py
COPY --chmod=0755 workload/test/v1/mixed-miniprotocol-security/ /opt/antithesis/test/v1/mixed-miniprotocol-security/

ENV PYTHONPATH=/workload
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/workload/workload_entrypoint.py"]
