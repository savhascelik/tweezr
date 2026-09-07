"""API uçları. WebMCP araçlarının ve sayfa içi panelin arkası.

find_line LLM gerektirmiyor: cümle -> ClickHouse -> sıralı aday. Doğal dili araç
parametrelerine çeviren ADK ajanı bunun ÜSTÜNE biniyor, altına değil. Bu yüzden arama
Gemini anahtarı olmadan da tam çalışıyor.

Sıralama ürün mantığı, SQL'de değil burada: ton skoru yüksek olan önce. Ton filtresi
verildiğinde bu doğrudan "o tonun en iyi örneği önce" demek oluyor.
"""

from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from pipeline import db as ch
from pipeline import queries, schema, search

from . import config, sessions

router = APIRouter(prefix="/api")

_client = None
_client_lock = threading.Lock()


def clickhouse():
    """Paylaşılan ClickHouse istemcisi. Hata olursa bir sonraki istekte yeniden kurulur."""
    global _client
    with _client_lock:
        if _client is None:
            _client = ch.connect()
        return _client


def drop_client() -> None:
    global _client
    with _client_lock:
        _client = None


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
            detail="Bu adresten çok fazla yeni oturum açıldı. Biraz sonra tekrar dene.",
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
        raise HTTPException(status_code=404, detail=f"Bilinmeyen proje: {project}")
    return project


def validate_tone(tone: str) -> str:
    if tone and tone not in schema.TONES:
        raise HTTPException(
            status_code=422,
            detail=f"Bilinmeyen ton: {tone}. Geçerli: {', '.join(schema.TONES)}",
        )
    return tone


@router.post("/find_line")
def find_line(body: FindLineRequest, request: Request, response: Response) -> dict:
    session = current_session(request, response)
    project = validate_project(body.project)
    tone = validate_tone(body.tone)

    words = schema.normalize_phrase(body.phrase)
    if not words:
        raise HTTPException(status_code=422, detail="Cümle boş.")
    if len(words) > config.MAX_PHRASE_WORDS:
        raise HTTPException(
            status_code=422,
            detail=f"Cümle çok uzun ({len(words)} kelime, en fazla "
            f"{config.MAX_PHRASE_WORDS}).",
        )

    try:
        matches = search.phrase_search(clickhouse(), project, body.phrase, tone)
    except Exception as error:
        drop_client()
        raise HTTPException(status_code=503, detail=f"Arama başarısız: {error}")

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
        raise HTTPException(status_code=503, detail=f"Sorgu başarısız: {error}")

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
        raise HTTPException(status_code=503, detail=f"Arama başarısız: {error}")

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
def render(body: RenderRequest, request: Request, response: Response) -> dict:
    session = current_session(request, response)
    validate_project(body.project)
    for segment in body.segments:
        if segment.end_ms <= segment.start_ms:
            raise HTTPException(
                status_code=422,
                detail=f"Geçersiz aralık: {segment.start_ms}-{segment.end_ms}",
            )

    raise HTTPException(
        status_code=503,
        detail=(
            "Render işçisi henüz devrede değil. Öneri, önizleme ve timeline oynatma "
            f"çalışıyor ve kredi harcamıyor. Bakiye: {session['credits']}."
        ),
    )
