FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl && \
    rm -rf /var/lib/apt/lists/*

# Python deps from repo root
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# App source
COPY control_plane /app/control_plane

ENV PYTHONPATH=/app
EXPOSE 8000

# Start uvicorn directly
CMD ["bash","-lc","uvicorn control_plane.app:app --host 0.0.0.0 --port 8000"]
