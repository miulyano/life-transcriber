FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install latest yt-dlp separately (updates frequently)
RUN pip install --no-cache-dir --upgrade yt-dlp

COPY bot/ ./bot/

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "bot.main"]
