FROM python:3.11-slim

RUN apt-get update && apt-get install -y calibre wget poppler-utils && rm -rf /var/lib/apt/lists/*

RUN wget https://github.com/pgaskin/kepubify/releases/download/v4.0.4/kepubify-linux-64bit -O /usr/local/bin/kepubify \
    && chmod +x /usr/local/bin/kepubify

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

COPY log_config.json .

RUN mkdir -p /app/books/uploads /app/books/processed

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --log-config log_config.json
