FROM ghcr.io/intersectmbo/cardano-node@sha256:3275d357053d21f3220f74b0854fd584e1fe322dfa1bbb78effd760c3191d14c AS cardano

FROM python:3.11-slim

COPY --from=cardano /nix/store /nix/store
COPY --from=cardano /usr/local/bin/cardano-cli /usr/local/bin/cardano-cli
RUN pip install --no-cache-dir antithesis==0.3.1

WORKDIR /workload
COPY workload/kes_observer.py /workload/kes_observer.py
COPY workload/kes_observer.py /opt/antithesis/catalog/kes_observer.py
COPY workload/test/v1/mixed-kes-security/ /opt/antithesis/test/v1/mixed-kes-security/
RUN chmod 0755 /opt/antithesis/test/v1/mixed-kes-security/*.py

ENV PYTHONPATH=/workload
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["sleep", "infinity"]
