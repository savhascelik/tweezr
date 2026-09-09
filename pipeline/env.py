"""Reads `.env` into the environment.

WHY THIS EXISTS
`.env.example` tells you to copy it to `.env` and fill it in, and nothing was reading the
result. So the documented way to supply a Gemini key silently did nothing: you set it,
`pipeline.tone` still reported no key, and the reasonable conclusion was that the feature
was broken. Anyone cloning this repository would hit the same wall.

Hand-written rather than `python-dotenv`, because it is fifteen lines and the server image
should not grow a dependency for local convenience.

THE ORDER MATTERS: a real environment variable always wins. In production the value comes
from Cloud Run's secrets, and a stale file must never be able to override it. `.env` is in
both `.gitignore` and `.dockerignore`, so it should not be in the image at all — this rule
is what makes that a defence in depth rather than the only defence.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent

_loaded = False


def load(path: Path | None = None) -> int:
    """Sets any variable the file defines and the environment does not. Returns how many.

    Idempotent, so it can be called from every entry point without coordinating them.
    """
    global _loaded
    if path is None:
        if _loaded:
            return 0
        _loaded = True
        path = APP_ROOT / ".env"

    if not path.is_file():
        return 0

    applied = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Quotes are how you keep a trailing space or a '#'; they are a quoting device and
        # not part of the value.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        # An empty assignment is how .env.example ships its keys. Treating it as a value
        # would mean a copied-but-unedited file masks a real environment variable.
        if not key or not value or key in os.environ:
            continue

        os.environ[key] = value
        applied += 1

    return applied
