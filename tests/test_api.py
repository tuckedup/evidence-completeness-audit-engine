from fastapi.testclient import TestClient

from ecsae.api.main import app


def test_api_cache_and_metadata() -> None:
    payload = {"study_id": "API-1", "randomized_n": 20, "arm_ns": {"a": 10, "b": 10}}
    with TestClient(app) as client:
        first = client.post("/audit", json=payload)
        second = client.post("/audit", json=payload)
        assert first.status_code == 200
        assert float(first.headers["x-ecsae-server-ms"]) >= 0
        assert first.headers["x-ecsae-cache"] == "miss"
        assert second.headers["x-ecsae-cache"] == "hit"
        assert first.json() == second.json()
        validated = client.post("/audit", json=payload, headers={"If-None-Match": first.headers["etag"]})
        assert validated.status_code == 304
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/ready").json() == {"status": "ready", "transformer": "offline"}
        assert len(client.get("/rules").json()) >= 10
        assert client.get("/version").json()["rules"] == "1.2.0"
        assert client.get("/version").json()["transformer"] == "offline"
        assert len(client.post("/audit/batch", json={"records": [payload, payload]}).json()) == 2
        extracted = client.post("/extract", json={"text": "Randomized N=20. Arm a: n=10; arm b: n=10."})
        assert extracted.json()["randomized_n"] == 20
        unavailable = client.post("/extract/transformer", json={"text": "Randomized N=20."})
        assert unavailable.status_code == 503
        assert client.post("/audit", json={"randomized_n": 20, "reporting": {"harm": True}}).status_code == 422
