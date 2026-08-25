# Deployment Guide

This guide covers deploying the Weather Station application (Scraper and Quart API Server) with Docker Compose.

---

## 1. Prerequisites

- **Docker** (v20.10+) and **Docker Compose** (v2.0+)
- Tuya Cloud Developer credentials:
  - `CLIENT_ID`
  - `CLIENT_SECRET`
  - `DEVICE_ID`
  - `BASE_URL` (e.g., `https://openapi.tuyaeu.com`)

---

## 2. Project Architecture

```
weatherstation/
├── data/                      # Persisted on host; contains .jsonl and weather.db
├── docs/
│   └── deployment.md          # Deployment documentation
├── scraper/                   # Scraper service
│   ├── Dockerfile
│   ├── scrape_data.sh         # Main polling and API push script
│   ├── scrape_data_for_24h.sh # Local 24h helper script
│   └── scrape_data_forever.sh # Local continuous helper script
├── server/                    # API server service
│   ├── Dockerfile
│   ├── app.py                 # Quart API server + SQLite + SSE
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── tests/
│       └── test_api.py
├── docker-compose.yml
├── .env.example
└── .env                       # Secrets (ignored by Git)
```

---

## 3. Environment Configuration

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your Tuya credentials:
   ```ini
   CLIENT_ID=your_actual_client_id
   CLIENT_SECRET=your_actual_client_secret
   BASE_URL=https://openapi.tuyaeu.com
   DEVICE_ID=bf637c44012ca3c7979ia7
   INTERVAL=300
   # Optional: HTTP request and idle keep-alive timeout in seconds (default: 3600)
   TIMEOUT_SECONDS=3600
   KEEP_ALIVE_SECONDS=3600
   # Optional: set secret API key to protect POST /api/data from unauthorized callers
   INGEST_API_KEY=your_ingest_secret_key
   ```

---

## 4. Running the Stack

### Start all services in the background:

```bash
docker compose up -d --build
```

Docker Compose starts:

1. **`api`**: Boots the Quart server on port `5000`, initializes/loads SQLite from [data/](data/), and exposes health check endpoints.
2. **`scraper`**: Starts after `api` reports healthy, scrapes Tuya API at the configured interval, writes JSONL logs to [data/](data/), and pushes new readings to `http://api:5000/api/data`.

### Check container status:

```bash
docker compose ps
```

### View logs:

```bash
# View all logs
docker compose logs -f

# Follow API server logs
docker compose logs -f api

# Follow Scraper logs
docker compose logs -f scraper
```

### Stop the services:

```bash
docker compose down
```

### SSE Connection Lifetime

The API runs Hypercorn with uvloop and one-hour `read-timeout` and `keep-alive` defaults. These can be adjusted with `TIMEOUT_SECONDS` and `KEEP_ALIVE_SECONDS` in `.env` or the deployment environment.

`GET /api/events` explicitly disables Quart's response timeout and sends an SSE comment heartbeat every 30 seconds when no measurements arrive. This allows an active SSE stream to remain connected beyond one hour, subject to the timeout configuration of any reverse proxy, load balancer, CDN, or client. Configure those intermediary idle timeouts above 30 seconds as well; a truly permanent connection is never guaranteed because clients and network infrastructure can disconnect at any time.

---

## 5. API Endpoints

Once running, the API is available at `http://localhost:5000`:

| Method | Endpoint                        | Description                                                                                         |
| ------ | ------------------------------- | --------------------------------------------------------------------------------------------------- |
| `GET`  | `/api/health`                   | Service health status                                                                               |
| `GET`  | `/api/latest`                   | Latest readings for all metrics                                                                     |
| `GET`  | `/api/latest?code=temp_current` | Latest reading for a specific metric code                                                           |
| `GET`  | `/api/measurements`             | Query historical data (filters: `code`, `start_time`, `end_time`, `limit`, `order`)                 |
| `GET`  | `/api/events`                   | Server-Sent Events (SSE) stream for live updates                                                    |
| `POST` | `/api/data`                     | Ingest new measurement payload (requires `X-API-Key` or `Bearer` if `INGEST_API_KEY` is configured) |

---

## 6. Running Server Tests

To run the test suite in an isolated Docker container:

```bash
docker build --target test -t weatherstation-server-test ./server
docker run --rm weatherstation-server-test
```
