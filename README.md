# Sales AI Manager

Flask app that turns sales paperwork and recordings into structured data with Gemini.

* **PO pipeline** (`/`) - upload a PDF or let the inbox watcher pick one up; POs, order
  acknowledgements and shipping docs are extracted and tracked.
* **YouTube transcript agent** (`/youtube`) - paste a YouTube link and get a full
  timestamped transcript plus a summary with key points and action items.

## YouTube transcript agent

How a job runs:

1. The video id is parsed from any usual link shape (`watch?v=`, `youtu.be`, `shorts`,
   `embed`, `live`, or a bare 11-character id).
2. If the video has captions they are used directly - fast and free. Tick
   **"Ignore captions and transcribe the audio with AI"** to skip this step.
3. Otherwise yt-dlp downloads the audio track, ffmpeg converts it to a low-bitrate mono
   mp3, long recordings are cut into chunks, and each chunk is transcribed by Gemini.
   Chunk timestamps are shifted back onto the real timeline.
4. The transcript is summarised into `summary`, `key_points`, `action_items` and `topics`.

Transcription runs in a background thread; the job row carries a live status
(`Queued` → `Downloading audio` → `Transcribing audio (1/3)` → `Summarising` → `Done`),
and the detail page refreshes itself until the job finishes.

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/youtube` | Submit a URL, list recent transcripts |
| POST | `/youtube/transcribe` | Form submit (`url`, optional `force_audio`) |
| GET | `/youtube/<id>` | Transcript, summary and live status |
| GET | `/youtube/<id>/download` | Transcript as a `.txt` file |
| POST | `/youtube/<id>/delete` | Remove a transcript |
| POST | `/api/youtube` | JSON `{"url": ..., "force_audio": false}` → `202 {"id": ...}` |
| GET | `/api/youtube/<id>` | JSON job status / transcript / summary |

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GENAI_API_KEY` | - | Required for AI transcription and summaries |
| `GENAI_MODEL` | `gemini-flash-latest` | Model used for transcription and summaries |
| `YT_MAX_DURATION_SEC` | `14400` | Reject videos longer than this |
| `YT_CHUNK_SEC` | `1800` | Audio chunk length sent to Gemini |
| `YT_AUDIO_BITRATE` | `64` | mp3 bitrate (kbps) for the downloaded audio |
| `YT_CAPTION_LANGS` | `en,en-US,en-GB` | Caption languages to try, in order |
| `YT_REQUEST_TIMEOUT` | `900` | Per-chunk transcription timeout (seconds) |
| `YTDLP_COOKIES_FILE` | - | Cookie file for videos YouTube gates on sign-in |

ffmpeg is required for the audio path and is installed by the Dockerfile. Without it the
agent still works from captions, and falls back to sending the raw audio in one piece.
