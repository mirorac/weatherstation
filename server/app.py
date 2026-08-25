from __future__ import annotations

import asyncio
from dataclasses import dataclass
import glob
import json
import os
import sqlite3
import time
from typing import Any, Callable

from quart import Quart, Response, jsonify, request
from quart_cors import cors


@dataclass
class RawProperty:
    code: str
    value: Any
    type: str
    dp_id: int | None
    custom_name: str
    time: int | None
    recorded_at: int | None
    tid: str


@dataclass
class TransformedMeasurement:
    timestamp: int
    code: str
    value: str | None
    num_value: float | None
    type: str
    recorded_at: int | None


WIND_DIRECTION_DEGREES: dict[str, float] = {
    "N": 0.0,
    "NNE": 22.5,
    "NE": 45.0,
    "ENE": 67.5,
    "E": 90.0,
    "ESE": 112.5,
    "SE": 135.0,
    "SSE": 157.5,
    "S": 180.0,
    "SSW": 202.5,
    "SW": 225.0,
    "WSW": 247.5,
    "W": 270.0,
    "WNW": 292.5,
    "NW": 315.0,
    "NNW": 337.5,
}


def _scaled_decimal_transformer(
    target_code: str, scale: float = 10.0
) -> Callable[[RawProperty], TransformedMeasurement]:
    def transformer(prop: RawProperty) -> TransformedMeasurement:
        raw_val = prop.value
        num_val = (
            round(float(raw_val) / scale, 2)
            if isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool)
            else None
        )
        val_str = str(num_val) if num_val is not None else (str(raw_val) if raw_val is not None else None)
        timestamp = prop.recorded_at or prop.time or int(time.time() * 1000)
        return TransformedMeasurement(
            timestamp=timestamp,
            code=target_code,
            value=val_str,
            num_value=num_val,
            type=prop.type,
            recorded_at=prop.recorded_at,
        )

    return transformer


def _direct_numeric_transformer(
    target_code: str,
) -> Callable[[RawProperty], TransformedMeasurement]:
    def transformer(prop: RawProperty) -> TransformedMeasurement:
        raw_val = prop.value
        num_val = (
            float(raw_val)
            if isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool)
            else None
        )
        val_str = str(raw_val) if raw_val is not None else None
        timestamp = prop.recorded_at or prop.time or int(time.time() * 1000)
        return TransformedMeasurement(
            timestamp=timestamp,
            code=target_code,
            value=val_str,
            num_value=num_val,
            type=prop.type,
            recorded_at=prop.recorded_at,
        )

    return transformer


def transform_wind_direction(prop: RawProperty) -> TransformedMeasurement:
    val_str = str(prop.value) if prop.value is not None else None
    degrees = WIND_DIRECTION_DEGREES.get(val_str.upper() if val_str else "", None)
    timestamp = prop.recorded_at or prop.time or int(time.time() * 1000)
    return TransformedMeasurement(
        timestamp=timestamp,
        code="wind_direction",
        value=val_str,
        num_value=degrees,
        type=prop.type,
        recorded_at=prop.recorded_at,
    )


def transform_comfort_level(prop: RawProperty) -> TransformedMeasurement:
    val_str = str(prop.value) if prop.value is not None else None
    timestamp = prop.recorded_at or prop.time or int(time.time() * 1000)
    return TransformedMeasurement(
        timestamp=timestamp,
        code="comfort_level",
        value=val_str,
        num_value=None,
        type=prop.type,
        recorded_at=prop.recorded_at,
    )


def default_transformer(prop: RawProperty) -> TransformedMeasurement:
    raw_val = prop.value
    num_val = (
        float(raw_val)
        if isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool)
        else None
    )
    val_str = str(raw_val) if raw_val is not None else None
    timestamp = prop.recorded_at or prop.time or int(time.time() * 1000)
    return TransformedMeasurement(
        timestamp=timestamp,
        code=prop.code,
        value=val_str,
        num_value=num_val,
        type=prop.type,
        recorded_at=prop.recorded_at,
    )


PROPERTY_TRANSFORMERS: dict[str, Callable[[RawProperty], TransformedMeasurement | None]] = {
    "wd": transform_wind_direction,
    "temp_current": _scaled_decimal_transformer("temp_current"),
    "intemp": _scaled_decimal_transformer("indoor_temperature"),
    "ch1temp": _scaled_decimal_transformer("outdoor_temperature"),
    "inhum": _scaled_decimal_transformer("indoor_humidity"),
    "ch1hum": _scaled_decimal_transformer("outdoor_humidity"),
    "pressure": _scaled_decimal_transformer("pressure"),
    "windspeed": _direct_numeric_transformer("wind_speed"),
    "gustwind": _direct_numeric_transformer("gust_wind"),
    "rain_1h": _direct_numeric_transformer("rain_1h"),
    "rain_24h": _direct_numeric_transformer("rain_24h"),
    "rain": _direct_numeric_transformer("rain"),
    "com": transform_comfort_level,
    # Ignore raw/diagnostic payloads
    "alarm": lambda _: None,
    "alert": lambda _: None,
    "unit": lambda _: None,
    "battery": lambda _: None,
    "basic": lambda _: None,
    "alertup": lambda _: None,
    "datadisplay": lambda _: None,
}


def apply_property_transformer(prop: RawProperty) -> TransformedMeasurement | None:
    transformer = PROPERTY_TRANSFORMERS.get(prop.code, default_transformer)
    return transformer(prop)


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
            recorded_at INTEGER,
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
    tid = str(payload.get("tid", ""))
    rows = []
    for property_data in properties:
        raw_code = property_data.get("code")
        if not raw_code:
            continue

        raw_prop = RawProperty(
            code=raw_code,
            value=property_data.get("value"),
            type=property_data.get("type", ""),
            dp_id=property_data.get("dp_id"),
            custom_name=property_data.get("custom_name", ""),
            time=property_data.get("time"),
            recorded_at=recorded_at,
            tid=tid,
        )

        transformed = apply_property_transformer(raw_prop)
        if transformed is None:
            continue

        rows.append(
            (
                transformed.timestamp,
                transformed.code,
                transformed.value,
                transformed.num_value,
                transformed.type,
                transformed.recorded_at,
            )
        )

    if not rows:
        return 0

    connection.executemany(
        """
        INSERT OR REPLACE INTO measurements (
            timestamp, code, value, num_value, type, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?)
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
                for line_number, line in enumerate(data_file, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        inserted += parse_and_insert_raw(connection, json.loads(line))
                    except json.JSONDecodeError:
                        app.logger.warning(
                            "Skipping invalid JSON in %s:%d", file_path, line_number
                        )
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
                "SELECT timestamp, value, num_value, code FROM measurements WHERE code = ? ORDER BY timestamp DESC LIMIT 1",
                (code,),
            )
            row = cursor.fetchone()
            result = dict(row) if row else None
        else:
            cursor.execute(
                """
                SELECT measurements.timestamp, measurements.value, measurements.num_value, measurements.code
                FROM measurements
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

        query = "SELECT timestamp, value, num_value, code FROM measurements WHERE 1=1"
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
                    try:
                        event = await asyncio.wait_for(client_queue.get(), timeout=30)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                    else:
                        yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
            finally:
                app.extensions["sse_clients"].discard(client_queue)

        response = Response(
            event_generator(),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

        response.timeout = None  # Disable timeout for SSE connections

        return response

    return app


app = create_app()