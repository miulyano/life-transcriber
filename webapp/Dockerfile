FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-webapp.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-webapp.txt

COPY bot/ ./bot/
COPY webapp/ ./webapp/

CMD ["uvicorn", "webapp.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
