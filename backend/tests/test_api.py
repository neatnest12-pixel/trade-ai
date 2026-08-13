from fastapi.testclient import TestClient

from app.main import app


def test_health_and_prediction():
    with TestClient(app) as client:
        assert client.get("/health").status_code==200
        r=client.get("/prediction/EURUSD/5m");assert r.status_code==200;assert "probability_up" in r.json()
def test_invalid_timeframe_structured():
    with TestClient(app) as client:
        r=client.get("/market/EURUSD/bad");assert r.status_code==422;assert "error" in r.json()

