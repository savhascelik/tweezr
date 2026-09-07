"""FastAPI uygulaması. Tek origin: API, medya ve sayfa aynı sunucudan.

Tek origin tesadüf değil, WebMCP'nin gereği:

  1. Araçlar secure context istiyor.
  2. Origin-Agent-Cluster: ?1 header'ı ŞART. Yoksa registerTool SecurityError ile
     reddediyor ve hiçbir ipucu vermiyor. Geçen projede bunu deploy'da öğrendik.
  3. Session cookie'si ile API aynı origin'de olunca ajanın devraldığı oturum
     bağlamı doğrudan çalışıyor, ayrı token akışı gerekmiyor.

CORS yok ve olmayacak: aynı origin'de gerek yok, açmak sadece saldırı yüzeyi ekler.

GÜVENLİK NOTU — bu API kasten kimlik doğrulamasız:
Zorunlu giriş WebMCP keşfini öldürüyor (bkz. sessions.py). Karşılığında konan frenler:
anonim oturum başına kredi, IP başına oturum limiti, proje allowlist'i, parametreli
sorgular (string interpolasyon yok) ve sadece okuma yapan uçlar. Yazma yapan tek uç
render ve o da kredi düşürüyor.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, routes, sessions


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agentic Cinema retrieval API",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    sessions.init_db()

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        # WebMCP'nin çalışması için zorunlu. Eksikse registerTool sessizce reddediyor.
        response.headers["Origin-Agent-Cluster"] = "?1"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    app.include_router(routes.router)

    # Medya. StaticFiles HTTP range destekliyor; sanal kırpma oynatıcısı buna dayanıyor.
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/media",
        StaticFiles(directory=config.MEDIA_DIR),
        name="media",
    )

    # Frontend build çıktısı. Henüz yoksa mount etmiyoruz — API tek başına çalışsın.
    if config.STATIC_DIR.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=config.STATIC_DIR, html=True),
            name="web",
        )

    return app


app = create_app()
