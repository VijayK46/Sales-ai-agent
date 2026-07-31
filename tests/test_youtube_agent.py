"""Unit tests for the agent itself - no network, no model calls."""

import pytest

import youtube_agent as ya


# --- URL PARSING ---
@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?t=30",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://m.youtube.com/watch?app=desktop&v=dQw4w9WgXcQ&list=PL123",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
    "https://www.youtube.com/live/dQw4w9WgXcQ",
    "  dQw4w9WgXcQ  ",
])
def test_extract_video_id(url):
    assert ya.extract_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", ["", None, "https://example.com/nope", "garbage"])
def test_extract_video_id_rejects_junk(url):
    with pytest.raises(ya.AgentError):
        ya.extract_video_id(url)


def test_watch_url_drops_playlist_and_time_params():
    # The audio path builds its own URL, so a &list= can never pull a playlist.
    vid = ya.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1&t=90")
    assert ya.watch_url(vid) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# --- TIMESTAMPS ---
def test_format_timestamp():
    assert ya.format_timestamp(0) == "00:00:00"
    assert ya.format_timestamp(3725) == "01:02:05"
    assert ya.format_timestamp(-5) == "00:00:00"


def test_shift_timestamps_moves_chunks_onto_the_real_timeline():
    text = "[00:00:05] hi\n[00:01:00] yo"
    assert ya._shift_timestamps(text, 1800) == "[00:30:05] hi\n[00:31:00] yo"


def test_shift_timestamps_accepts_mm_ss_and_zero_offset():
    assert ya._shift_timestamps("[01:05] hi", 60) == "[00:02:05] hi"
    assert ya._shift_timestamps("[00:00:05] hi", 0) == "[00:00:05] hi"


# --- CAPTION FOLDING ---
def test_captions_fold_into_timestamped_blocks():
    segments = [
        {"text": "hello", "start": 0.0, "duration": 2},
        {"text": "world", "start": 10.0, "duration": 2},
        {"text": "next\nblock", "start": 45.0, "duration": 2},
        {"text": "   ", "start": 50.0, "duration": 1},
    ]
    assert ya._captions_to_text(segments) == (
        "[00:00:00] hello world\n[00:00:45] next block"
    )


def test_a_cue_past_the_boundary_opens_the_next_block():
    # Regression: the crossing cue used to be glued onto the previous block.
    segments = [{"text": "a", "start": 0.0, "duration": 1},
                {"text": "b", "start": 31.0, "duration": 1}]
    assert ya._captions_to_text(segments) == "[00:00:00] a\n[00:00:31] b"


def test_captions_to_text_handles_empty():
    assert ya._captions_to_text([]) is None
    assert ya._captions_to_text(None) is None


def test_duration_from_segments():
    assert ya._duration_from_segments([{"start": 118.0, "duration": 2.0}]) == 120
    assert ya._duration_from_segments([]) == 0


# --- ORCHESTRATION ---
def test_captions_path_survives_a_yt_dlp_failure(monkeypatch):
    """A caption-bearing video must transcribe even when YouTube blocks yt-dlp."""
    def blocked(video_id):
        raise ya.AgentError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(ya, "fetch_metadata", blocked)
    monkeypatch.setattr(ya, "fetch_caption_segments", lambda vid: [
        {"text": "hello", "start": 0.0, "duration": 2.0},
        {"text": "bye", "start": 118.0, "duration": 2.0},
    ])

    result = ya.transcribe_youtube("https://youtu.be/dQw4w9WgXcQ")
    assert result["source"] == "captions"
    assert result["duration_sec"] == 120      # derived from the last cue
    assert "hello" in result["transcript"]


def test_captions_path_uses_metadata_when_it_is_available(monkeypatch):
    monkeypatch.setattr(ya, "fetch_metadata", lambda vid: {
        "title": "Real Title", "channel": "Acme", "duration_sec": 500})
    monkeypatch.setattr(ya, "fetch_caption_segments", lambda vid: [
        {"text": "hi", "start": 0.0, "duration": 1.0}])

    result = ya.transcribe_youtube("https://youtu.be/dQw4w9WgXcQ")
    assert result["title"] == "Real Title"
    assert result["duration_sec"] == 500


def test_force_audio_skips_captions(monkeypatch):
    monkeypatch.setattr(ya, "fetch_caption_segments",
                        lambda vid: pytest.fail("captions must not be consulted"))
    monkeypatch.setattr(ya, "fetch_metadata", lambda vid: {
        "title": "T", "channel": "C", "duration_sec": 10})
    monkeypatch.setattr(ya, "download_audio", lambda vid, wd: "/tmp/a.mp3")
    monkeypatch.setattr(ya, "split_audio", lambda path: [(path, 0)])
    monkeypatch.setattr(ya, "transcribe_audio_file", lambda path: "[00:00:01] spoken")

    result = ya.transcribe_youtube("https://youtu.be/dQw4w9WgXcQ", prefer_captions=False)
    assert result["source"] == "audio"
    assert result["transcript"] == "[00:00:01] spoken"


def test_chunks_are_stitched_with_shifted_timestamps(monkeypatch):
    monkeypatch.setattr(ya, "fetch_caption_segments", lambda vid: None)
    monkeypatch.setattr(ya, "fetch_metadata", lambda vid: {
        "title": "T", "channel": "C", "duration_sec": 3600})
    monkeypatch.setattr(ya, "download_audio", lambda vid, wd: "/tmp/a.mp3")
    monkeypatch.setattr(ya, "split_audio", lambda path: [(path, 0), (path, 1800)])
    monkeypatch.setattr(ya, "transcribe_audio_file", lambda path: "[00:00:10] line")

    transcript = ya.transcribe_youtube("https://youtu.be/dQw4w9WgXcQ")["transcript"]
    assert "Part 1 (from 00:00:00)" in transcript
    assert "[00:00:10] line" in transcript      # first chunk unchanged
    assert "[00:30:10] line" in transcript      # second chunk shifted
    assert "Part 2 (from 00:30:00)" in transcript


def test_long_videos_are_rejected_before_any_download(monkeypatch):
    monkeypatch.setattr(ya, "fetch_caption_segments", lambda vid: None)
    monkeypatch.setattr(ya, "fetch_metadata", lambda vid: {
        "title": "T", "channel": "C", "duration_sec": 99999})
    monkeypatch.setattr(ya, "download_audio",
                        lambda vid, wd: pytest.fail("must not download"))

    with pytest.raises(ya.AgentError, match="limit is"):
        ya.transcribe_youtube("https://youtu.be/dQw4w9WgXcQ")


def test_missing_ffmpeg_is_reported_directly(monkeypatch, tmp_path):
    """Without ffmpeg yt-dlp leaves a .webm that Gemini rejects obscurely."""
    monkeypatch.setattr(ya.shutil, "which", lambda name: None)
    with pytest.raises(ya.AgentError, match="ffmpeg is not installed"):
        ya.download_audio("dQw4w9WgXcQ", str(tmp_path))


def test_split_audio_degrades_to_one_piece_without_ffmpeg(monkeypatch, tmp_path):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"\x00" * 32)
    monkeypatch.setattr(ya.shutil, "which", lambda name: None)
    assert ya.split_audio(str(path)) == [(str(path), 0)]


def test_transcribe_audio_file_requires_an_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("GENAI_API_KEY", raising=False)
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"\x00")
    with pytest.raises(ya.AgentError, match="GENAI_API_KEY"):
        ya.transcribe_audio_file(str(path))


def test_summarize_never_raises(monkeypatch):
    monkeypatch.delenv("GENAI_API_KEY", raising=False)
    assert ya.summarize_transcript("some text") is None
    assert ya.summarize_transcript("") is None
