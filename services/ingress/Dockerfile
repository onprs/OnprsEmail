FROM python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31

WORKDIR /app

COPY app.py /app/app.py

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "app.py"]
