# DWARF adversarial differential oracle — tails the two Amaru relays' logs and
# emits Antithesis SDK properties. Kept tiny; the antithesis SDK is optional so
# the same image is runnable locally (falls back to stdout).
FROM python:3.12-slim
RUN pip install --no-cache-dir antithesis || true
COPY oracle.py /oracle.py
ENTRYPOINT ["python", "/oracle.py"]
