# One stage, one origin.
#
# There is NO node stage: web/ is not built, the file being served is the source
# itself. That is the concrete payoff of departing from the blueprint's React
# decision -- there is no compile step to break on deploy.
#
# faster-whisper IS here, and that is a reversal. The argument for leaving it out
# was that transcription happens offline and the server only queries prepared
# rows. Uploads ended that argument: a library a visitor cannot add to is a demo
# of itself. The cost is about 260 MB with the model baked in.
#
# To go back to the small image, drop requirements-ingest.txt and the model step
# below. Nothing breaks -- /api/upload/status reports uploads as off and the
# interface says so, because that path was built before this decision was made.

FROM python:3.11-slim

# Python behaviour: no .pyc files, unbuffered output (so Cloud Run logs appear at once)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies in their own layer: a code change does not reinstall them.
COPY requirements.txt requirements-ingest.txt ./
RUN pip install --no-cache-dir -r requirements-ingest.txt

# The transcription model, baked in rather than fetched on first use.
#
# faster-whisper downloads from HuggingFace on demand and caches it. On Cloud Run the
# filesystem is ephemeral, so "on demand" would mean 141 MB pulled from a third party on
# every cold start, with the first upload paying for it. Baking it in trades image size
# for a predictable first request and one less runtime dependency on someone else's CDN.
ENV HF_HOME=/opt/hf
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')" \
    && chmod -R a+rX /opt/hf

# The application. scratch/, .venv/ and .env are excluded by .dockerignore.
COPY pipeline/ ./pipeline/
COPY server/ ./server/
COPY web/ ./web/
COPY dev/ ./dev/
# demo/ is not a build artifact but CONTENT: without it a judge hears nothing.
COPY demo/ ./demo/

# /tmp is the only place that needs to be writable. The image layer is treated read-only.
#
# UPLOAD_DIR belongs here and not in the image for two reasons: visitor media must not be
# able to land in the committed corpus, and Cloud Run's filesystem is ephemeral anyway, so
# uploads live as long as the instance does. Object storage is the durable version and it
# is the same piece of work as moving the demo corpus to GCS.
ENV SESSION_DB=/tmp/sessions.db \
    RENDER_DIR=/tmp/renders \
    UPLOAD_DIR=/tmp/uploads \
    PORT=8080

# Non-root user. The app has no reason to write into the image.
RUN useradd --create-home --uid 10001 cinema \
    && mkdir -p /tmp/renders /tmp/uploads \
    && chown -R cinema:cinema /tmp/renders /tmp/uploads
USER cinema

EXPOSE 8080

# Cloud Run supplies PORT itself, so shell expansion is required. But the plain
# shell form sends SIGTERM to the shell instead of uvicorn and graceful shutdown
# breaks. JSON form + `exec`: the shell expands PORT, then uvicorn replaces it and
# receives signals directly.
CMD ["sh", "-c", "exec uvicorn server.main:app --host 0.0.0.0 --port ${PORT}"]
