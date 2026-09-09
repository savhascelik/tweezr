"""The ADK agent's tools. Plain functions that work without an LLM.

Two things here are deliberate.

**The docstrings are the schema the agent sees.** ADK builds the tool definition from the
signature and the docstring, so that text is interface rather than documentation. Which
is why they say WHEN to reach for a function more than what it does.

**The proposal reaches the browser through a collector.** The agent runs server-side and
cannot touch the page store. A tool records its intent in a per-request ContextVar, the
reply carries that to the browser, and the page applies it through the same
actions.propose() — so the external agent and the in-page assistant change the SAME store
the same way.
"""

from __future__ import annotations

import contextvars

from pipeline import queries, schema, search

from . import ch, config

# Per-request collector. A ContextVar so concurrent requests cannot mix; a
# module-level dict would leak one visitor's proposal into another's.
_collector: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "agent_collector", default=None
)


def new_collection() -> dict:
    """Sets up the per-request collector and returns it."""
    collection = {"candidates": [], "proposal": [], "tool_calls": []}
    _collector.set(collection)
    return collection


def collection() -> dict:
    current = _collector.get()
    if current is None:
        current = new_collection()
    return current


def _record(name: str, args: dict) -> None:
    collection()["tool_calls"].append({"name": name, "args": args})


def _candidate(match: dict, rank: int) -> dict:
    """The same shape as routes.to_candidate, so the page never has to tell them apart."""
    from . import routes

    return routes.to_candidate(match, rank)


def find_line(phrase: str, tone: str = "") -> dict:
    """Find every take in the editing library where a line was spoken.

    Returns the matches best first, each with its take id, camera, speaker, vocal
    delivery (tone) with a confidence score, exact millisecond range and source
    recording. Call this before proposing a cut. It costs nothing, so search freely.

    Args:
        phrase: The spoken words to look for, for example "I never asked for this".
        tone: Optional. Keep only takes delivered this way. One of neutral, calm,
            tense, angry, whisper, shouted. Use it when the request is about
            performance rather than words, such as a calmer or angrier reading.
    """
    _record("find_line", {"phrase": phrase, "tone": tone})

    words = schema.normalize_phrase(phrase or "")
    if not words:
        return {"error": "phrase was empty after normalisation; pass the words to look for"}
    if len(words) > config.MAX_PHRASE_WORDS:
        return {"error": f"phrase too long: {len(words)} words, max {config.MAX_PHRASE_WORDS}"}
    if tone and tone not in schema.TONES:
        return {
            "error": f"unknown tone {tone!r}",
            "allowed_tones": list(schema.TONES),
        }

    try:
        matches = search.hybrid_search(
            ch.client(), config.DEMO_PROJECT, phrase, tone, limit=config.MAX_PHRASE_WORDS
        )
    except Exception as error:
        ch.drop()
        return {"error": f"library search failed: {error}"}

    candidates = [
        _candidate(match, rank) for rank, match in enumerate(matches, start=1)
    ]

    # The page puts these in the store, so the user sees what the agent found
    collection()["candidates"] = candidates

    if not candidates:
        return {
            "found": 0,
            "message": f"No take contains {' '.join(words)!r}"
            + (f" delivered {tone}" if tone else "")
            + ". Try get_library_stats to see what the library holds.",
        }

    return {
        "found": len(candidates),
        "candidates": [
            {
                "id": item["id"],
                "take": item["take_id"],
                "camera": item["camera"],
                "speaker": item["speaker"],
                "tone": item["tone"],
                "tone_score": item["tone_score"],
                "duration_ms": item["duration_ms"],
                "text": item["text"],
                "source": item["source_url"],
            }
            for item in candidates
        ],
    }


def assemble_proposal(candidate_ids: list[str]) -> dict:
    """Propose a rough cut on the editor's timeline, in the order given.

    Nothing is rendered and nothing is spent. The editor sees the proposal over the
    real footage with the source and timecode of every fragment, and can reorder,
    drop or reject it before anything is produced. Use candidate ids returned by
    find_line in this same conversation.

    Args:
        candidate_ids: Candidate ids from find_line, in playback order. Repeats allowed.
    """
    _record("assemble_proposal", {"candidate_ids": candidate_ids})

    if not candidate_ids:
        return {"error": "candidate_ids was empty; call find_line and pass the ids you want"}

    known = {item["id"]: item for item in collection()["candidates"]}
    if not known:
        return {"error": "no candidates in this conversation yet; call find_line first"}

    missing = [cid for cid in candidate_ids if cid not in known]
    if missing:
        return {
            "error": f"unknown candidate ids: {missing}",
            "available_ids": list(known),
        }

    collection()["proposal"] = list(candidate_ids)
    segments = [known[cid] for cid in candidate_ids]
    total = sum(item["duration_ms"] for item in segments)

    return {
        "proposed_segments": len(segments),
        "total_duration_ms": total,
        "rendered": False,
        "message": (
            f"Proposed a {len(segments)}-segment cut of {total / 1000:.2f}s on the "
            "editor's timeline. Nothing was rendered. Tell the editor what you chose "
            "and why, and that they can change it before approving the render."
        ),
        "segments": [
            {
                "id": item["id"],
                "take": item["take_id"],
                "tone": item["tone"],
                "start_ms": item["start_ms"],
                "end_ms": item["end_ms"],
                "text": item["text"],
                "source": item["source_url"],
            }
            for item in segments
        ],
    }


def get_library_stats() -> dict:
    """Report what the editing library contains: takes, words, vocabulary, tones.

    Useful before searching, and when a search finds nothing and you need to tell the
    editor whether the line is missing or the library is empty.
    """
    _record("get_library_stats", {})
    try:
        result = ch.client().query(
            queries.LIBRARY_STATS, parameters={"projects": [config.DEMO_PROJECT]}
        )
    except Exception as error:
        ch.drop()
        return {"error": f"library query failed: {error}"}

    stats = dict(zip(result.column_names, result.result_rows[0]))
    # ClickHouse returns an array type; make it JSON-serialisable
    stats["tones"] = list(stats.get("tones") or [])
    return stats


TOOLS = [find_line, assemble_proposal, get_library_stats]
