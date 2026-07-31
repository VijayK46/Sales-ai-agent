# 3.11: yt-dlp requires Python >= 3.10
FROM python:3.11-slim

WORKDIR /app

# ffmpeg converts downloaded YouTube audio into a format Gemini accepts
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .


CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:10000", "main:app"]
