FROM python:3.9-slim-bookworm

# Fix for No Logs: Disable python output buffering
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip3 install --no-cache-dir -r requirements.txt

CMD ["python3", "bot.py"]
