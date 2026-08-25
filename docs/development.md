# Development Guide

This guide covers local development workflows, running the API server, scraping scripts, and executing tests both locally and via Docker.

---

## 1. Prerequisites & Tooling

- **Python 3.11+** (recommended for local development to match the container image)
- **Docker** and **Docker Compose**
- **curl**, **jq**, and **openssl** (required if running scraper scripts locally)

---

## 2. Local Python Environment Setup

Create and activate a virtual environment, then install both runtime and development dependencies:

```bash
# Create a virtual environment
python3 -m venv .venv

# Activate the virtual environment
# macOS / Linux:
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

# Install server dependencies
pip install -r server/requirements-dev.txt
```

---

## 3. Running the Server Locally

Run the Quart application locally using Hypercorn or Python:

```bash
# Set environment variables (optional defaults shown)
export DATA_DIR=data
export DB_PATH=data/weather.db
# export INGEST_API_KEY=your_secret_key

# Run using hypercorn from the server directory or with PYTHONPATH set
PYTHONPATH=server hypercorn --bind 127.0.0.1:5000 --reload app:app
```

The server will be accessible at `http://localhost:5000`.

---

## 4. Running Tests

### Option A: Using Docker (Recommended)

Run the test suite inside the Python 3.11 container test stage:

```bash
docker build --target test -t weatherstation-server-test ./server
docker run --rm weatherstation-server-test
```

### Option B: Using Local Virtual Environment

```bash
# Ensure your virtual environment is active and dev dependencies are installed
PYTHONPATH=server pytest server/tests
```

Or pass flags for detailed output:

```bash
PYTHONPATH=server pytest -v server/tests
```

---

## 5. Running the Scraper Scripts Locally

The scraper scripts push scraped data to the server and write `.jsonl` files to `data/`.

1. Copy `.env.example` to `.env` and fill in credentials:

   ```bash
   cp .env.example .env
   ```

2. Export environment variables or source `.env`:

   ```bash
   set -a
   source .env
   set +a
   ```

3. Run individual scraping runs or continuous polling:

   ```bash
   # Single scrape run
   ./scraper/scrape_data.sh

   # Scrape for 24 hours at intervals
   ./scraper/scrape_data_for_24h.sh

   # Continuous polling
   ./scraper/scrape_data_forever.sh
   ```

---

## 6. Running with Docker Compose for Local Integration

You can run the full stack (API + Scraper) locally using Docker Compose:

```bash
# Build and start services
docker compose up --build

# View logs
docker compose logs -f

# Shut down services
docker compose down
```

---

## 7. Code Conventions & Entity Format

- **Response Entity Shape**: Entity measurements exposed by `/api/latest` and `/api/measurements` are kept lightweight:
  ```json
  {
    "timestamp": 1787511666584,
    "value": "0",
    "num_value": 0.0,
    "code": "windspeed"
  }
  ```
- **Type Annotations**: Use `from __future__ import annotations` at the top of Python modules when using modern union types (`|`) across different Python versions.
