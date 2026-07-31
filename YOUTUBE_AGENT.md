# YouTube Transcript Agent

Turns a YouTube link into a transcript (plus an optional AI summary).

It tries two sources, in order:

1. **Captions** — `youtube-transcript-api`. Fast, free, no download.
2. **Audio** — `yt-dlp` downloads the audio track, Gemini transcribes it.
   Used automatically when a video has no captions.

## Web UI

Open `/youtube` (also linked from the home page). Paste a URL, submit, and the
job runs in the background — the list page refreshes every 10s while jobs are
running. Finished jobs can be viewed in the browser or downloaded as `.txt`.

Routes:

| Route | Purpose |
| --- | --- |
| `GET /youtube` | Job list + submit form |
| `POST /youtube` | Queue a video (`url`, `lang`, `summary`) |
| `GET /youtube/<id>` | Transcript + summary |
| `GET /youtube/<id>/download` | Transcript as `.txt` |

Jobs are stored in the `youtube_transcripts_v1` table.

## Command line

```bash
python youtube_agent.py "https://youtu.be/VIDEO_ID"
python youtube_agent.py VIDEO_ID -o transcript.txt --summary
python youtube_agent.py URL --lang en,ta --captions-only
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GENAI_API_KEY` | — | Required for audio transcription and summaries |
| `YT_MAX_AUDIO_SECONDS` | `10800` | Reject videos longer than this before transcribing |
| `YT_AUDIO_MODEL` | `gemini-flash-latest` | Model used for transcription |
| `YT_SUMMARY_MODEL` | `gemini-flash-latest` | Model used for summaries |
| `YT_COOKIES_FILE` | — | Netscape cookie file, if YouTube asks the host to sign in |

## Notes

- **ffmpeg is required** for the audio path. The Dockerfile installs it; for a
  local run use `apt-get install -y ffmpeg` (or `brew install ffmpeg`).
- Captions need no API key — only the audio path and summaries call Gemini.
- YouTube sometimes blocks datacenter IPs. If the captions path fails and
  downloads get "sign in to confirm you're not a bot", supply `YT_COOKIES_FILE`
  or run the fetch from a residential IP.
