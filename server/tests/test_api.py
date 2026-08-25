from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app import create_app


def payload(
    timestamp: int, temperature: int = 215, property_timestamp: int | None = None
) -> dict:
    return {
        "success": True,
        "t": timestamp,
        "tid": "test-reading",
        "result": {
            "properties": [
                {
                    "code": "temp_current",
                    "dp_id": 1,
                    "time": property_timestamp or timestamp,
                    "type": "value",
                    "value": temperature,
                },
                {
                    "code": "wd",
                    "dp_id": 112,
                    "time": property_timestamp or timestamp,
                    "type": "enum",
                    "value": "N",
                },
            ]
        },
    }


@pytest_asyncio.fixture
async def client(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "readings.jsonl").write_text(json.dumps(payload(1000)) + "\n")
    app = create_app({"TESTING": True, "DATA_DIR": str(data_dir), "DB_PATH": ":memory:"})
    async with app.test_app():
        async with app.test_client() as test_client:
            yield test_client


@pytest.mark.asyncio
async def test_startup_loads_jsonl_and_returns_latest(client):
    response = await client.get("/api/latest?code=temp_current")
    assert response.status_code == 200
    data = (await response.get_json())["result"]
    assert data["num_value"] == 21.5
    assert data["value"] == "21.5"
    assert set(data.keys()) == {"timestamp", "value", "num_value", "code"}

    # Check wind_direction transformed from 'wd'
    wd_resp = await client.get("/api/latest?code=wind_direction")
    assert wd_resp.status_code == 200
    wd_data = (await wd_resp.get_json())["result"]
    assert wd_data["num_value"] == 0.0
    assert wd_data["value"] == "N"
    assert wd_data["code"] == "wind_direction"


@pytest.mark.asyncio
async def test_ingestion_updates_latest_and_historical_query(client):
    response = await client.post("/api/data", json=payload(2000, 230))
    assert response.status_code == 200
    assert (await response.get_json())["inserted_properties"] == 2

    latest = await (await client.get("/api/latest?code=temp_current")).get_json()
    assert latest["result"]["timestamp"] == 2000
    assert latest["result"]["num_value"] == 23.0
    assert set(latest["result"].keys()) == {"timestamp", "value", "num_value", "code"}

    history = await (
        await client.get("/api/measurements?code=temp_current&order=asc")
    ).get_json()
    assert history["count"] == 2
    assert [reading["timestamp"] for reading in history["results"]["temp_current"]] == [1000, 2000]
    assert set(history["results"]["temp_current"][0].keys()) == {
        "timestamp", "value", "num_value", "code", "recorded_at"
    }


@pytest.mark.asyncio
async def test_historical_query_supports_multiple_codes_and_groups_results(client):
    await client.post("/api/data", json=payload(2000, 230))
    await client.post("/api/data", json=payload(3000, 240))

    # Test multi-code query and per-code limit
    history = await (
        await client.get("/api/measurements?code=temp_current,wind_direction&limit=2&order=asc")
    ).get_json()

    assert history["count"] == 4
    assert set(history["results"]) == {"temp_current", "wind_direction"}
    assert len(history["results"]["temp_current"]) == 2
    assert len(history["results"]["wind_direction"]) == 2
    assert [r["timestamp"] for r in history["results"]["temp_current"]] == [1000, 2000]
    assert [r["timestamp"] for r in history["results"]["wind_direction"]] == [1000, 2000]
    assert history["results"]["temp_current"][0]["recorded_at"] == 1000
    assert history["results"]["wind_direction"][0]["recorded_at"] == 1000


@pytest.mark.asyncio
async def test_historical_query_supports_matrix_format(client):
    await client.post("/api/data", json=payload(2000, 230))

    resp = await client.get("/api/measurements?code=temp_current,wind_direction&format=matrix&order=asc")
    assert resp.status_code == 200
    data = await resp.get_json()

    assert data["count"] == 4
    assert set(data["results"].keys()) == {"temp_current", "wind_direction"}
    assert data["results"]["temp_current"]["columns"] == ["num_value", "recorded_at", "timestamp", "value"]
    assert data["results"]["wind_direction"]["columns"] == ["num_value", "recorded_at", "timestamp", "value"]

    assert data["results"]["temp_current"]["data"] == [
        [21.5, 1000, 1000, "21.5"],
        [23.0, 2000, 2000, "23.0"],
    ]
    assert data["results"]["wind_direction"]["data"] == [
        [0.0, 1000, 1000, "N"],
        [0.0, 2000, 2000, "N"],
    ]


@pytest.mark.asyncio
async def test_custom_transformers_for_all_properties(client):
    full_payload = {
        "success": True,
        "t": 3000,
        "tid": "full-test-reading",
        "result": {
            "properties": [
                {"code": "temp_current", "value": -200, "type": "value", "dp_id": 1},
                {"code": "intemp", "value": 248, "type": "value", "dp_id": 101},
                {"code": "inhum", "value": 500, "type": "value", "dp_id": 102},
                {"code": "ch1temp", "value": 220, "type": "value", "dp_id": 103},
                {"code": "ch1hum", "value": 540, "type": "value", "dp_id": 104},
                {"code": "pressure", "value": 9980, "type": "value", "dp_id": 109},
                {"code": "windspeed", "value": 2, "type": "value", "dp_id": 110},
                {"code": "gustwind", "value": 3, "type": "value", "dp_id": 111},
                {"code": "wd", "value": "NW", "type": "enum", "dp_id": 112},
                {"code": "rain_1h", "value": 0, "type": "value", "dp_id": 113},
                {"code": "rain_24h", "value": 0, "type": "value", "dp_id": 114},
                {"code": "rain", "value": 0, "type": "value", "dp_id": 134},
                {"code": "com", "value": "comfortable", "type": "enum", "dp_id": 126},
                {"code": "alarm", "value": "AX8AAAYAAn8AAAgA", "type": "raw", "dp_id": 127},
            ]
        },
    }
    response = await client.post("/api/data", json=full_payload)
    assert response.status_code == 200
    # 13 valid metrics inserted (alarm is filtered out)
    assert (await response.get_json())["inserted_properties"] == 13

    # Check wind_direction NW -> 315.0
    wd = (await (await client.get("/api/latest?code=wind_direction")).get_json())["result"]
    assert wd["code"] == "wind_direction"
    assert wd["value"] == "NW"
    assert wd["num_value"] == 315.0

    # Check outdoor and indoor temperatures
    outdoor_temp = (await (await client.get("/api/latest?code=outdoor_temperature")).get_json())["result"]
    assert outdoor_temp["num_value"] == 22.0

    indoor_temp = (await (await client.get("/api/latest?code=indoor_temperature")).get_json())["result"]
    assert indoor_temp["num_value"] == 24.8

    # Check humidity
    indoor_hum = (await (await client.get("/api/latest?code=indoor_humidity")).get_json())["result"]
    assert indoor_hum["num_value"] == 50.0

    outdoor_hum = (await (await client.get("/api/latest?code=outdoor_humidity")).get_json())["result"]
    assert outdoor_hum["num_value"] == 54.0

    # Check pressure
    press = (await (await client.get("/api/latest?code=pressure")).get_json())["result"]
    assert press["num_value"] == 998.0

    # Check wind_speed
    ws = (await (await client.get("/api/latest?code=wind_speed")).get_json())["result"]
    assert ws["num_value"] == 2.0


@pytest.mark.asyncio
async def test_ingestion_preserves_polls_with_unchanged_device_timestamps(client):
    response = await client.post("/api/data", json=payload(2000, property_timestamp=1000))
    assert response.status_code == 200

    history = await (
        await client.get("/api/measurements?code=temp_current&order=asc")
    ).get_json()
    assert history["count"] == 2
    assert [reading["timestamp"] for reading in history["results"]["temp_current"]] == [1000, 2000]


@pytest.mark.asyncio
async def test_ingestion_rejects_invalid_json(client):
    response = await client.post(
        "/api/data", data="not-json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert (await response.get_json())["error"] == "Invalid or empty JSON payload"


@pytest.mark.asyncio
async def test_ingestion_with_api_key_auth(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    app = create_app(
        {
            "TESTING": True,
            "DATA_DIR": str(data_dir),
            "DB_PATH": ":memory:",
            "INGEST_API_KEY": "secret-ingest-key",
        }
    )
    async with app.test_app():
        async with app.test_client() as test_client:
            # Missing key -> 401
            unauthorized_resp = await test_client.post("/api/data", json=payload(1000))
            assert unauthorized_resp.status_code == 401
            assert (await unauthorized_resp.get_json())["error"] == "Unauthorized"

            # Wrong key -> 401
            wrong_resp = await test_client.post(
                "/api/data",
                json=payload(1000),
                headers={"X-API-Key": "wrong-key"},
            )
            assert wrong_resp.status_code == 401

            # Valid X-API-Key header -> 200
            ok_resp = await test_client.post(
                "/api/data",
                json=payload(1000),
                headers={"X-API-Key": "secret-ingest-key"},
            )
            assert ok_resp.status_code == 200
            assert (await ok_resp.get_json())["success"] is True

            # Valid Bearer token -> 200
            bearer_resp = await test_client.post(
                "/api/data",
                json=payload(2000),
                headers={"Authorization": "Bearer secret-ingest-key"},
            )
            assert bearer_resp.status_code == 200
            assert (await bearer_resp.get_json())["success"] is True
