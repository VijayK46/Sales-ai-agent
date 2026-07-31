"""YouTube audio transcript agent.

Given a YouTube URL this module produces a full text transcript:

1. If the video already has captions, they are fetched directly (fast, free).
2. Otherwise (or when captions are skipped) the audio track is downloaded with
   yt-dlp, converted to a small mono mp3 with ffmpeg and transcribed by Gemini.
   Long recordings are split into chunks so each request stays a sane size, and
   the per-chunk timestamps are shifted back onto the real timeline.

The module keeps no state and knows nothing about the database - main.py owns
the job records and passes a `progress` callback in to report on each stage.
"""

import os
import re
import json
import time
import shutil
import tempfile
import subprocess

import google.generativeai as genai

# --- CONFIGURATION ---
MODEL_NAME = os.environ.get("GENAI_MODEL", "gemini-flash-latest")
MAX_DURATION_SEC = int(os.environ.get("YT_MAX_DURATION_SEC", str(4 * 60 * 60)))
CHUNK_SEC = int(os.environ.get("YT_CHUNK_SEC", str(30 * 60)))
AUDIO_BITRATE = os.environ.get("YT_AUDIO_BITRATE", "64")
COOKIES_FILE = os.environ.get("YTDLP_COOKIES_FILE")
CAPTION_LANGS = [l.strip() for l in os.environ.get("YT_CAPTION_LANGS", "en,en-US,en-GB").split(",") if l.strip()]
REQUEST_TIMEOUT = int(os.environ.get("YT_REQUEST_TIMEOUT", "900"))

TRANSCRIBE_PROMPT = """You are a professional transcriptionist.
Transcribe this audio COMPLETELY and VERBATIM into text.

Rules:
* Start a new line whenever the speaker changes or the topic moves on.
* Begin every line with a timestamp in [HH:MM:SS] format, relative to the start
  of THIS audio file.
* If more than one person speaks, label them: [HH:MM:SS] Speaker 1: ...
* Do not summarise, translate, or skip any section. Write what is said.
* If a passage is inaudible write [inaudible].
Return only the transcript text - no preamble, no markdown fences.
"""

SUMMARY_PROMPT = """You are analysing the transcript of a recorded call or video.
Return STRICT JSON with these keys:
  "summary": a 3-5 sentence overview,
  "key_points": array of the most important points (max 8 short strings),
  "action_items": array of concrete follow-ups or commitments (empty if none),
  "topics": array of 3-6 short topic tags.
Return JSON only.

TITLE: {title}
TRANSCRIPT:
{transcript}
"""


class AgentError(Exception):
    """Raised for problems the user can act on (bad URL, video too long...)."""


# --- URL / ID HANDLING ---
_ID_PATTERNS = [
    r"(?:v=|vi=)([0-9A-Za-z_-]{11})",
    r"youtu\.be/([0-9A-Za-z_-]{11})",
    r"youtube\.com/(?:embed|v|shorts|live)/([0-9A-Za-z_-]{11})",
]


def extract_video_id(url):
    """Pull the 11 character video id out of any common YouTube URL form."""
    if not url:
        raise AgentError("No YouTube URL supplied.")
    url = url.strip()
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", url):
        return url
    for pattern in _ID_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise AgentError(f"Could not find a YouTube video id in: {url}")


def watch_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


# --- TIME HELPERS ---
def format_timestamp(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _parse_timestamp(text):
    parts = [int(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _shift_timestamps(text, offset_sec):
    """Rewrite [HH:MM:SS] markers so they sit on the full-video timeline."""
    if not offset_sec:
        return text

    def replace(match):
        return "[" + format_timestamp(_parse_timestamp(match.group(1)) + offset_sec) + "]"

    return re.sub(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", replace, text)


# --- CAPTION PATH ---
def fetch_caption_transcript(video_id):
    """Return caption text, or None when the video has no usable captions."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None

    try:
        if hasattr(YouTubeTranscriptApi, "get_transcript"):  # api < 1.0
            segments = YouTubeTranscriptApi.get_transcript(video_id, languages=CAPTION_LANGS)
        else:  # api >= 1.0
            segments = YouTubeTranscriptApi().fetch(video_id, languages=CAPTION_LANGS).to_raw_data()
    except Exception:
        return None

    return _captions_to_text(segments)


def _captions_to_text(segments, group_sec=30):
    """Fold raw caption cues into timestamped paragraphs of ~group_sec each."""
    lines, buffer, block_start = [], [], None
    for seg in segments or []:
        text = (seg.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        start = float(seg.get("start", 0))
        # Close the current block before adding the cue that runs past it, so
        # the cue opens the next block instead of being pulled into this one.
        if block_start is not None and start - block_start >= group_sec:
            lines.append(f"[{format_timestamp(block_start)}] " + " ".join(buffer))
            buffer, block_start = [], None
        if block_start is None:
            block_start = start
        buffer.append(text)
    if buffer:
        lines.append(f"[{format_timestamp(block_start or 0)}] " + " ".join(buffer))
    return "\n".join(lines).strip() or None


# --- AUDIO PATH ---
def fetch_metadata(video_id):
    """Title / channel / duration without downloading the media."""
    try:
        import yt_dlp
    except ImportError:
        raise AgentError("yt-dlp is not installed - run: pip install -r requirements.txt")

    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(watch_url(video_id), download=False)
    except Exception as e:
        raise AgentError(f"Could not read video info: {e}")

    return {
        "title": info.get("title") or video_id,
        "channel": info.get("uploader") or info.get("channel") or "",
        "duration_sec": int(info.get("duration") or 0),
    }


def download_audio(video_id, work_dir):
    """Download the audio track and return the path to a small mono mp3."""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": os.path.join(work_dir, "audio.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": AUDIO_BITRATE,
        }],
        # Mono at a low bitrate: speech stays clear and uploads stay small.
        "postprocessor_args": {"ffmpegextractaudio": ["-ac", "1"]},
    }
    if COOKIES_FILE:
        opts["cookiefile"] = COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([watch_url(video_id)])
    except Exception as e:
        raise AgentError(f"Audio download failed: {e}")

    mp3_path = os.path.join(work_dir, "audio.mp3")
    if os.path.exists(mp3_path):
        return mp3_path

    # ffmpeg missing or conversion skipped - fall back to whatever was saved.
    leftovers = [os.path.join(work_dir, f) for f in os.listdir(work_dir)]
    if not leftovers:
        raise AgentError("Audio download produced no file.")
    return max(leftovers, key=os.path.getsize)


def split_audio(path, chunk_sec=CHUNK_SEC):
    """Cut the audio into chunk_sec pieces. Returns [(path, offset_sec), ...]."""
    if not shutil.which("ffmpeg"):
        return [(path, 0)]

    out_dir = os.path.join(os.path.dirname(path), "chunks")
    os.makedirs(out_dir, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
             "-f", "segment", "-segment_time", str(chunk_sec), "-c", "copy",
             os.path.join(out_dir, "part_%03d" + os.path.splitext(path)[1])],
            check=True,
        )
    except Exception:
        return [(path, 0)]

    parts = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir))
    if not parts:
        return [(path, 0)]
    return [(p, i * chunk_sec) for i, p in enumerate(parts)]


# --- GEMINI ---
def _require_api_key():
    if not os.environ.get("GENAI_API_KEY"):
        raise AgentError("GENAI_API_KEY is not set - cannot transcribe audio.")


def transcribe_audio_file(path):
    """Upload one audio file to Gemini and return its transcript text."""
    _require_api_key()
    uploaded = genai.upload_file(path=path)
    try:
        # Uploads are processed asynchronously; wait for the file to go ACTIVE.
        deadline = time.time() + 300
        while uploaded.state.name == "PROCESSING" and time.time() < deadline:
            time.sleep(3)
            uploaded = genai.get_file(uploaded.name)
        if uploaded.state.name != "ACTIVE":
            raise AgentError(f"Gemini could not process the audio (state: {uploaded.state.name}).")

        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(
            [uploaded, TRANSCRIBE_PROMPT],
            request_options={"timeout": REQUEST_TIMEOUT},
        )
        text = (response.text or "").replace("```", "").strip()
        if not text:
            raise AgentError("Gemini returned an empty transcript.")
        return text
    finally:
        try:
            genai.delete_file(uploaded.name)
        except Exception:
            pass


def summarize_transcript(transcript, title=""):
    """Return {"summary", "key_points", "action_items", "topics"} or None."""
    if not transcript:
        return None
    try:
        _require_api_key()
        model = genai.GenerativeModel(MODEL_NAME)
        prompt = SUMMARY_PROMPT.format(title=title or "Untitled", transcript=transcript[:200000])
        response = model.generate_content(prompt, request_options={"timeout": 180})
        text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception:
        return None


# --- ORCHESTRATION ---
def transcribe_youtube(url, prefer_captions=True, progress=None):
    """Run the full agent for one URL and return the transcript payload.

    Raises AgentError for anything the caller should show to the user.
    """
    def report(message):
        if progress:
            progress(message)

    video_id = extract_video_id(url)
    report("Reading video info")
    meta = fetch_metadata(video_id)

    if MAX_DURATION_SEC and meta["duration_sec"] > MAX_DURATION_SEC:
        raise AgentError(
            f"Video is {format_timestamp(meta['duration_sec'])} long - the limit is "
            f"{format_timestamp(MAX_DURATION_SEC)}."
        )

    result = {"video_id": video_id, "url": watch_url(video_id), **meta}

    if prefer_captions:
        report("Checking for captions")
        captions = fetch_caption_transcript(video_id)
        if captions:
            report("Captions found")
            result.update(source="captions", transcript=captions)
            return result

    work_dir = tempfile.mkdtemp(prefix="yt_agent_")
    try:
        report("Downloading audio")
        audio_path = download_audio(video_id, work_dir)

        chunks = split_audio(audio_path)
        pieces = []
        for index, (chunk_path, offset) in enumerate(chunks, start=1):
            report(f"Transcribing audio ({index}/{len(chunks)})")
            text = _shift_timestamps(transcribe_audio_file(chunk_path), offset)
            if len(chunks) > 1:
                pieces.append(f"--- Part {index} (from {format_timestamp(offset)}) ---\n{text}")
            else:
                pieces.append(text)

        result.update(source="audio", transcript="\n\n".join(pieces).strip())
        return result
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
