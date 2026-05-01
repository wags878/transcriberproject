def test_transcribe_without_auth_returns_401(client, fake_audio) -> None:
    r = client.post(
        "/v1/transcribe",
        files={"audio": ("clip.wav", fake_audio, "audio/wav")},
    )
    assert r.status_code == 401


def test_transcribe_with_bad_token_returns_401(client, fake_audio) -> None:
    r = client.post(
        "/v1/transcribe",
        headers={"Authorization": "Bearer not-the-right-token"},
        files={"audio": ("clip.wav", fake_audio, "audio/wav")},
    )
    assert r.status_code == 401


def test_transcribe_malformed_auth_header_returns_401(client, fake_audio) -> None:
    r = client.post(
        "/v1/transcribe",
        headers={"Authorization": "Token foo"},
        files={"audio": ("clip.wav", fake_audio, "audio/wav")},
    )
    assert r.status_code == 401


def test_admin_storage_requires_auth(client) -> None:
    assert client.get("/v1/admin/storage").status_code == 401


def test_results_endpoint_requires_auth(client) -> None:
    # 401 must come before 404; auth is the gate.
    assert client.get("/v1/results/some-uuid/transcript.txt").status_code == 401
    assert client.get("/v1/results/some-uuid/transcript.json").status_code == 401
