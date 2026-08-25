FROM python:3.11-slim AS runtime

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

ENV PORT=5000
ENV DATA_DIR=/app/data
ENV DB_PATH=/app/data/weather.db
ENV TIMEOUT_SECONDS=3600
ENV KEEP_ALIVE_SECONDS=3600

EXPOSE 5000

CMD ["sh", "-c", "exec hypercorn app:app --bind 0.0.0.0:${PORT:-5000} --worker-class uvloop --workers 1 --read-timeout ${TIMEOUT_SECONDS:-3600} --keep-alive ${KEEP_ALIVE_SECONDS:-3600}"]

FROM python:3.11-slim AS test

WORKDIR /app
ENV PYTHONPATH=/app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY app.py .
COPY tests ./tests

CMD ["pytest", "-q"]