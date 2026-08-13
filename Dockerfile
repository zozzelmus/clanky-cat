FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY clanky_cat ./clanky_cat

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "clanky_cat"]
