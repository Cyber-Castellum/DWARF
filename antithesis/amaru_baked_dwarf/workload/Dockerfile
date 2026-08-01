# DWARF submit-api fuzz workload. Dials Amaru's HTTP submit-api, POSTs mutated
# transaction CBOR, and reads the real accept(200)/reject(400)/crash response,
# emitting Antithesis SDK assertions (never-panic, stays-alive, sometimes-reject,
# and — with >1 target — implementations-agree).
FROM python:3.11-slim

# Antithesis SDK is optional at import (workload.py no-ops without it); install it
# so assertions register inside the sim.
RUN pip install --no-cache-dir antithesis || true

WORKDIR /workload
COPY workload.py /workload/workload.py
COPY driver.py /workload/driver.py
COPY corpus /workload/corpus
ENV WORKLOAD_CORPUS=/workload/corpus
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "/workload/driver.py"]
