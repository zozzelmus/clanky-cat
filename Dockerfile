FROM python:3.12-slim

# ffmpeg merges/re-encodes reposted videos (TikTok, Instagram, X, YouTube Shorts)
# to Discord-friendly mp4s.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY clanky_cat ./clanky_cat

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "clanky_cat"]
