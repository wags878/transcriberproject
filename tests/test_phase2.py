from __future__ import annotations


def test_txt_paragraph_merge(client, auth_headers, fake_audio) -> None:
    r = client.post(
        "/v1/transcribe",
        headers=auth_headers,
        files={"audio": ("clip.wav", fake_audio, "audio/wav")},
        data={"title": "merge-test", "language": "en"},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    txt = client.get(body["transcript_txt_url"], headers=auth_headers)
    assert txt.status_code == 200

    paragraphs = txt.text.rstrip("\n").split("\n\n")
    assert len(paragraphs) == 2, txt.text
    assert paragraphs[0] == "[00:00] SPEAKER_00: Hello there. How are you today?"
    assert paragraphs[1] == "[00:03] SPEAKER_01: I am well, thank you."
