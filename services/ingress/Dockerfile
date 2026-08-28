FROM python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31

WORKDIR /app

COPY app.py /app/app.py

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c 'import json; from urllib.request import urlopen; response = urlopen("http://127.0.0.1:8080/health", timeout=3); payload = json.load(response); raise SystemExit(0 if response.status == 200 and payload.get("status") == "healthy" else 1)'

CMD ["python", "app.py"]
