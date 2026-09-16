FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Deno is required by yt-dlp as a JS runtime for YouTube signature solving
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

# Install latest yt-dlp separately (updates frequently)
# [default] pulls in yt-dlp-ejs: the JS challenge solver scripts that Deno
# runs; without them YouTube web-client formats are dropped ("Only images").
RUN pip install --no-cache-dir --upgrade "yt-dlp[default]"

COPY bot/ ./bot/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]
