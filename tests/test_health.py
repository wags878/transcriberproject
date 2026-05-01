def test_health_returns_ok(client) -> None:
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["device"] in {"cpu", "cuda"}
    assert body["compute_type"]
    assert isinstance(body["gpu"], bool)


def test_health_does_not_require_auth(client) -> None:
    r = client.get("/v1/health")
    assert r.status_code == 200
