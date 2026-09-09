"""Upload a recording from the browser or download from YouTube and get it into the library.

Mirrors render.py's shape on purpose: an in-memory job, a background thread, a status
endpoint the client polls. Transcription is CPU work of unpredictable length, so it cannot
happen inside the request, and inventing a second job mechanism for it would only mean two
things to reason about.

WHAT MAKES THIS DIFFERENT FROM RENDER
Render's input is a set of ids that already exist in the database, so validation is a
lookup. Here the input is a file the visitor chose or a YouTube link, which means:

  - The stored name is generated, never taken from the upload. A browser sends whatever
    filename it likes, including one with slashes in it.
  - The file lands in UPLOAD_DIR, not in the demo corpus. The image layer is read-only on
    Cloud Run, and visitor files must not be able to reach the committed repository.
  - Duration is measured with ffmpeg/yt-dlp BEFORE anything expensive starts, because the cap has
    to be enforced on the real media rather than on what the request claimed.
  - The take goes into a project derived from the session cookie, so one visitor's footage
    does not show up in everyone else's library.

ORDER OF OPERATIONS: validate, then charge, then work. A rejected upload costs nothing,
which is the same rule render follows and for the same reason — billing someone for their
own rejected request is indefensible.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import ingest as ingest_doc
from pipeline import schema

from . import ch, config, sessions

FFPROBE_TIMEOUT_S = 60
# Read in chunks so a large upload is never held in memory in one piece.
CHUNK_BYTES = 1024 * 1024


class UploadRejected(Exception):
    """Failed validation. Rejected before any credit is spent."""


@dataclass
class Job:
    id: str
    session_id: str
    project: str
    take_id: str
    filename: str = ""              # what the visitor called it, for display only
    status: str = "queued"          # queued -> running -> done | failed
    stage: str = ""                 # saved | downloading | transcribing | labelling | writing
    seconds: float = 0.0
    language: str = ""
    lines: int = 0
    words: int = 0
    charged: int = 0
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def public(self) -> dict:
        payload = {
            "job_id": self.id,
            "status": self.status,
            "stage": self.stage,
            "take_id": self.take_id,
            "filename": self.filename,
            "media_seconds": round(self.seconds, 2),
            "charged": self.charged,
        }
        if self.status == "done":
            payload.update(
                {
                    "language": self.language,
                    "lines": self.lines,
                    "words": self.words,
                    "elapsed": round((self.finished_at or 0) - self.created_at, 1),
                }
            )
        if self.error:
            payload["error"] = self.error
        return payload


# In memory, ephemeral, exactly like render jobs. A restart loses them, which is accepted:
# a durable queue would be complexity this scale does not earn.
_jobs: dict[str, Job] = {}


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def active_for_session(session_id: str) -> int:
    return sum(
        1
        for job in _jobs.values()
        if job.session_id == session_id and job.status in ("queued", "running")
    )


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def transcription_available() -> tuple[bool, str]:
    """Whether this deployment can transcribe at all.

    faster-whisper lives in requirements-ingest.txt, not in the server runtime: ctranslate2
    is hundreds of megabytes and the container has no other use for it. So the interface
    asks first and says plainly that uploads are off, rather than accepting a file and
    failing a minute later.
    """
    try:
        import faster_whisper  # noqa: F401
    except Exception as error:
        return False, f"faster-whisper is not installed on this server ({error})"
    return True, ""


# --- Naming -----------------------------------------------------------------

_SAFE = re.compile(r"[^A-Za-z0-9]+")


def take_id_for(label: str, existing: int) -> str:
    """A take id derived from what the visitor typed, or a sequence number if they did not.

    Sanitised down to ASCII word characters. The id ends up in URLs, in ClickHouse and in
    the interface, and a take called `../../etc` would be someone's afternoon.
    """
    cleaned = _SAFE.sub("_", (label or "").strip()).strip("_")[:32]
    return cleaned.upper() if cleaned else f"UP{existing + 1:02d}"


def stored_name(take_id: str, suffix: str) -> str:
    """The filename on disk. Generated, never the one the browser sent."""
    return f"{take_id}_{uuid.uuid4().hex[:8]}{suffix.lower()}"


# --- Probing ----------------------------------------------------------------


def media_seconds(path: Path) -> float:
    """Duration in seconds, measured from the file.

    imageio-ffmpeg ships ffmpeg but not ffprobe, so the duration comes from ffmpeg's own
    report on stderr. Same technique render.py uses to detect a video stream.
    """
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=FFPROBE_TIMEOUT_S,
    )
    match = re.search(r"Duration:\s*(\d+):(\d\d):(\d\d)\.(\d+)", result.stderr or "")
    if not match:
        raise UploadRejected(
            "Could not read a duration from that file. Is it really audio or video?"
        )
    hours, minutes, secs, frac = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + float(f"0.{frac}")


def credits_for(seconds: float) -> int:
    """Priced per started minute, rounded up. A twenty second clip still costs one."""
    return max(1, math.ceil(seconds / 60)) * config.COST_INGEST_PER_MINUTE


# --- The job & Pipeline -----------------------------------------------------


def _process_ingest(job: Job, media: Path, language: str, speaker: str = "") -> None:
    """Shared pipeline: transcribe, label, validate, ingest into ClickHouse."""
    from pipeline import tone, transcribe

    job.stage = "transcribing"

    doc, stats = transcribe.transcribe(
        media,
        take_id=job.take_id,
        project_id=job.project,
        speaker=speaker,
        source_url=f"{config.UPLOAD_URL_PREFIX}{media.name}",
        model_size=config.UPLOAD_MODEL,
        language=language or None,
    )
    job.language = stats["language"]

    lines = doc["takes"][0]["lines"]
    if not lines:
        raise UploadRejected(
            "No speech was found in that file. Wrong language, or a silent track?"
        )

    job.stage = "labelling"
    try:
        tone.apply_tones(doc, media, dry_run=not tone_available())
    except Exception as error:
        print(f"upload {job.id}: tone pass failed, writing neutral: {error}")
        tone.apply_tones(doc, None, dry_run=True)

    problems = schema.validate(doc)
    if problems:
        raise UploadRejected("The transcript did not match the contract: " + problems[0])

    for note in schema.warnings(doc):
        print(f"upload {job.id}: {note}")

    job.stage = "writing"
    client = ch.client()
    from pipeline import db

    db.create_table(client)
    job.words = ingest_doc.ingest(client, doc)
    job.lines = len(lines)

    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (config.UPLOAD_DIR / f"{media.stem}.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    job.status = "done"
    job.stage = ""


def run_blocking(job: Job, media: Path, language: str) -> None:
    """Transcribe, label, write from uploaded file."""
    try:
        job.status = "running"
        _process_ingest(job, media, language)
    except Exception as error:
        job.status = "failed"
        job.error = str(error)
        media.unlink(missing_ok=True)
        if job.charged:
            sessions.refund(job.session_id, job.charged, f"ingest failed: {job.take_id}")
            job.charged = 0
    finally:
        job.finished_at = time.time()


def run_youtube_blocking(job: Job, url: str, max_duration_s: int, language: str) -> None:
    """Downloads YouTube video and ingests it."""
    from pipeline import youtube

    media: Path | None = None
    try:
        job.status = "running"
        job.stage = "downloading"

        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        filename_stem = stored_name(job.take_id, "").rstrip(".")
        media, meta = youtube.download(
            url,
            config.UPLOAD_DIR,
            filename_stem=filename_stem,
            max_duration_s=max_duration_s,
        )
        job.seconds = meta["duration"]
        _process_ingest(job, media, language, speaker=meta.get("uploader", "")[:32])
    except Exception as error:
        job.status = "failed"
        job.error = str(error)
        if media and media.is_file():
            media.unlink(missing_ok=True)
        if job.charged:
            sessions.refund(job.session_id, job.charged, f"ingest failed: {job.take_id}")
            job.charged = 0
    finally:
        job.finished_at = time.time()


def tone_available() -> bool:
    from . import agent

    return agent.available()


async def start(job: Job, media: Path, language: str) -> None:
    await asyncio.to_thread(run_blocking, job, media, language)


async def start_youtube(job: Job, url: str, max_duration_s: int, language: str) -> None:
    await asyncio.to_thread(run_youtube_blocking, job, url, max_duration_s, language)


# --- Receiving the file & YouTube -------------------------------------------


def takes_in_project(project: str) -> int:
    """How many takes this session already put in. The per-session cap rests on it."""
    try:
        result = ch.client().query(
            "SELECT uniqExact(take_id) FROM words WHERE project_id = {p:String}",
            parameters={"p": project},
        )
        return int(result.result_rows[0][0]) if result.result_rows else 0
    except Exception:
        # An unreachable database is the caller's problem to report, not a reason to
        # refuse on a count we could not read.
        return 0


async def receive(upload, session_id: str, label: str) -> tuple[Path, float, str]:
    """Streams the upload to disk and validates it. Returns (path, duration, take_id)."""
    if active_for_session(session_id) >= config.MAX_CONCURRENT_INGESTS_PER_SESSION:
        raise UploadRejected(
            "An upload is already being processed in this session. Wait for it to finish."
        )

    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in config.UPLOAD_SUFFIXES:
        raise UploadRejected(
            f"{suffix or 'That file'} is not a container we read. "
            f"Accepted: {', '.join(sorted(config.UPLOAD_SUFFIXES))}"
        )

    project = config.session_project(session_id)
    already = takes_in_project(project)
    if already >= config.MAX_UPLOADS_PER_SESSION:
        raise UploadRejected(
            f"This session already holds {already} uploads, which is the limit."
        )

    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    take_id = take_id_for(label, already)
    target = config.UPLOAD_DIR / stored_name(take_id, suffix)

    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with target.open("wb") as sink:
            while True:
                chunk = await upload.read(CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise UploadRejected(
                        f"Larger than the {config.MAX_UPLOAD_MB} MB limit."
                    )
                sink.write(chunk)

        if not written:
            raise UploadRejected("That file is empty.")

        seconds = media_seconds(target)
        if seconds > config.MAX_UPLOAD_SECONDS:
            raise UploadRejected(
                f"{seconds:.0f} seconds long; the limit is {config.MAX_UPLOAD_SECONDS}. "
                f"Trim it and try again."
            )
    except Exception:
        target.unlink(missing_ok=True)
        raise

    return target, seconds, take_id


async def receive_youtube(
    url: str, session_id: str, label: str, max_duration: int = 180
) -> tuple[dict, float, str]:
    """Validates YouTube URL and fetches info. Returns (info, duration, take_id)."""
    from pipeline import youtube

    if active_for_session(session_id) >= config.MAX_CONCURRENT_INGESTS_PER_SESSION:
        raise UploadRejected(
            "An upload is already being processed in this session. Wait for it to finish."
        )

    if not youtube.is_youtube_url(url):
        raise UploadRejected("Not a valid YouTube video or shorts URL.")

    project = config.session_project(session_id)
    already = takes_in_project(project)
    if already >= config.MAX_UPLOADS_PER_SESSION:
        raise UploadRejected(
            f"This session already holds {already} uploads, which is the limit."
        )

    try:
        info = await asyncio.to_thread(youtube.get_info, url)
    except Exception as error:
        raise UploadRejected(f"Could not read YouTube info: {error}")

    duration = float(info.get("duration", 0.0))
    cap = float(max_duration or config.MAX_UPLOAD_SECONDS)
    effective_seconds = min(duration if duration > 0 else cap, cap, float(config.MAX_UPLOAD_SECONDS))
    if effective_seconds <= 0:
        effective_seconds = float(config.MAX_UPLOAD_SECONDS)

    video_id = info.get("id") or youtube.extract_video_id(url)
    take_id = take_id_for(label or f"YT_{video_id}", already)

    return info, effective_seconds, take_id


def enqueue(session_id: str, take_id: str, filename: str, seconds: float) -> Job:
    """Creates the job record. Called after validation, before the charge."""
    job = Job(
        id=uuid.uuid4().hex[:12],
        session_id=session_id,
        project=config.session_project(session_id),
        take_id=take_id,
        filename=filename,
        seconds=seconds,
        stage="saved",
    )
    _jobs[job.id] = job
    return job
