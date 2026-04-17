FROM python:3.11-slim

WORKDIR /app

COPY probe_exporter.py .

ENV PYTHONUNBUFFERED=1

EXPOSE 9102

CMD ["python", "probe_exporter.py"]
