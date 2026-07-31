FROM python:3.9-slim

WORKDIR /app

# ffmpeg is used to convert downloaded YouTube audio to small mono mp3 chunks
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .


CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:10000", "main:app"]
