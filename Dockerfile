# One stage, one origin.
#
# There is NO node stage: web/ is not built, the file being served is the source
# itself. That is the concrete payoff of departing from the blueprint's React
# decision -- the image stays small and there is no compile step to break on deploy.
#
# No faster-whisper either: requirements.txt is the server runtime, transcription
# happens offline (requirements-ingest.txt). ctranslate2 is hundreds of MB and
# never runs here.

FROM python:3.11-slim

# Python behaviour: no .pyc files, unbuffered output (so Cloud Run logs appear at once)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies in their own layer: a code change does not reinstall them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The application. scratch/, .venv/ and .env are excluded by .dockerignore.
COPY pipeline/ ./pipeline/
COPY server/ ./server/
COPY web/ ./web/
COPY dev/ ./dev/
# demo/ is not a build artifact but CONTENT: without it a judge hears nothing.
COPY demo/ ./demo/

# /tmp is the only place that needs to be writable. The image layer is treated read-only.
ENV SESSION_DB=/tmp/sessions.db \
    RENDER_DIR=/tmp/renders \
    PORT=8080

# Non-root user. The app has no reason to write into the image.
RUN useradd --create-home --uid 10001 cinema \
    && mkdir -p /tmp/renders \
    && chown -R cinema:cinema /tmp/renders
USER cinema

EXPOSE 8080

# Cloud Run supplies PORT itself, so shell expansion is required. But the plain
# shell form sends SIGTERM to the shell instead of uvicorn and graceful shutdown
# breaks. JSON form + `exec`: the shell expands PORT, then uvicorn replaces it and
# receives signals directly.
CMD ["sh", "-c", "exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT}"]
