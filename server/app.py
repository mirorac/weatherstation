import asyncio
import glob
import json
import os
import sqlite3
import time
from typing import Any

from quart import Quart, Response, jsonify, request
from quart_cors import cors


def init_db(connection: sqlite3.Connection) -> None:
    cursor = connection.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS measurements (
            timestamp INTEGER NOT NULL,
            code TEXT NOT NULL,
            value TEXT,
            num_value REAL,
            type TEXT,
            dp_id INTEGER,
            custom_name TEXT,
            recorded_at INTEGER,
            tid TEXT,
            PRIMARY KEY (timestamp, code)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_code_timestamp ON measurements (code, timestamp)"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON measurements (timestamp)")
    connection.commit()


def parse_and_insert_raw(connection: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """Insert Tuya shadow properties from one API response into SQLite."""
    if not isinstance(payload, dict) or not payload.get("success", False):
        return 0

    properties = payload.get("result", {}).get("properties", [])
    recorded_at = payload.get("t")
    rows = []
    for property_data in properties:
        code = property_data.get("code")
        if not code:
            continue

        value = property_data.get("value")
        rows.append(
            (
                recorded_at or property_data.get("time"),
                code,
                str(value) if value is not None else None,
                float(value)
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else None,
                property_data.get("type", ""),
                property_data.get("dp_id"),
                property_data.get("custom_name", ""),
                recorded_at,
                payload.get("tid", ""),
            )
        )

    if not rows:
        return 0

    connection.executemany(
        """
        INSERT OR REPLACE INTO measurements (
            timestamp, code, value, num_value, type, dp_id, custom_name, recorded_at, tid
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    return len(rows)


def load_initial_jsonl_data(
    connection: sqlite3.Connection, data_directory: str, app: Quart
) -> None:
    """Load existing JSONL measurements at server startup."""
    files = sorted(glob.glob(os.path.join(data_directory, "*.jsonl")))
    inserted = 0
    for file_path in files:
        try:
            with open(file_path, encoding="utf-8") as data_file:
                for line in data_file:
                    try:
                        inserted += parse_and_insert_raw(connection, json.loads(line))
                    except json.JSONDecodeError:
                        app.logger.warning("Skipping invalid JSON in %s", file_path)
        except OSError as error:
            app.logger.error("Could not load %s: %s", file_path, error)
    app.logger.info("Loaded %d property measurements from %d JSONL file(s).", inserted, len(files))


def create_app(config: dict[str, Any] | None = None) -> Quart:
    app = cors(Quart(__name__), allow_origin="*")
    app.config.update(
        DATA_DIR=os.environ.get("DATA_DIR", "data"),
        DB_PATH=os.environ.get("DB_PATH", ":memory:"),
        INGEST_API_KEY=os.environ.get("INGEST_API_KEY", ""),
    )
    if config:
        app.config.update(config)

    app.extensions["db_connection"] = None
    app.extensions["sse_clients"] = set()

    def connection() -> sqlite3.Connection:
        database = app.extensions["db_connection"]
        if database is None:
            raise RuntimeError("Database is not initialized")
        return database

    @app.before_serving
    async def startup() -> None:
        database = sqlite3.connect(app.config["DB_PATH"], check_same_thread=False)
        database.row_factory = sqlite3.Row
        init_db(database)
        load_initial_jsonl_data(database, app.config["DATA_DIR"], app)
        app.extensions["db_connection"] = database

    @app.after_serving
    async def shutdown() -> None:
        database = app.extensions["db_connection"]
        if database is not None:
            database.close()
            app.extensions["db_connection"] = None

    @app.get("/api/health")
    async def health():
        return jsonify({"status": "ok", "timestamp": int(time.time() * 1000)})

    @app.post("/api/data")
    async def ingest_data():
        expected_api_key = app.config.get("INGEST_API_KEY")
        if expected_api_key:
            auth_header = request.headers.get("Authorization", "")
            api_key_header = request.headers.get("X-API-Key", "")
            bearer_token = (
                auth_header[7:].strip()
                if auth_header.startswith("Bearer ")
                else ""
            )
            provided_key = api_key_header or bearer_token
            if not provided_key or provided_key != expected_api_key:
                return jsonify({"error": "Unauthorized"}), 401

        payload = await request.get_json(silent=True)
        if not payload:
            return jsonify({"error": "Invalid or empty JSON payload"}), 400

        inserted_count = parse_and_insert_raw(connection(), payload)
        event = {"type": "new_measurement", "count": inserted_count, "data": payload}
        for client_queue in list(app.extensions["sse_clients"]):
            await client_queue.put(event)
        return jsonify({"success": True, "inserted_properties": inserted_count})

    @app.get("/api/latest")
    async def get_latest():
        code = request.args.get("code")
        cursor = connection().cursor()
        if code:
            cursor.execute(
                "SELECT * FROM measurements WHERE code = ? ORDER BY timestamp DESC LIMIT 1",
                (code,),
            )
            row = cursor.fetchone()
            result = dict(row) if row else None
        else:
            cursor.execute(
                """
                SELECT measurements.* FROM measurements
                INNER JOIN (
                    SELECT code, MAX(timestamp) AS max_timestamp
                    FROM measurements GROUP BY code
                ) latest ON measurements.code = latest.code
                    AND measurements.timestamp = latest.max_timestamp
                """
            )
            result = {row["code"]: dict(row) for row in cursor.fetchall()}
        return jsonify({"result": result})

    @app.get("/api/measurements")
    async def query_measurements():
        code = request.args.get("code")
        start_time = request.args.get("start_time", type=int)
        end_time = request.args.get("end_time", type=int)
        limit = min(request.args.get("limit", default=500, type=int), 5000)
        order = "ASC" if request.args.get("order", "").lower() == "asc" else "DESC"

        query = "SELECT * FROM measurements WHERE 1=1"
        parameters: list[Any] = []
        if code:
            query += " AND code = ?"
            parameters.append(code)
        if start_time is not None:
            query += " AND timestamp >= ?"
            parameters.append(start_time)
        if end_time is not None:
            query += " AND timestamp <= ?"
            parameters.append(end_time)
        query += f" ORDER BY timestamp {order} LIMIT ?"
        parameters.append(limit)

        rows = connection().execute(query, parameters).fetchall()
        return jsonify({"count": len(rows), "results": [dict(row) for row in rows]})

    @app.get("/api/events")
    async def sse_events():
        client_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        app.extensions["sse_clients"].add(client_queue)

        async def event_generator():
            try:
                yield 'event: connected\ndata: {"status": "connected"}\n\n'
                while True:
                    event = await client_queue.get()
                    yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
            finally:
                app.extensions["sse_clients"].discard(client_queue)

        return Response(
            event_generator(),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()