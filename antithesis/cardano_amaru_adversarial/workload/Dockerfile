<<<<<<< HEAD
FROM python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7

RUN pip install --no-cache-dir antithesis==0.2.0
=======
FROM python:3.11-slim

RUN pip install --no-cache-dir antithesis
>>>>>>> 6082f0eedabe478801051a661435a5ffb3424f47

WORKDIR /workload
COPY workload/mixed_phase1.py /workload/mixed_phase1.py
COPY workload/mixed_phase1.py /opt/antithesis/catalog/mixed_phase1.py
COPY workload/test/v1/mixed-phase1/ /opt/antithesis/test/v1/mixed-phase1/
COPY fixture/static/ /fixture/
RUN chmod 0755 /opt/antithesis/test/v1/mixed-phase1/*.py

ENV PYTHONPATH=/workload
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["sleep", "infinity"]
