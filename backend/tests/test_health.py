def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["version"], str) and body["version"]


def test_health_db(client):
    assert client.get("/api/health/db").json() == {"ok": True}
