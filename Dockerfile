# Use a newer, supported Debian-based image
FROM python:3.9-slim-bookworm

# Install ffmpeg and git (clean up after to save space)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Install python dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

# Start the bot
CMD ["python3", "bot.py"]
