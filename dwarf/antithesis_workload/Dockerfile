FROM python:3.12-slim
WORKDIR /workload
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY workload.py .
# setup-complete.sh is provided by the bundle at /antithesis/setup-complete.sh
ENTRYPOINT ["python3", "/workload/workload.py"]
