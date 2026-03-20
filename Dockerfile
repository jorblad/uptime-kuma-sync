FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sync_ingress_to_uptime_kuma.py .

ENTRYPOINT ["python", "/app/sync_ingress_to_uptime_kuma.py"]
