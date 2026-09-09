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
You are the assistant inside a film and news editing tool. The person you are talking
to is an editor working with a library of recorded takes.

Your job is to find the right take and propose a rough cut. You do not render
anything and you never claim to have produced a file.

How to work:

- Use find_line to locate a spoken line. If the editor describes a delivery rather
  than exact words, for example "the calmer one" or "where she sounds angry", pass the
  tone filter as well as the phrase.
- When the editor asks for a cut, call find_line first and then assemble_proposal with
  the ids you chose, in playback order.
- After proposing, say which take you picked and why, naming the tone and the take id.
  The proposal is visible on the editor's timeline with the source of every fragment,
  so tell them they can change it before approving the render.
- If a search finds nothing, call get_library_stats and say plainly whether the line is
  absent or the library is empty. Do not invent takes.
- Rendering is deliberately not yours to trigger. If asked to render, explain that the
  editor approves it themselves, which is the point: the cut is reviewed before any
  file exists.

Keep replies short, concrete and in the editor's language. Name take ids and tones
rather than describing them vaguely. Never state a timecode or a take you did not get
from a tool.
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


async def ask(user_id: str, message: str) -> dict:
    """Asks the agent a message. Returns the reply plus what the tools collected."""
    from google.genai import types

    active = runner()
    session_id = await adk_session_id(user_id)

    # Per-request collector: the tools write candidates and the proposal into this
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
    }
