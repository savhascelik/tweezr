"""The retrieval backbone: alignment, tone, ingest, search.

A package, because server/ uses the same contract and the same queries. Neither the
schema nor the SQL should exist in two places.

The CLIs run as modules, from the app/ root:

    python -m pipeline.transcribe ...
    python -m pipeline.ingest ...
    python -m pipeline.search --phrase "..."
    python -m pipeline.test_queries

`.env` is read here, on import, so every command and the server get it without each one
remembering to ask. A real environment variable still wins — see pipeline/env.py.
"""

from . import env as _env

_env.load()
