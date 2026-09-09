"""API uçları. WebMCP araçlarının ve sayfa içi panelin arkası.

find_line LLM gerektirmiyor: cümle -> ClickHouse -> sıralı aday. Doğal dili araç
parametrelerine çeviren ADK ajanı bunun ÜSTÜNE biniyor, altına değil. Bu yüzden arama
Gemini anahtarı olmadan da tam çalışıyor.

Sıralama ürün mantığı, SQL'de değil burada: ton skoru yüksek olan önce. Ton filtresi
verildiğinde bu doğrudan "o tonun en iyi örneği önce" demek oluyor.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pipeline import queries, schema, search

from . import agent, ch, config, sessions
from . import render as render_worker

router = APIRouter(prefix="/api")

# Paylaşılan istemci server/ch.py'de: ajan araçları da aynısını kullanıyor.
clickhouse = ch.client
drop_client = ch.drop


def media_url(source_url: str) -> str:
    """Kayıt referansını oynatılabilir URL'e çevirir.

    Yerelde /media altından servis ediliyor ve StaticFiles HTTP range destekliyor —
    sanal kırpma oynatıcısının çalışması buna bağlı. Üretimde GCS signed URL.
    """
    if source_url.startswith(("http://", "https://", "/")):
        return source_url
    return f"/media/{source_url.rsplit('/', 1)[-1]}"


def match_id(match: dict) -> str:
    """Adayı referanslamak için kararlı kimlik. propose_cut bunları kullanıyor."""
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
        # provenance: fragment kaynağına geri gidebilsin
        "source_url": match["source_url"],
        "media_url": media_url(match["source_url"]),
    }


# --- Oturum ---


def current_session(request: Request, response: Response) -> dict:
    """Oturumu döner, yoksa oluşturur. Kullanıcı hiçbir şey yapmıyor."""
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
        httponly=True,      # sayfa JS'inin okumasına gerek yok, fetch otomatik gönderiyor
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return session


def session_payload(session: dict) -> dict:
    # Oturum kimliği DÖNMÜYOR. Çerezde duruyor, gövdede taşımanın faydası yok,
    # log'lara ve ekran görüntülerine sızma riski var.
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


# --- Arama ---


class FindLineRequest(BaseModel):
    phrase: str = Field(min_length=1, max_length=500)
    tone: str = ""
    project: str = config.DEMO_PROJECT
    limit: int = Field(default=20, ge=1, le=100)


def validate_project(project: str) -> str:
    # project_id istekten geliyor, allowlist dışına çıkmasın
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

    # Ürün mantığı: en iyi örnek önce. Ton skoru eşitse daha güvenli hizalama önce.
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
    return {"project": project, "stats": stats}


@router.get("/word/{word}")
def word_occurrences(
    word: str,
    request: Request,
    response: Response,
    project: str = config.DEMO_PROJECT,
    tone: str = "",
) -> dict:
    """Tek kelimenin geçtiği yerler. Kelime cımbızlama arayüzünün arkası."""
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
# Henüz uygulanmadı. Kredi düşürüp iş yapmamak yerine açıkça 503 dönüyor:
# çalışmayan bir şey için kredi harcamak sessiz veri kaybı olur.


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

    # Sıra önemli: DOĞRULAMA önce, kredi sonra. Reddedilen bir istek için kredi
    # düşürmek kullanıcının hatasını ona ödetmek olur.
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
            session["id"], config.COST_RENDER, f"render {job.id} ({job.segments} parça)"
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
    # Başka oturumun işini 404 olarak veriyoruz: var olduğunu bile söylemiyoruz
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

    # StaticFiles ile mount ETMİYORUZ: çıktılar oturuma ait, dizin listelenebilir
    # ya da kimliği bilen herkes tarafından indirilebilir olmamalı.
    return FileResponse(
        job.output,
        filename=f"roughcut-{job.id}{job.output.suffix}",
        media_type="video/mp4" if job.output.suffix == ".mp4" else "audio/wav",
    )


# --- Sayfa içi sohbet (ADK ajanı) ---
#
# Bu uç harici ajanın YERİNE geçmiyor, ajanı OLMAYAN kullanıcı için var. WebMCP
# araçları doğrudan API'ye gidiyor; ChatGPT gibi bir istemci zaten LLM olduğu için
# parametre eşleştirmesini ikinci bir modele yaptırmak gecikmeden başka bir şey
# eklemezdi. Ayrıntı: server/agent.py


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=config.MAX_CHAT_MESSAGE_CHARS)


@router.get("/chat/status")
def chat_status(request: Request, response: Response) -> dict:
    """Sohbet kullanılabilir mi. Arayüz kutuyu buna göre gösteriyor.

    `reason_code` makine okunur, `reason` insan okunur ve İngilizce. Sunucu
    kullanıcının dilini bilmiyor; istemci kodu görüp kendi dilinde yazıyor ve
    tanımadığı bir kod gelirse buradaki metne düşüyor.
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
