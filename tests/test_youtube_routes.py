"""Route-level tests for the transcript agent, with the agent stubbed out."""

import youtube_agent
from conftest import wait_for_job


def test_listing_page_renders(client):
    assert client.get("/youtube").status_code == 200


def test_home_page_links_to_the_agent(client):
    assert "/youtube" in client.get("/").get_data(as_text=True)


def test_form_submit_runs_a_job_to_completion(client, stub_agent):
    response = client.post("/youtube/transcribe",
                           data={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert response.status_code == 302
    job = wait_for_job(client, 1)

    assert job["status"] == "Done"
    assert job["source"] == "audio"
    assert job["title"] == "Demo Call"
    assert job["summary"]["action_items"] == ["send quote"]
    assert stub_agent["prefer_captions"] is True    # captions preferred by default


def test_force_audio_checkbox_is_honoured(client, stub_agent):
    client.post("/youtube/transcribe",
                data={"url": "https://youtu.be/dQw4w9WgXcQ", "force_audio": "1"})
    wait_for_job(client, 1)
    assert stub_agent["prefer_captions"] is False


def test_detail_page_shows_transcript_and_summary(client, stub_agent):
    client.post("/youtube/transcribe", data={"url": "https://youtu.be/dQw4w9WgXcQ"})
    wait_for_job(client, 1)

    html = client.get("/youtube/1").get_data(as_text=True)
    assert "Demo Call" in html
    assert "Speaker 1: hello there" in html
    assert "send quote" in html
    assert "01:02:05" in html                 # duration formatted
    assert "http-equiv" not in html           # finished, so no auto-refresh


def test_download_returns_a_named_text_file(client, stub_agent):
    client.post("/youtube/transcribe", data={"url": "https://youtu.be/dQw4w9WgXcQ"})
    wait_for_job(client, 1)

    response = client.get("/youtube/1/download")
    assert response.status_code == 200
    assert b"Speaker 1: hello there" in response.data
    assert "Demo_Call.txt" in response.headers["Content-Disposition"]


def test_transcript_text_is_escaped_not_injected(client, stub_agent, monkeypatch):
    def nasty(url, prefer_captions=True, progress=None):
        return {"video_id": "dQw4w9WgXcQ", "url": url, "title": "T", "channel": "C",
                "duration_sec": 10, "source": "captions",
                "transcript": "<script>alert(1)</script>"}

    monkeypatch.setattr(youtube_agent, "transcribe_youtube", nasty)
    client.post("/youtube/transcribe", data={"url": "https://youtu.be/dQw4w9WgXcQ"})
    wait_for_job(client, 1)

    html = client.get("/youtube/1").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_worker_failure_is_recorded_and_shown(client, monkeypatch):
    def boom(url, prefer_captions=True, progress=None):
        raise youtube_agent.AgentError("Video is 05:00:00 long - the limit is 04:00:00.")

    monkeypatch.setattr(youtube_agent, "transcribe_youtube", boom)
    client.post("/youtube/transcribe", data={"url": "https://youtu.be/dQw4w9WgXcQ"})
    job = wait_for_job(client, 1)

    assert job["status"] == "Error"
    assert "the limit is" in job["error"]
    assert "the limit is" in client.get("/youtube/1").get_data(as_text=True)


def test_delete_removes_the_job(client, stub_agent):
    client.post("/youtube/transcribe", data={"url": "https://youtu.be/dQw4w9WgXcQ"})
    wait_for_job(client, 1)

    assert client.post("/youtube/1/delete").status_code == 302
    assert client.get("/api/youtube/1").status_code == 404


def test_json_api_accepts_and_reports(client, stub_agent):
    response = client.post("/api/youtube", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert response.status_code == 202
    job = wait_for_job(client, response.get_json()["id"])
    assert job["status"] == "Done"


def test_json_api_rejects_a_bad_url(client):
    response = client.post("/api/youtube", json={"url": "https://example.com/nope"})
    assert response.status_code == 400
    assert "Could not find" in response.get_json()["error"]


def test_form_reports_a_bad_url_without_creating_a_job(client):
    response = client.post("/youtube/transcribe", data={"url": "garbage"})
    assert response.status_code == 302
    assert "error=" in response.headers["Location"]
    assert client.get("/api/youtube/1").status_code == 404


def test_missing_ids_are_handled(client):
    assert client.get("/api/youtube/999").status_code == 404
    assert client.get("/youtube/999").status_code == 302
    assert client.get("/youtube/999/download").status_code == 302
