"""The FastAPI application. One origin: API, media and page from the same server.

That is not incidental, it is what WebMCP requires:

  1. Tools need a secure context.
  2. The Origin-Agent-Cluster: ?1 header is mandatory. Without it registerTool rejects
     with a SecurityError and offers no hint as to why. We learned that during a deploy
     on the previous project.
  3. With the session cookie on the same origin as the API, the session context the
     agent inherits just works, and no separate token flow is needed.

There is no CORS and there will not be: unnecessary on one origin, and enabling it would
only add attack surface.

SECURITY NOTE — this API is deliberately unauthenticated.
A mandatory login kills WebMCP discovery, see sessions.py. The brakes that pay for it:
credits per anonymous session, a per-IP session cap, a project allowlist, parameterised
queries with no string interpolation, and read-only endpoints everywhere. The only
endpoint that writes is render, and that one spends credit.
"""

from __future__ import annotations

import mimetypes

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import config, routes, sessions

# Python's mimetypes table has no entry for woff2, so StaticFiles would serve the
# self-hosted font with no Content-Type. Combined with the nosniff header below that is
# asking for trouble, and registering it is cheaper than debugging it later.
mimetypes.add_type("font/woff2", ".woff2")


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
        # Mandatory for WebMCP. Without it registerTool refuses without saying why.
        response.headers["Origin-Agent-Cluster"] = "?1"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        # Prevent aggressive browser caching of static scripts and markup
        path = request.url.path
        if path.endswith((".js", ".css", ".html")) or path in {"/", ""}:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    app.include_router(routes.router)

    # Media. StaticFiles supports HTTP range, which the virtual-splice player depends on.
    config.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/media",
        StaticFiles(directory=config.MEDIA_DIR),
        name="media",
    )

    # Uploaded media, mounted separately from the demo corpus. Two directories rather than
    # one because the image layer is read-only on Cloud Run and visitor files must not be
    # able to land in the committed corpus.
    #
    # NOT access controlled, and that is a real property worth stating: a visitor's upload
    # is reachable by anyone who knows the filename. The names carry eight random hex
    # characters so they are not guessable, and SEARCH is isolated per session so nobody
    # discovers them by looking. Proper object storage with signed URLs is the fix, and it
    # is the same piece of work as moving the demo corpus to GCS.
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/uploads",
        StaticFiles(directory=config.UPLOAD_DIR),
        name="uploads",
    )

    # The frontend. If it is absent we do not mount, so the API still works alone.
    if config.STATIC_DIR.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=config.STATIC_DIR, html=True),
            name="web",
        )

    return app


app = create_app()
