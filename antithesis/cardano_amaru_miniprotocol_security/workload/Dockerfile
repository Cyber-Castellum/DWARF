FROM ghcr.io/j-gainsec/dwarf-kes-workload@sha256:f3b5603f2d880a617c3ef3b9624c589d6af01ad30951f41e807cfd436f167a20

COPY workload/ /workload/
COPY workload/test/v1/mixed-miniprotocol-security/ /opt/antithesis/test/v1/mixed-miniprotocol-security/
RUN chmod 0755 /opt/antithesis/test/v1/mixed-miniprotocol-security/*.py

ENV PYTHONPATH=/workload
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/workload/workload_entrypoint.py"]

