import json


def test_transcribe_happy_path(client, auth_headers, fake_audio) -> None:
    r = client.post(
        "/v1/transcribe",
        headers=auth_headers,
        files={"audio": ("clip.wav", fake_audio, "audio/wav")},
        data={"title": "smoke", "language": "en"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"]
    assert body["transcript_txt_url"].endswith("/transcript.txt")
    assert body["transcript_json_url"].endswith("/transcript.json")
    assert body["speakers_detected"] == 2
    assert body["duration_seconds"] == 4.5
    assert body["language"] == "en"

    # The result files should now exist via the result endpoints.
    txt = client.get(body["transcript_txt_url"], headers=auth_headers)
    assert txt.status_code == 200
    assert "SPEAKER_00" in txt.text
    assert "SPEAKER_01" in txt.text
    assert "[00:00]" in txt.text  # timestamp prefix

    js = client.get(body["transcript_json_url"], headers=auth_headers)
    assert js.status_code == 200
    payload = json.loads(js.text)
    assert payload["id"] == body["id"]
    assert payload["language"] == "en"
    assert payload["speakers_detected"] == 2
    assert len(payload["segments"]) == 3


def test_transcribe_accepts_translate_task(client, auth_headers, fake_audio) -> None:
    r = client.post(
        "/v1/transcribe",
        headers=auth_headers,
        files={"audio": ("clip.wav", fake_audio, "audio/wav")},
        data={"title": "smoke", "task": "translate"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["task"] == "translate"


def test_transcribe_rejects_bad_task(client, auth_headers, fake_audio) -> None:
    r = client.post(
        "/v1/transcribe",
        headers=auth_headers,
        files={"audio": ("clip.wav", fake_audio, "audio/wav")},
        data={"task": "summarize"},
    )
    assert r.status_code == 400


def test_results_404_for_unknown_job(client, auth_headers) -> None:
    r = client.get(
        "/v1/results/00000000-0000-0000-0000-000000000000/transcript.txt",
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_admin_storage_returns_numbers(client, auth_headers) -> None:
    r = client.get("/v1/admin/storage", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"uploads_mb", "outputs_mb", "models_mb"}
    for v in body.values():
        assert v >= 0
