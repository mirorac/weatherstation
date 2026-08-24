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
    assert (await response.get_json())["result"]["num_value"] == 215.0


@pytest.mark.asyncio
async def test_ingestion_updates_latest_and_historical_query(client):
    response = await client.post("/api/data", json=payload(2000, 230))
    assert response.status_code == 200
    assert (await response.get_json())["inserted_properties"] == 2

    latest = await (await client.get("/api/latest?code=temp_current")).get_json()
    assert latest["result"]["timestamp"] == 2000
    assert latest["result"]["num_value"] == 230.0

    history = await (
        await client.get("/api/measurements?code=temp_current&order=asc")
    ).get_json()
    assert history["count"] == 2
    assert [reading["timestamp"] for reading in history["results"]] == [1000, 2000]


@pytest.mark.asyncio
async def test_ingestion_preserves_polls_with_unchanged_device_timestamps(client):
    response = await client.post("/api/data", json=payload(2000, property_timestamp=1000))
    assert response.status_code == 200

    history = await (
        await client.get("/api/measurements?code=temp_current&order=asc")
    ).get_json()
    assert history["count"] == 2
    assert [reading["timestamp"] for reading in history["results"]] == [1000, 2000]


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
