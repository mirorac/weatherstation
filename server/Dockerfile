FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV PORT=5000
ENV DATA_DIR=/app/data
ENV DB_PATH=/app/data/weather.db

EXPOSE 5000

CMD ["hypercorn", "--bind", "0.0.0.0:5000", "app:app"]

FROM python:3.11-slim AS test

WORKDIR /app
ENV PYTHONPATH=/app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY app.py .
COPY tests ./tests

CMD ["pytest", "-q"]