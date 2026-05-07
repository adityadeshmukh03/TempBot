from fastapi.testclient import TestClient

from server import app


def test_gui_health_and_status_endpoints():
    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True

    status = client.get("/api/status")
    assert status.status_code == 200
    payload = status.json()
    assert "process" in payload
    assert "config" in payload


def test_live_start_requires_confirmation():
    client = TestClient(app)
    response = client.post("/api/bot/start", json={"live_mode": True, "confirm": ""})
    assert response.status_code == 400
