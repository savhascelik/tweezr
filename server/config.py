"""Server settings. No credentials; everything comes from the environment.

The credit design in one sentence: **the impressive path is free, the expensive path is
metered.**

Search, proposal and preview cost nothing. Not only for cost reasons: when a judge opens
the URL they need to reach the point of the product without hitting a wall. Credits are
not revenue here, they are a brake against a surprise bill.
"""

from __future__ import annotations

import os
from pathlib import Path

# Before the first os.environ read below. `pipeline/__init__.py` does this too, but this
# module is imported first on some paths and a settings file that reads a variable before
# anything loaded it would be a very quiet bug.
from pipeline import env as _env

_env.load()

APP_ROOT = Path(__file__).resolve().parent.parent

# --- Projects ---
# Which projects a guest may query. An allowlist, because project_id arrives in a request.
DEMO_PROJECT = "demo"
ALLOWED_PROJECTS = frozenset({DEMO_PROJECT})

# Uploads go into a project of the visitor's own, so one person's footage does not turn up
# in everyone else's library. The id is derived from the session cookie rather than taken
# from the request, which is what keeps it out of the allowlist argument entirely: a caller
# cannot ask for someone else's project because they cannot name it.
UPLOAD_PROJECT_PREFIX = "up_"


def session_project(session_id: str) -> str:
    return f"{UPLOAD_PROJECT_PREFIX}{session_id}"

# --- Session ---
SESSION_COOKIE = "cinema_session"
SESSION_TTL_DAYS = 7

# --- Credits ---
GUEST_CREDITS = 10
MEMBER_CREDITS = 60
ROLE_CREDITS = {"guest": GUEST_CREDITS, "member": MEMBER_CREDITS}

# Free: the ClickHouse query is cheap, and proposal and preview are entirely client-side.
COST_FIND_LINE = 0
COST_PROPOSE_CUT = 0
COST_PREVIEW = 0
# Paid: FFmpeg burns CPU, and ingest is Whisper CPU plus one Gemini call.
COST_RENDER = 1
COST_INGEST_PER_MINUTE = 1

# --- Abuse brakes ---
# Credits alone are not enough: a user can clear the cookie and take a new session.
MAX_SESSIONS_PER_IP_PER_HOUR = int(os.environ.get("MAX_SESSIONS_PER_IP_PER_HOUR", "30"))
MAX_CONCURRENT_RENDERS_PER_SESSION = 1
MAX_PHRASE_WORDS = 40          # stop query inflation

# The vocabulary panel is a wall of clickable chips. Past a couple of hundred it stops
# being scannable, so the cap is a readability limit as much as a query one — and the rows
# are ordered by how often each word is spoken, so a cut here drops the rare words rather
# than an arbitrary slice.
MAX_VOCABULARY = int(os.environ.get("MAX_VOCABULARY", "240"))

# The assistant is metered by count rather than credits: a turn is an LLM call, a
# different resource from the render and ingest that credits pay for. Leaving it free
# would publish an open LLM endpoint. The limit is wide enough to finish a demo
# comfortably and narrow enough to discourage abuse.
MAX_CHAT_MESSAGES = int(os.environ.get("MAX_CHAT_MESSAGES", "40"))
MAX_CHAT_MESSAGE_CHARS = 1000

# --- Uploads ---
# Transcription is CPU work with no upper bound of its own, so the brakes are on the input:
# a size cap, a duration cap, one ingest at a time per session, and a per-session take
# count so a visitor cannot fill the disk one small file at a time.
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))
MAX_UPLOAD_SECONDS = int(os.environ.get("MAX_UPLOAD_SECONDS", "180"))
MAX_CONCURRENT_INGESTS_PER_SESSION = 1
MAX_UPLOADS_PER_SESSION = int(os.environ.get("MAX_UPLOADS_PER_SESSION", "8"))

# Containers we accept. Not a security boundary — ffmpeg decides what it can actually read
# — but it turns "you sent a .zip" into an immediate answer instead of a failed job.
UPLOAD_SUFFIXES = frozenset({
    ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac",
    ".mp4", ".mov", ".mkv", ".webm", ".m4v",
})

# The transcription model used for uploads. `base` is multilingual; an .en model would make
# the product silently English-only. Overridable because a deployment with more CPU than
# this one may prefer `small`.
UPLOAD_MODEL = os.environ.get("UPLOAD_MODEL", "base")

# --- Storage ---
# The session ledger is SQLite. Cloud Run's filesystem is not durable, so guest sessions
# reset on restart — not a bug but accepted behaviour: a visitor gets a fresh session and
# a fresh allowance. The only thing that would need durability is member accounts, and
# those do not exist yet.
SESSION_DB = Path(os.environ.get("SESSION_DB", APP_ROOT / "scratch" / "sessions.db"))

# Render outputs. Pointed at /tmp inside the container: on Cloud Run the image layer
# should be treated as read-only, and the outputs are ephemeral anyway.
RENDER_DIR = Path(os.environ.get("RENDER_DIR", APP_ROOT / "scratch" / "renders"))

# The demo corpus media. NOT under scratch/, because this is not generated output but
# **content**: it has to be in the deployed image or a judge hears nothing. We do not
# commit what the code generates, but we do commit what the product shows.
MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", APP_ROOT / "demo" / "media"))

# Uploaded media, kept apart from the demo corpus for two reasons. The image layer should
# be treated as read-only on Cloud Run, so this points at /tmp there. And keeping visitor
# files out of the committed corpus means a stray upload can never end up in the repository.
#
# Ephemeral, like sessions and render outputs: a restart clears it. The durable version of
# this is object storage, which is the same piece of work as moving the demo corpus to GCS.
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", APP_ROOT / "scratch" / "uploads"))

# The marker a stored source_url carries so both the URL builder and the render path know
# which directory to resolve it in. Written into the data rather than guessed from the
# filename, because guessing from a filename is how traversal bugs start.
UPLOAD_URL_PREFIX = "uploads/"

# The demo corpus's ingest documents. The deployed instance fills ClickHouse from these,
# so search works out of the box.
DEMO_TAKES_DIR = Path(os.environ.get("DEMO_TAKES_DIR", APP_ROOT / "demo" / "takes"))

# The frontend. One origin: the same server serves both the API and the page.
#
# There is NO build step, so this points straight at the source directory. A deliberate
# decision: nothing in this interface is a problem a bundler solves, and in exchange the
# Cloud Run image loses its node stage and the npm supply chain entirely. Anyone reading
# the repository also sees the same file that gets served.
STATIC_DIR = Path(os.environ.get("STATIC_DIR", APP_ROOT / "web"))


def costs() -> dict[str, int]:
    """The price list exposed to the agent and the interface."""
    return {
        "find_line": COST_FIND_LINE,
        "propose_cut": COST_PROPOSE_CUT,
        "preview_segment": COST_PREVIEW,
        "commit_render": COST_RENDER,
        "ingest_per_minute": COST_INGEST_PER_MINUTE,
    }


def limits() -> dict:
    """Upload limits, so the interface can state them before a file is chosen."""
    return {
        "max_mb": MAX_UPLOAD_MB,
        "max_seconds": MAX_UPLOAD_SECONDS,
        "max_uploads": MAX_UPLOADS_PER_SESSION,
        "suffixes": sorted(UPLOAD_SUFFIXES),
    }
