FROM python:3.11-slim

RUN pip install --no-cache-dir antithesis

WORKDIR /workload
COPY workload/mixed_phase1.py /workload/mixed_phase1.py
COPY workload/mixed_phase1.py /opt/antithesis/catalog/mixed_phase1.py
COPY workload/test/v1/mixed-phase1/ /opt/antithesis/test/v1/mixed-phase1/
COPY fixture/static/ /fixture/
RUN chmod 0755 /opt/antithesis/test/v1/mixed-phase1/*.py

ENV PYTHONPATH=/workload
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["sleep", "infinity"]
