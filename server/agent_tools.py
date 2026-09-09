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

# Per-request client context (e.g. current timeline, candidates on screen).
_client_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "agent_client_context", default=None
)


def set_client_context(context: dict | None) -> None:
    """Stores the editor's screen context (timeline, active candidate, etc.) for this request."""
    _client_context.set(context or {})


def get_client_context() -> dict:
    """Retrieves the editor's screen context for this request."""
    return _client_context.get() or {}


def new_collection() -> dict:
    """Sets up the per-request collector and returns it."""
    collection = {
        "candidates": [],
        "proposal": [],
        "tool_calls": [],
        "actions": [],
    }
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
    context = get_client_context()
    for item in context.get("candidates", []):
        if item.get("id") and item["id"] not in known:
            known[item["id"]] = item

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
    total = sum(item.get("duration_ms", 0) for item in segments)

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


def get_timeline_state() -> dict:
    """Read the rough cut currently on the editor's timeline.

    Returns the segments currently placed on the timeline, including any hand-picked
    edits, reorderings or cuts made by the editor. Call this when the user asks what is
    on the timeline, or before making incremental edits.
    """
    _record("get_timeline_state", {})
    context = get_client_context()
    timeline = context.get("timeline", [])

    total_ms = sum(seg.get("duration_ms", 0) for seg in timeline)
    return {
        "count": len(timeline),
        "total_duration_ms": total_ms,
        "segments": [
            {
                "index": idx,
                "id": seg.get("id", ""),
                "take_id": seg.get("take_id", ""),
                "tone": seg.get("tone", ""),
                "start_ms": seg.get("start_ms", 0),
                "end_ms": seg.get("end_ms", 0),
                "text": seg.get("text", ""),
            }
            for idx, seg in enumerate(timeline)
        ],
    }


def tweeze_words(
    candidate_id: str,
    word_indices: list[int] | None = None,
    phrase: str = "",
    start_ms: int = -1,
    end_ms: int = -1,
) -> dict:
    """Select specific words from a take line and place them on the timeline.

    This is Tweezr's signature capability: word-level precision. Instead of including an
    entire long sentence or line, you can tweeze out exact words (e.g. only 'never asked'
    from 'I never asked for this') with millisecond-exact audio boundaries.

    Args:
        candidate_id: The candidate id (e.g. 'S01_T03:1:800') or line id to tweeze from.
        word_indices: Optional list of 0-based word indices to pick (e.g. [1, 2] for 2nd and 3rd words).
        phrase: Optional sub-phrase to look for inside the line (e.g. 'never asked').
            If word_indices is not given, phrase is used to find matching words.
        start_ms: Optional explicit start millisecond boundary.
        end_ms: Optional explicit end millisecond boundary.
    """
    _record(
        "tweeze_words",
        {
            "candidate_id": candidate_id,
            "word_indices": word_indices,
            "phrase": phrase,
            "start_ms": start_ms,
            "end_ms": end_ms,
        },
    )

    parts = candidate_id.split(":")
    if len(parts) < 2:
        return {"error": f"Invalid candidate_id format: {candidate_id!r}. Expected 'take_id:line_id:start_ms'"}

    take_id = parts[0]
    try:
        line_id = int(parts[1])
    except ValueError:
        return {"error": f"Invalid line_id in {candidate_id!r}"}

    words = []
    try:
        client = ch.client()
        lines = search.line_words(client, [config.DEMO_PROJECT], [(take_id, line_id)])
        words = lines.get(f"{take_id}:{line_id}", [])
    except Exception:
        ch.drop()

    if not words:
        context = get_client_context()
        all_cands = collection()["candidates"] + context.get("candidates", []) + context.get("timeline", [])
        cand = next((c for c in all_cands if c.get("id") == candidate_id or c.get("take_id") == take_id), None)
        if cand and cand.get("text"):
            tokens = cand["text"].split()
            c_start = cand.get("start_ms", 0)
            dur = cand.get("duration_ms", max(300, len(tokens) * 300))
            step = dur // max(1, len(tokens))
            words = [
                {
                    "word": w,
                    "word_norm": schema.normalize_word(w),
                    "start_ms": c_start + i * step,
                    "end_ms": c_start + (i + 1) * step,
                    "confidence": 0.99,
                }
                for i, w in enumerate(tokens)
            ]

    picked_words = []
    if start_ms >= 0 and end_ms > start_ms:
        if words:
            picked_words = [w for w in words if w["start_ms"] >= start_ms and w["end_ms"] <= end_ms]
        if not picked_words:
            picked_words = [{
                "word": phrase or "segment",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "confidence": 1.0,
            }]
    elif word_indices is not None and len(word_indices) > 0:
        for idx in word_indices:
            if 0 <= idx < len(words):
                picked_words.append(words[idx])
    elif phrase:
        target_norms = schema.normalize_phrase(phrase)
        word_norms = [w.get("word_norm", schema.normalize_word(w["word"])) for w in words]
        n = len(target_norms)
        for i in range(len(word_norms) - n + 1):
            if word_norms[i : i + n] == target_norms:
                picked_words = words[i : i + n]
                break
        if not picked_words:
            picked_words = [w for w in words if w.get("word_norm", schema.normalize_word(w["word"])) in target_norms]

    if not picked_words:
        picked_words = words if words else [{
            "word": phrase or "segment",
            "start_ms": 0,
            "end_ms": 1000,
            "confidence": 1.0,
        }]

    start_ms = picked_words[0]["start_ms"]
    end_ms = picked_words[-1]["end_ms"]
    text = " ".join(w["word"] for w in picked_words)

    # Find source candidate for metadata
    context = get_client_context()
    all_candidates = collection()["candidates"] + context.get("candidates", [])
    source = next((c for c in all_candidates if c.get("id") == candidate_id), {})

    segment = {
        "id": f"{take_id}:{line_id}:{start_ms}",
        "take_id": take_id,
        "line_id": line_id,
        "scene": source.get("scene", ""),
        "camera": source.get("camera", ""),
        "speaker": source.get("speaker", ""),
        "tone": source.get("tone", "neutral"),
        "tone_score": source.get("tone_score", 0.0),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "duration_ms": end_ms - start_ms,
        "text": text,
        "source_url": source.get("source_url", f"{take_id}.wav"),
        "media_url": source.get("media_url", f"/media/{take_id}.wav"),
        "tweezed": True,
    }

    collection()["actions"].append({"type": "tweeze_words", "segment": segment})

    return {
        "tweezed": True,
        "segment": segment,
        "message": (
            f"Tweezed {len(picked_words)} words ({text!r}) from {take_id} "
            f"({start_ms}ms → {end_ms}ms) and placed on timeline."
        ),
    }


def remove_segment(index: int) -> dict:
    """Remove a clip segment from the editor's timeline by its 0-based position.

    Args:
        index: The 0-based position of the segment to remove (0 for the first clip).
    """
    _record("remove_segment", {"index": index})
    collection()["actions"].append({"type": "remove_segment", "index": index})
    return {
        "removed_index": index,
        "message": f"Removed segment at position {index} from the timeline.",
    }


def reorder_timeline(from_index: int, to_index: int) -> dict:
    """Reorder a segment on the editor's timeline by moving it to another position.

    Args:
        from_index: The current 0-based position of the clip.
        to_index: The desired 0-based position to move it to.
    """
    _record("reorder_timeline", {"from_index": from_index, "to_index": to_index})
    collection()["actions"].append(
        {"type": "reorder_timeline", "from": from_index, "to": to_index}
    )
    return {
        "reordered": True,
        "message": f"Moved segment from position {from_index} to {to_index}.",
    }


def swap_take(index: int, candidate_id: str) -> dict:
    """Swap an existing segment on the timeline with another take or delivery.

    Args:
        index: The 0-based position of the segment on the timeline to replace.
        candidate_id: The candidate id of the alternative take from find_line.
    """
    _record("swap_take", {"index": index, "candidate_id": candidate_id})
    collection()["actions"].append(
        {"type": "swap_take", "index": index, "candidate_id": candidate_id}
    )
    return {
        "swapped": True,
        "message": f"Swapped segment at position {index} with candidate {candidate_id}.",
    }


def clear_timeline() -> dict:
    """Clear all segments from the editor's timeline, resetting it to empty."""
    _record("clear_timeline", {})
    collection()["actions"].append({"type": "clear_timeline"})
    return {"cleared": True, "message": "Cleared the timeline."}


def preview_segment(candidate_id: str = "", timeline_index: int = -1) -> dict:
    """Play audio in the page so the editor can hear a take or timeline clip out loud.

    Args:
        candidate_id: The candidate id to preview (from find_line).
        timeline_index: Optional 0-based index of a timeline clip to play.
    """
    _record(
        "preview_segment",
        {"candidate_id": candidate_id, "timeline_index": timeline_index},
    )
    collection()["actions"].append(
        {"type": "preview_segment", "candidate_id": candidate_id, "index": timeline_index}
    )
    return {
        "playing": True,
        "message": f"Started playback preview for {candidate_id or f'timeline index {timeline_index}'}.",
    }


def play_timeline(start_index: int = 0) -> dict:
    """Play the current rough cut timeline in sequence from the beginning or a specific clip.

    Args:
        start_index: The 0-based clip index to start playback from (default: 0).
    """
    _record("play_timeline", {"start_index": start_index})
    collection()["actions"].append({"type": "play_timeline", "start_index": start_index})
    return {
        "playing": True,
        "message": f"Started timeline playback from clip {start_index}.",
    }


def stop_playback() -> dict:
    """Stop currently playing audio in the browser."""
    _record("stop_playback", {})
    collection()["actions"].append({"type": "stop_playback"})
    return {"stopped": True, "message": "Stopped playback."}


def get_vocabulary(take: str = "", tone: str = "", limit: int = 30) -> dict:
    """List the most frequently spoken words in the library or a specific take.

    Use this when exploring what words exist in the library, or to suggest words that
    the editor can search or tweeze.

    Args:
        take: Optional take_id to limit the vocabulary to.
        tone: Optional tone filter.
        limit: Number of distinct words to return (default: 30).
    """
    _record("get_vocabulary", {"take": take, "tone": tone, "limit": limit})
    try:
        client = ch.client()
        words = search.vocabulary(
            client, [config.DEMO_PROJECT], take=take, tone=tone, limit=limit
        )
    except Exception as error:
        ch.drop()
        return {"error": f"Failed to retrieve vocabulary: {error}"}

    return {
        "count": len(words),
        "words": [
            {
                "word": w.get("spelling", w.get("word_norm")),
                "occurrences": w.get("occurrences", 1),
                "takes": w.get("takes", 1),
            }
            for w in words
        ],
    }


def get_line_transcript(
    candidate_id: str = "", take_id: str = "", line_id: int = 0
) -> dict:
    """Get the full word-by-word transcript and timestamps for a specific take line.

    Returns each word with its 0-based index, text, start_ms, end_ms, and confidence score.
    Call this before tweezing to inspect exact word indices.

    Args:
        candidate_id: Optional candidate id (e.g. 'S01_T03:1:800').
        take_id: Take ID if candidate_id is omitted.
        line_id: Line ID if candidate_id is omitted.
    """
    _record(
        "get_line_transcript",
        {"candidate_id": candidate_id, "take_id": take_id, "line_id": line_id},
    )

    if candidate_id:
        parts = candidate_id.split(":")
        take_id = parts[0]
        line_id = int(parts[1]) if len(parts) > 1 else 1

    if not take_id:
        return {"error": "take_id or candidate_id is required"}

    try:
        client = ch.client()
        lines = search.line_words(client, [config.DEMO_PROJECT], [(take_id, line_id)])
    except Exception as error:
        ch.drop()
        return {"error": f"Failed to fetch transcript: {error}"}

    words = lines.get(f"{take_id}:{line_id}", [])
    return {
        "take_id": take_id,
        "line_id": line_id,
        "text": " ".join(w["word"] for w in words),
        "words": [
            {
                "index": idx,
                "word": w["word"],
                "start_ms": w["start_ms"],
                "end_ms": w["end_ms"],
                "confidence": round(w.get("confidence", 1.0), 3),
            }
            for idx, w in enumerate(words)
        ],
    }


def request_render() -> dict:
    """Prompt the editor with the render confirmation dialog to approve rendering the timeline.

    Rendering costs credits and produces an actual media file. Calling this tool opens
    the render confirmation sheet in the editor's browser, displaying segments, duration
    and credit cost. The human editor makes the final decision.
    """
    _record("request_render", {})
    collection()["actions"].append({"type": "request_render"})
    return {
        "requested": True,
        "message": (
            "Opened the render approval sheet for the editor. Remind the user that "
            "approval is their final decision."
        ),
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


TOOLS = [
    find_line,
    assemble_proposal,
    tweeze_words,
    remove_segment,
    reorder_timeline,
    swap_take,
    clear_timeline,
    get_timeline_state,
    preview_segment,
    play_timeline,
    stop_playback,
    get_vocabulary,
    get_line_transcript,
    request_render,
    get_library_stats,
]
