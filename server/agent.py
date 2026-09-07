"""ADK ajanı — sayfanın kendi asistanı.

Neden WebMCP araçlarının ALTINDA değil ÜSTÜNDE:

Harici ajan (ChatGPT in-app browser) zaten bir LLM. `find_line`'ı yapılandırılmış
parametrelerle çağırıyor. Onu bir de bizim ajanımızdan geçirmek, ilk modelin çoktan
yaptığı parametre eşleştirmesini ikinci bir modele yaptırmak olurdu — gecikme ve hata
yüzeyinden başka bir şey eklemez. O yüzden WebMCP araçları doğrudan API'ye gidiyor.

Bu ajanın işi başka: **ajanı olmayan kullanıcı.** Çoğu insan ChatGPT in-app browser'da
gezmiyor. Sayfa kendi asistanını taşıyınca ürün harici bir ajan olmadan da doğal dille
kullanılabiliyor, ve ikisi AYNI araçların üstünde çalışıyor.

Anahtar yoksa sohbet devre dışı ama ürün çalışmaya devam ediyor: arama paneli, timeline,
önizleme ve provenance hepsi LLM'siz. Bu bilinçli — jüri anahtarsız bir ortamda bile
ürünün ne yaptığını görebilmeli.
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
    """Anahtar yok ya da ADK kurulu değil. Ürünün geri kalanı etkilenmiyor."""


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def available() -> bool:
    return bool(api_key())


_runner = None


def runner():
    """ADK runner'ı. İlk kullanımda kuruluyor."""
    global _runner
    if _runner is not None:
        return _runner

    if not available():
        raise AgentUnavailable(
            "GEMINI_API_KEY tanımlı değil, sohbet devre dışı. Arama paneli, timeline, "
            "önizleme ve provenance LLM olmadan çalışıyor."
        )

    try:
        from google.adk.agents import Agent
        from google.adk.runners import InMemoryRunner
    except ImportError as error:
        raise AgentUnavailable(f"google-adk kurulu değil: {error}") from error

    agent = Agent(
        name="rough_cut_assistant",
        model=MODEL,
        description="Finds spoken lines in an editing library and proposes rough cuts.",
        instruction=INSTRUCTION,
        # Düz Python fonksiyonları: ADK şemayı imza ve docstring'den üretiyor,
        # yani agent_tools.py'deki docstring'ler modelin gördüğü arayüz.
        tools=agent_tools.TOOLS,
    )
    _runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    return _runner


# Tarayıcı oturumu -> ADK oturumu. Sohbet geçmişi ADK'nın session service'inde.
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
    """Ajana bir mesaj sorar. Cevabı ve araçların topladığı yan etkileri döner."""
    from google.genai import types

    active = runner()
    session_id = await adk_session_id(user_id)

    # İstek başına toplayıcı: araçlar adayları ve öneriyi buraya yazıyor
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
        # Sadece nihai metni topluyoruz; ara akış parçaları tekrar üretiyor
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
