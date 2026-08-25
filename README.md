# Weather Station

A self-hosted service for scraping, persisting, and querying smart weather station metrics via the Tuya Cloud API.

The project consists of:

- **Scraper Service**: Periodically fetches weather station device shadow properties from the Tuya Cloud OpenAPI, writes daily `.jsonl` audit logs, and pushes new readings to the API server.
- **API Server**: An asynchronous Quart (Python) application that indexes incoming measurements into SQLite, provides REST query endpoints, and broadcasts real-time readings via Server-Sent Events (SSE).

---

## Features

- **Automated Data Ingestion**: Authenticates with Tuya Cloud OpenAPI using HMAC-SHA256 signatures and scrapes device state at configurable intervals.
- **Persistent Storage**:
  - Daily raw backup logs formatted as newline-delimited JSON (`.jsonl`).
  - Indexed SQLite database (`weather.db`) with fast timestamp and code lookups.
- **RESTful API**:
  - `GET /api/latest`: Latest readings across all data points or for a specific metric.
  - `GET /api/measurements`: Historical query support with time-range filtering, sorting, and pagination.
  - `POST /api/data`: Endpoint for ingesting raw Tuya shadow property payloads.
  - `GET /api/health`: Health status endpoint for container orchestration.
- **Real-Time Streaming**: `GET /api/events` streams new measurements to clients via Server-Sent Events (SSE).
- **Containerized**: Fully orchestrated with Docker and Docker Compose.

---

## Project Structure

```
weatherstation/
├── data/                      # Persisted data on host (.jsonl files and weather.db)
├── docs/
│   ├── deployment.md          # Detailed deployment guide
│   └── development.md         # Local development and testing guide
├── scraper/                   # Scraper service (Bash + cURL + jq)
│   ├── Dockerfile
│   ├── scrape_data.sh         # Core polling, logging, and API push script
│   ├── scrape_data_for_24h.sh # Helper script for a 24-hour run
│   └── scrape_data_forever.sh # Helper script for running scraper locally in a loop
├── server/                    # Backend service (Quart + SQLite)
│   ├── app.py                 # Quart application and database logic
│   ├── Dockerfile
│   ├── requirements.txt       # Production dependencies
│   ├── requirements-dev.txt   # Development / testing dependencies
│   └── tests/
│       └── test_api.py        # Async API test suite
├── docker-compose.yml         # Stack orchestration
├── .env.example               # Example environment configuration
└── README.md
```

---

## Quick Start

### 1. Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (v20.10+) and [Docker Compose](https://docs.docker.com/compose/) (v2.0+)
- Tuya Developer Platform account and credentials:
  - `CLIENT_ID`
  - `CLIENT_SECRET`
  - `DEVICE_ID`
  - `BASE_URL` (e.g. `https://openapi.tuyaeu.com` for EU, `https://openapi.tuyaus.com` for US)

### 2. Configure Environment

Copy the example environment file and fill in your Tuya credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```ini
CLIENT_ID=your_tuya_client_id
CLIENT_SECRET=your_tuya_client_secret
BASE_URL=https://openapi.tuyaeu.com
DEVICE_ID=your_device_id
INTERVAL=300
```

### 3. Run with Docker Compose

Start the application stack in the background:

```bash
docker compose up -d --build
```

- API Server will be available at `http://localhost:5000`
- Scraper will wait for the API server health check and begin polling Tuya Cloud.

To stop the stack:

```bash
docker compose down
```

See [docs/deployment.md](docs/deployment.md) for detailed deployment options and troubleshooting.

---

## API Reference

### Health Check

```http
GET /api/health
```

**Response (`200 OK`):**

```json
{
  "status": "ok",
  "timestamp": 1756024800000
}
```

---

### Ingest Data

```http
POST /api/data
Content-Type: application/json
```

Accepts raw Tuya shadow property payload and broadcasts new measurements over SSE.

**Response (`200 OK`):**

```json
{
  "success": true,
  "inserted_properties": 8
}
```

---

### Get Latest Measurements

```http
GET /api/latest
GET /api/latest?code=temp_current
```

- **Query Parameters:**
  - `code` _(optional)_: Filter by metric code (e.g. `temp_current`, `humidity_value`, `wd`).

**Response (`200 OK` - single code):**

```json
{
  "result": {
    "timestamp": 1756024800000,
    "value": "215",
    "num_value": 21.5,
    "code": "temp_current"
  }
}
```

---

### Query Historical Measurements

```http
GET /api/measurements?code=temp_current,wind_direction&limit=100&order=desc
```

- **Query Parameters:**
  - `code` _(optional)_: Filter by one or more comma-separated property codes.
  - `start_time` _(optional)_: Start timestamp (epoch ms inclusive).
  - `end_time` _(optional)_: End timestamp (epoch ms inclusive).
  - `limit` _(optional)_: Max results to return (default: `500`, max: `5000`).
  - `order` _(optional)_: `asc` or `desc` (default: `desc`).

**Response (`200 OK`):**

```json
{
  "count": 1,
  "results": {
    "temp_current": [
      {
      "timestamp": 1756024800000,
      "value": "215",
      "num_value": 21.5,
      "code": "temp_current",
      "recorded_at": 1756024800000
      }
    ]
  }
}
```

---

### Real-Time Event Stream (SSE)

```http
GET /api/events
Accept: text/event-stream
```

Streams real-time updates when new data is ingested.

---

## Local Development & Testing

### Running Tests

1. Navigate to the `server/` directory and install development dependencies:

   ```bash
   cd server
   pip install -r requirements.txt -r requirements-dev.txt
   ```

2. Run `pytest`:

   ```bash
   pytest
   ```
