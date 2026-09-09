"""The ADK agent — the page's own assistant.

Why it sits ON TOP of the WebMCP tools rather than underneath them:

The external agent, ChatGPT's in-app browser, is already an LLM. It calls find_line with
structured parameters. Routing that through our agent as well would mean asking a second
model to redo a mapping the first one already did, which adds latency and failure
surface and nothing else. So the WebMCP tools go straight to the API.

This agent is for something else: **the visitor with no agent.** Most people are not
browsing inside ChatGPT's in-app browser. Carrying our own assistant means the product
is usable in natural language on its own, and both sit on the SAME tools.

With no key the assistant is off but the product keeps working: search panel, timeline,
preview and provenance are all LLM-free. That is deliberate — a judge should see what
the product does even in an environment with no key.
"""

from __future__ import annotations

import os

from . import agent_tools

MODEL = os.environ.get("AGENT_MODEL", "gemini-2.5-flash")
APP_NAME = "cinema"

INSTRUCTION = """
You are the AI co-editor assistant inside Tweezr, a film, video and news rough-cut assembly tool.
The person you are collaborating with is an editor or director working with recorded dialogue takes.

Your job is to search dialogue, tweeze exact words, edit the timeline, control playback, and propose rough cuts.

Tools and capabilities:
- Searching: Use find_line to locate lines. Use the tone filter (calm, tense, angry, whisper, shouted, neutral) when the editor asks about performance or emotion.
- Library discovery: Use get_library_stats and get_vocabulary to discover available takes, speakers, and frequent words.
- Transcripts: Use get_line_transcript to inspect exact word timings, indices, and confidence before tweezing.
- Word Tweezing: Use tweeze_words to extract specific words from a line with millisecond precision and add them to the timeline (the namesake feature of Tweezr).
- Timeline manipulation:
  - Use get_timeline_state to see what is currently on the editor's timeline.
  - Use assemble_proposal to place a sequence of candidate takes on the timeline.
  - Use remove_segment to remove a specific clip by index.
  - Use reorder_timeline to change clip order.
  - Use swap_take to switch a take on the timeline for an alternative reading.
  - Use clear_timeline to reset the timeline.
- Playback & Audio:
  - Use preview_segment to play a specific candidate or clip out loud in the browser.
  - Use play_timeline to play the rough cut sequence.
  - Use stop_playback to pause or stop playing audio.
- Render & Export:
  - Use request_render when the editor wants to render or export the cut. This prompts the editor's approval dialog.

Keep replies concise, friendly, and in the editor's language (Turkish or English). Name take ids and tones. Never invent timestamps or takes you did not get from a tool.
""".strip()


class AgentUnavailable(RuntimeError):
    """No key, or ADK is not installed. The rest of the product is unaffected."""


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def available() -> bool:
    return bool(api_key())


_runner = None


def runner():
    """The ADK runner, built on first use."""
    global _runner
    if _runner is not None:
        return _runner

    if not available():
        raise AgentUnavailable(
            "GEMINI_API_KEY is not set, so the assistant is off. Search, timeline, "
            "preview and provenance all work without an LLM."
        )

    try:
        from google.adk.agents import Agent
        from google.adk.runners import InMemoryRunner
    except ImportError as error:
        raise AgentUnavailable(f"google-adk is not installed: {error}") from error

    agent = Agent(
        name="rough_cut_assistant",
        model=MODEL,
        description="Finds spoken lines in an editing library and proposes rough cuts.",
        instruction=INSTRUCTION,
        # Plain Python functions: ADK builds the schema from the signature and the
        # docstring, so those docstrings in agent_tools.py are the model's interface.
        tools=agent_tools.TOOLS,
    )
    _runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    return _runner


# Browser session -> ADK session. The conversation history lives in ADK's session service.
_adk_sessions: dict[str, str] = {}


async def adk_session_id(user_id: str) -> str:
    existing = _adk_sessions.get(user_id)
    if existing:
        return existing

    service = runner().session_service
    session = await service.create_session(app_name=APP_NAME, user_id=user_id)
    _adk_sessions[user_id] = session.id
    return session.id


async def ask(user_id: str, message: str, context: dict | None = None) -> dict:
    """Asks the agent a message. Returns the reply plus what the tools collected."""
    from google.genai import types

    active = runner()
    session_id = await adk_session_id(user_id)

    # Set client context for the request (timeline, candidates, etc.)
    agent_tools.set_client_context(context)

    # Per-request collector: the tools write candidates, proposal and actions into this
    collected = agent_tools.new_collection()

    reply_parts: list[str] = []
    async for event in active.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        if event.error_message:
            raise RuntimeError(event.error_message)
        if not event.content or not event.content.parts:
            continue
        # Only the final text is collected; intermediate streamed parts repeat it
        if event.is_final_response():
            for part in event.content.parts:
                if getattr(part, "text", None):
                    reply_parts.append(part.text)

    return {
        "reply": "".join(reply_parts).strip(),
        "tool_calls": collected["tool_calls"],
        "candidates": collected["candidates"],
        "proposal": collected["proposal"],
        "actions": collected.get("actions", []),
    }
