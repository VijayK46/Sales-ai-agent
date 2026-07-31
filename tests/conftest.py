import os
import sys
import tempfile

import pytest

# main.py reads DATABASE_URL and starts the email watcher at import time, so
# both have to be set up before it is imported.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.pop("EMAIL_USER", None)
os.environ.pop("GENAI_API_KEY", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import youtube_agent  # noqa: E402


@pytest.fixture
def app():
    return main.app


@pytest.fixture
def client(app):
    with app.app_context():
        main.db.drop_all()
        main.db.create_all()
    return app.test_client()


@pytest.fixture
def stub_agent(monkeypatch):
    """Replace the network/model calls with a canned successful run."""
    calls = {}

    def fake_transcribe(url, prefer_captions=True, progress=None):
        calls["url"] = url
        calls["prefer_captions"] = prefer_captions
        if progress:
            progress("Downloading audio")
        return {
            "video_id": "dQw4w9WgXcQ", "url": url, "title": "Demo Call",
            "channel": "Acme", "duration_sec": 3725, "source": "audio",
            "transcript": "[00:00:00] Speaker 1: hello there",
        }

    monkeypatch.setattr(youtube_agent, "transcribe_youtube", fake_transcribe)
    monkeypatch.setattr(youtube_agent, "summarize_transcript", lambda text, title="": {
        "summary": "A demo.", "key_points": ["p1"],
        "action_items": ["send quote"], "topics": ["demo"],
    })
    return calls


def wait_for_job(client, job_id, timeout=15):
    """Poll the JSON API until the background worker finishes."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/youtube/{job_id}").get_json()
        if payload and payload.get("status") in ("Done", "Error"):
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")
