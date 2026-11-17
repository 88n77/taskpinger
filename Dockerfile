FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Europe/Kyiv

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data/ і logs/ монтуються як volume (див. docker-compose.yml)
CMD ["python", "main.py"]
