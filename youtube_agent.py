"""YouTube audio transcript agent.

Two ways to get a transcript, tried in this order:
  1. Captions  -> youtube-transcript-api (fast, free, no download)
  2. Audio     -> yt-dlp downloads the audio track, Gemini transcribes it

Can be used as a library (get_transcript / summarize_transcript) or from the
command line:  python youtube_agent.py <youtube-url>
"""

import os
import re
import json
import time
import shutil
import tempfile

import google.generativeai as genai

# Audio longer than this is rejected before we spend money on transcription.
MAX_AUDIO_SECONDS = int(os.environ.get("YT_MAX_AUDIO_SECONDS", 3 * 60 * 60))
# Characters of transcript handed to the summarizer.
MAX_SUMMARY_CHARS = 120000

AUDIO_MODEL = os.environ.get("YT_AUDIO_MODEL", "gemini-flash-latest")
SUMMARY_MODEL = os.environ.get("YT_SUMMARY_MODEL", "gemini-flash-latest")


# --- URL PARSING ---
def extract_video_id(url):
    """Pull the 11 char video id out of any common YouTube URL (or an id)."""
    if not url:
        return None
    url = url.strip()

    # Already a bare video id
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url

    patterns = [
        r"(?:youtube\.com|youtube-nocookie\.com)/watch\?(?:.*&)?v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com|youtube-nocookie\.com)/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def watch_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"


# --- FORMATTING ---
def format_timestamp(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def segments_to_text(segments, with_timestamps=True):
    """Turn [{'start': 12.3, 'text': '...'}, ...] into readable text."""
    lines = []
    for seg in segments:
        text = (seg.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        if with_timestamps and seg.get("start") is not None:
            lines.append(f"[{format_timestamp(seg['start'])}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


# --- SOURCE 1: CAPTIONS ---
def fetch_captions(video_id, languages=("en",)):
    """Return caption segments, or None when the video has no usable captions.

    Handles both the 1.x instance API and the older 0.6.x classmethod API of
    youtube-transcript-api.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None

    languages = list(languages) or ["en"]
    try:
        api = YouTubeTranscriptApi()
        if hasattr(api, "fetch"):  # 1.x
            fetched = api.fetch(video_id, languages=languages)
            return [
                {"start": s.start, "duration": s.duration, "text": s.text}
                for s in fetched.snippets
            ]
        # 0.6.x
        return YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
    except Exception:
        # No captions, captions disabled, video unavailable, IP blocked...
        # The audio path is the fallback, so swallow and move on.
        return None


# --- SOURCE 2: AUDIO ---
def download_audio(video_id, workdir):
    """Download the audio track as mp3. Returns (path, metadata dict)."""
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError(
            "yt-dlp is not installed. Run: pip install yt-dlp"
        )

    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg not found. It is required to convert YouTube audio into a "
            "format Gemini accepts. Install it (apt-get install -y ffmpeg)."
        )

    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(workdir, "audio.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "64",
        }],
    }
    cookie_file = os.environ.get("YT_COOKIES_FILE")
    if cookie_file and os.path.exists(cookie_file):
        opts["cookiefile"] = cookie_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(watch_url(video_id), download=False)
        duration = info.get("duration") or 0
        if duration > MAX_AUDIO_SECONDS:
            raise RuntimeError(
                f"Video is {format_timestamp(duration)} long, limit is "
                f"{format_timestamp(MAX_AUDIO_SECONDS)}."
            )
        ydl.download([watch_url(video_id)])

    path = os.path.join(workdir, "audio.mp3")
    if not os.path.exists(path):
        # Postprocessor may have produced a different extension.
        for name in os.listdir(workdir):
            if name.startswith("audio."):
                path = os.path.join(workdir, name)
                break
    if not os.path.exists(path):
        raise RuntimeError("Audio download failed: no output file produced.")

    meta = {
        "title": info.get("title"),
        "channel": info.get("uploader") or info.get("channel"),
        "duration": duration,
    }
    return path, meta


def transcribe_audio(path):
    """Transcribe a local audio file with Gemini. Returns plain text."""
    if not os.environ.get("GENAI_API_KEY"):
        raise RuntimeError("GENAI_API_KEY is not set.")

    uploaded = genai.upload_file(path)
    try:
        # The File API needs a moment before the file is usable.
        deadline = time.time() + 300
        while getattr(uploaded.state, "name", uploaded.state) == "PROCESSING":
            if time.time() > deadline:
                raise RuntimeError("Timed out waiting for audio upload to process.")
            time.sleep(3)
            uploaded = genai.get_file(uploaded.name)
        if getattr(uploaded.state, "name", uploaded.state) == "FAILED":
            raise RuntimeError("Gemini could not process the uploaded audio.")

        prompt = """
        Transcribe this audio completely and accurately.
        Rules:
        - Prefix each paragraph with its start time as [H:MM:SS].
        - Start a new paragraph on a speaker change or topic shift.
        - If there are multiple speakers, label them (Speaker 1, Speaker 2, ...).
        - Keep the original language of the audio. Do not translate or summarise.
        Return the transcript only, no preamble.
        """
        model = genai.GenerativeModel(AUDIO_MODEL)
        response = model.generate_content([uploaded, prompt])
        return (response.text or "").strip()
    finally:
        try:
            genai.delete_file(uploaded.name)
        except Exception:
            pass


# --- AGENT ENTRY POINT ---
def get_transcript(url, languages=("en",), allow_audio=True):
    """Get a transcript for a YouTube URL.

    Returns {video_id, url, source, text, segments, title, channel, duration}.
    `source` is "captions" or "audio". Raises RuntimeError on failure.
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise RuntimeError(f"Could not find a YouTube video id in: {url}")

    result = {
        "video_id": video_id,
        "url": watch_url(video_id),
        "title": None,
        "channel": None,
        "duration": 0,
        "segments": [],
    }

    segments = fetch_captions(video_id, languages)
    if segments:
        result["source"] = "captions"
        result["segments"] = segments
        result["text"] = segments_to_text(segments)
        last = segments[-1]
        result["duration"] = int((last.get("start") or 0) + (last.get("duration") or 0))
        return result

    if not allow_audio:
        raise RuntimeError("No captions available for this video.")

    workdir = tempfile.mkdtemp(prefix="yt_")
    try:
        path, meta = download_audio(video_id, workdir)
        text = transcribe_audio(path)
        if not text:
            raise RuntimeError("Transcription returned nothing.")
        result.update(meta)
        result["source"] = "audio"
        result["text"] = text
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def summarize_transcript(text, context="sales"):
    """Summarise a transcript with Gemini. Returns a dict (never raises)."""
    if not text:
        return {}
    if not os.environ.get("GENAI_API_KEY"):
        return {}

    audience = (
        "Read it as a sales team would: who is speaking, what they need, "
        "objections raised, and anything worth following up on."
        if context == "sales" else ""
    )
    prompt = f"""
    Summarise the transcript below. {audience}
    Return JSON only, with these keys:
      "summary": 3-5 sentence overview,
      "key_points": list of the main points,
      "action_items": list of concrete follow-ups (empty list if none),
      "topics": list of short topic tags.

    TRANSCRIPT:
    {text[:MAX_SUMMARY_CHARS]}
    """
    try:
        model = genai.GenerativeModel(SUMMARY_MODEL)
        response = model.generate_content(prompt)
        raw = (response.text or "").replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception:
        return {}


# --- CLI ---
def main():
    import argparse

    parser = argparse.ArgumentParser(description="YouTube audio transcript agent")
    parser.add_argument("url", help="YouTube URL or video id")
    parser.add_argument("-l", "--lang", default="en",
                        help="Preferred caption languages, comma separated (default: en)")
    parser.add_argument("-o", "--output", help="Write the transcript to this file")
    parser.add_argument("--captions-only", action="store_true",
                        help="Do not fall back to downloading and transcribing audio")
    parser.add_argument("--summary", action="store_true", help="Also print an AI summary")
    args = parser.parse_args()

    api_key = os.environ.get("GENAI_API_KEY")
    if api_key:
        genai.configure(api_key=api_key)

    data = get_transcript(
        args.url,
        languages=tuple(l.strip() for l in args.lang.split(",") if l.strip()),
        allow_audio=not args.captions_only,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(data["text"])
        print(f"Transcript ({data['source']}) written to {args.output}")
    else:
        print(data["text"])

    if args.summary:
        summary = summarize_transcript(data["text"])
        print("\n--- SUMMARY ---")
        print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
