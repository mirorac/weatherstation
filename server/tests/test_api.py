import json

import pytest
import pytest_asyncio

from app import create_app


def payload(timestamp: int, temperature: int = 215) -> dict:
    return {
        "success": True,
        "t": timestamp,
        "tid": "test-reading",
        "result": {
            "properties": [
                {
                    "code": "temp_current",
                    "dp_id": 1,
                    "time": timestamp,
                    "type": "value",
                    "value": temperature,
                },
                {
                    "code": "wd",
                    "dp_id": 112,
                    "time": timestamp,
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
async def test_ingestion_rejects_invalid_json(client):
    response = await client.post(
        "/api/data", data="not-json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert (await response.get_json())["error"] == "Invalid or empty JSON payload"