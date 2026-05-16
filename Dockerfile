FROM python:3.9-slim-buster
RUN apt-get update && apt-get install -y ffmpeg git
WORKDIR /app
COPY . .
RUN pip3 install --no-cache-dir -r requirements.txt
CMD ["python3", "bot.py"]
