"""The API endpoints. What sits behind the WebMCP tools and the in-page panel.

find_line needs no LLM: phrase -> ClickHouse -> ranked candidates. The ADK agent that
turns natural language into these parameters sits ON TOP of this, not underneath, which
is why search works fully with no Gemini key.

Ranking is product logic and lives here rather than in the SQL: the highest delivery
confidence first. Given a tone filter that reads directly as "the best example of that
delivery first".
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pipeline import queries, schema, search

from . import agent, ch, config, sessions
from . import render as render_worker

router = APIRouter(prefix="/api")

# The shared client lives in server/ch.py, because the agent tools use the same one.
clickhouse = ch.client
drop_client = ch.drop


def media_url(source_url: str) -> str:
    """Turns a recording reference into a playable URL.

    Served locally from /media, and StaticFiles supports HTTP range, which the
    virtual-splice player depends on. In production this becomes a GCS signed URL.
    """
    if source_url.startswith(("http://", "https://", "/")):
        return source_url
    return f"/media/{source_url.rsplit('/', 1)[-1]}"


def match_id(match: dict) -> str:
    """A stable id for referring to a candidate. propose_cut works from these."""
    return f"{match['take_id']}:{match['line_id']}:{match['start_ms']}"


def to_candidate(match: dict, rank: int) -> dict:
    return {
        "id": match_id(match),
        "rank": rank,
        "take_id": match["take_id"],
        "line_id": match["line_id"],
        "scene": match["scene"],
        "camera": match["camera"],
        "speaker": match["speaker"],
        "tone": match["tone"],
        "tone_score": round(float(match["tone_score"]), 3),
        "start_ms": match["start_ms"],
        "end_ms": match["end_ms"],
        "duration_ms": match["end_ms"] - match["start_ms"],
        "text": match["text"],
        # provenance, so a fragment can be traced back to its source
        "source_url": match["source_url"],
        "media_url": media_url(match["source_url"]),
    }


# --- Session ---


def current_session(request: Request, response: Response) -> dict:
    """Returns the session, creating one if absent. The user does nothing."""
    existing = sessions.get(request.cookies.get(config.SESSION_COOKIE, ""))
    if existing:
        sessions.touch(existing["id"])
        return existing

    ip = request.client.host if request.client else ""
    if sessions.recent_sessions_from_ip(ip) >= config.MAX_SESSIONS_PER_IP_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail="Too many new sessions from this address. Try again shortly.",
        )

    session = sessions.create(ip=ip)
    response.set_cookie(
        config.SESSION_COOKIE,
        session["id"],
        max_age=config.SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,      # page JS has no need to read it; fetch sends it automatically
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return session


def session_payload(session: dict) -> dict:
    # The session id is NOT returned. It lives in the cookie; carrying it in a body buys
    # nothing and risks leaking into logs and screenshots.
    return {
        "role": session["role"],
        "credits": session["credits"],
        "costs": config.costs(),
    }


@router.get("/session")
def read_session(request: Request, response: Response) -> dict:
    session = current_session(request, response)
    return {
        "session": session_payload(session),
        "demo_project": config.DEMO_PROJECT,
        "tones": list(schema.TONES),
    }


# --- Search ---


class FindLineRequest(BaseModel):
    phrase: str = Field(min_length=1, max_length=500)
    tone: str = ""
    project: str = config.DEMO_PROJECT
    limit: int = Field(default=20, ge=1, le=100)


def validate_project(project: str) -> str:
    # project_id arrives in the request, so keep it inside the allowlist
    if project not in config.ALLOWED_PROJECTS:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project}")
    return project


def validate_tone(tone: str) -> str:
    if tone and tone not in schema.TONES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown tone: {tone}. Allowed: {', '.join(schema.TONES)}",
        )
    return tone


@router.post("/find_line")
def find_line(body: FindLineRequest, request: Request, response: Response) -> dict:
    session = current_session(request, response)
    project = validate_project(body.project)
    tone = validate_tone(body.tone)

    words = schema.normalize_phrase(body.phrase)
    if not words:
        raise HTTPException(status_code=422, detail="The phrase is empty.")
    if len(words) > config.MAX_PHRASE_WORDS:
        raise HTTPException(
            status_code=422,
            detail=f"The phrase is too long ({len(words)} words, at most "
            f"{config.MAX_PHRASE_WORDS}).",
        )

    try:
        matches = search.phrase_search(clickhouse(), project, body.phrase, tone)
    except Exception as error:
        drop_client()
        raise HTTPException(status_code=503, detail=f"Search failed: {error}")

    # Product logic: best example first. On a tie, the more confident alignment first.
    matches.sort(key=lambda m: (-float(m["tone_score"]), m["take_id"], m["start_ms"]))
    candidates = [
        to_candidate(match, rank)
        for rank, match in enumerate(matches[: body.limit], start=1)
    ]

    return {
        "phrase": " ".join(words),
        "tone": tone or None,
        "total": len(matches),
        "candidates": candidates,
        "session": session_payload(session),
    }


class LineRef(BaseModel):
    take_id: str = Field(min_length=1, max_length=120)
    line_id: int = Field(ge=0)


class LinesRequest(BaseModel):
    # Capped at the find_line ceiling: this only ever describes lines a search returned.
    lines: list[LineRef] = Field(min_length=1, max_length=100)
    project: str = config.DEMO_PROJECT


@router.post("/lines")
def read_lines(body: LinesRequest, request: Request, response: Response) -> dict:
    """The words of specific lines, with their timings.

    Behind the clickable transcript. `find_line` returns the matched range; this returns
    the sentence around it, so a word can be previewed on its own and a range can be
    picked by hand instead of taking the whole match.

    Read-only and free, like every other search endpoint. Charging for reading the
    transcript you are already looking at would be absurd.
    """
    current_session(request, response)
    project = validate_project(body.project)

    # De-duplicated: several candidates can be hits inside the same line, and asking
    # ClickHouse for it more than once buys nothing.
    pairs = sorted({(ref.take_id, ref.line_id) for ref in body.lines})

    try:
        lines = search.line_words(clickhouse(), project, pairs)
    except Exception as error:
        drop_client()
        raise HTTPException(status_code=503, detail=f"Query failed: {error}")

    return {
        "lines": {
            key: {
                "words": words,
                # The full sentence, which is what a phrase match does NOT give you
                "text": " ".join(word["word"] for word in words),
                "start_ms": words[0]["start_ms"],
                "end_ms": words[-1]["end_ms"],
            }
            for key, words in lines.items()
        }
    }


@router.get("/library/stats")
def library_stats(request: Request, response: Response, project: str = config.DEMO_PROJECT) -> dict:
    current_session(request, response)
    validate_project(project)
    try:
        result = clickhouse().query(
            queries.LIBRARY_STATS, parameters={"project": project}
        )
    except Exception as error:
        drop_client()
        raise HTTPException(status_code=503, detail=f"Query failed: {error}")

    stats = dict(zip(result.column_names, result.result_rows[0]))

    # A real line from this library, for the search box's example. It has to come from the
    # data: the interface used to carry an English sentence as a constant, which is a
    # misleading hint the moment the footage is in another language.
    try:
        sample = clickhouse().query(queries.SAMPLE_LINE, parameters={"project": project})
        stats["sample_line"] = sample.result_rows[0][0] if sample.result_rows else ""
    except Exception:
        # Cosmetic. An empty library or a failed query means no example, not no stats.
        stats["sample_line"] = ""

    return {"project": project, "stats": stats}


@router.get("/word/{word}")
def word_occurrences(
    word: str,
    request: Request,
    response: Response,
    project: str = config.DEMO_PROJECT,
    tone: str = "",
) -> dict:
    """Every occurrence of a single word. Behind the word-assembly interface."""
    current_session(request, response)
    validate_project(project)
    validate_tone(tone)
    try:
        rows = search.word_search(clickhouse(), project, word, tone)
    except Exception as error:
        drop_client()
        raise HTTPException(status_code=503, detail=f"Search failed: {error}")

    return {
        "word": schema.normalize_word(word),
        "total": len(rows),
        "occurrences": [
            {
                "take_id": row["take_id"],
                "line_id": row["line_id"],
                "camera": row["camera"],
                "speaker": row["speaker"],
                "tone": row["tone"],
                "start_ms": row["start_ms"],
                "end_ms": row["end_ms"],
                "duration_ms": row["end_ms"] - row["start_ms"],
                "confidence": round(float(row["confidence"]), 3),
                "word": row["word"],
                "source_url": row["source_url"],
                "media_url": media_url(row["source_url"]),
            }
            for row in rows
        ],
    }


# --- Render ---


class Segment(BaseModel):
    candidate_id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


class RenderRequest(BaseModel):
    segments: list[Segment] = Field(min_length=1, max_length=200)
    project: str = config.DEMO_PROJECT


@router.post("/render")
async def render(
    body: RenderRequest,
    request: Request,
    response: Response,
    background: BackgroundTasks,
) -> dict:
    session = current_session(request, response)
    project = validate_project(body.project)

    for segment in body.segments:
        if segment.end_ms <= segment.start_ms:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid range: {segment.start_ms}-{segment.end_ms}",
            )

    # Order matters: VALIDATE first, charge second. Taking a credit for a request we
    # rejected means making the user pay for their own mistake.
    try:
        job, steps, total = render_worker.enqueue(
            session["id"], project, [segment.model_dump() for segment in body.segments]
        )
    except render_worker.RenderRejected as error:
        raise HTTPException(status_code=422, detail=str(error))
    except Exception as error:
        drop_client()
        raise HTTPException(status_code=503, detail=f"Could not plan the render: {error}")

    try:
        remaining = sessions.charge(
            session["id"], config.COST_RENDER, f"render {job.id} ({job.segments} segments)"
        )
    except sessions.InsufficientCredits as error:
        job.status = "failed"
        job.error = "not enough credits"
        raise HTTPException(
            status_code=402,
            detail=(
                f"Rendering needs {error.needed} credit, balance is {error.balance}. "
                "Search, proposal and preview cost nothing and keep working."
            ),
        )

    background.add_task(render_worker.start, job, steps)

    session = sessions.get(session["id"]) or session
    return {
        **job.public(),
        "charged": config.COST_RENDER,
        "credits_left": remaining,
        "total_duration_ms": total,
        "session": session_payload(session),
    }


@router.get("/render/{job_id}")
def render_status(job_id: str, request: Request, response: Response) -> dict:
    session = current_session(request, response)
    job = render_worker.get(job_id)
    # Another session's job is a 404: we do not even confirm it exists
    if job is None or job.session_id != session["id"]:
        raise HTTPException(status_code=404, detail="No such render job.")
    return job.public()


@router.get("/render/{job_id}/file")
def render_file(job_id: str, request: Request, response: Response):
    session = current_session(request, response)
    job = render_worker.get(job_id)
    if job is None or job.session_id != session["id"]:
        raise HTTPException(status_code=404, detail="No such render job.")
    if job.status != "done" or job.output is None or not job.output.is_file():
        raise HTTPException(status_code=409, detail=f"The render is not ready: {job.status}")

    # NOT mounted as StaticFiles: outputs belong to a session, and the directory should
    # be neither listable nor downloadable by anyone who learns an id.
    return FileResponse(
        job.output,
        filename=f"roughcut-{job.id}{job.output.suffix}",
        media_type="video/mp4" if job.output.suffix == ".mp4" else "audio/wav",
    )


# --- In-page assistant (the ADK agent) ---
#
# This endpoint does not REPLACE the external agent, it serves the visitor who has NO
# agent. The WebMCP tools go straight to the API; a client like ChatGPT is already an LLM,
# so having a second model redo the parameter mapping would add nothing but latency.
# Detail: server/agent.py


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=config.MAX_CHAT_MESSAGE_CHARS)


@router.get("/chat/status")
def chat_status(request: Request, response: Response) -> dict:
    """Whether the assistant is available. The interface shows the box based on this.

    reason_code is machine-readable and reason is human-readable English. The server does
    not know the reader's language; the client sees the code and writes the sentence in
    its own, falling back to this text for a code it does not recognise.
    """
    session = current_session(request, response)
    available = agent.available()
    return {
        "available": available,
        "model": agent.MODEL if available else None,
        "messages_left": max(0, config.MAX_CHAT_MESSAGES - int(session.get("chat_used", 0))),
        "reason_code": None if available else "no_api_key",
        "reason": None
        if available
        else "No GEMINI_API_KEY on the server. The search panel, timeline and preview "
        "all work without the assistant.",
    }


@router.post("/chat")
async def chat(body: ChatRequest, request: Request, response: Response) -> dict:
    session = current_session(request, response)

    if not agent.available():
        raise HTTPException(
            status_code=503,
            detail=(
                "The assistant is off: no GEMINI_API_KEY on the server. The search "
                "panel, timeline, preview and provenance all work without it."
            ),
        )

    try:
        left = sessions.consume_chat(session["id"], config.MAX_CHAT_MESSAGES)
    except sessions.ChatLimitReached as limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"This session reached the assistant limit ({limit.limit} messages). "
                "The search panel and timeline keep working."
            ),
        )

    try:
        result = await agent.ask(session["id"], body.message)
    except agent.AgentUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"The assistant could not answer: {error}")

    return {
        **result,
        "messages_left": left,
        "session": session_payload(session),
    }
