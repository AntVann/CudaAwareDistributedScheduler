FROM python:3.11-slim

WORKDIR /app

# Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# App source
COPY agent /app/agent

ENV PYTHONPATH=/app
EXPOSE 8001
EXPOSE 8002

# Start uvicorn directly; respect AGENT_PORT
CMD ["bash","-lc","uvicorn agent.agent:app --host 0.0.0.0 --port ${AGENT_PORT:-8001}"]
