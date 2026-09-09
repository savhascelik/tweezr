# server — the API and the single origin

FastAPI. The API, the media and the page are all served from one origin.

## Why one origin is not negotiable

Three WebMCP requirements meet here:

1. **Secure context** — Cloud Run gives us HTTPS.
2. **The `Origin-Agent-Cluster: ?1` header is mandatory.** Without it `registerTool`
   rejects with a `SecurityError` and offers no hint as to why. We learned that during a
   deploy on the previous project, which is why `server/test_api.py` asserts the header.
3. **The session cookie shares the API's origin.** The agent inherits the session
   context from the browser, so there is no separate token flow to build.

There is no CORS and there will not be: on one origin it is unnecessary, and enabling it
would only add attack surface.

## There is no login, and that is a design decision

Tools are registered by page JavaScript. If a judge opens the URL and hits a login wall,
the application's JS never runs, `registerTool` is never called, and the agent sees
**zero tools**. On top of that, OAuth redirect flows are fragile inside an
agent-driven browser.

So: an anonymous session on first load, with the user doing nothing.

**The cost of that is an unauthenticated API.** The brakes that pay for it:

| Brake | Where |
| --- | --- |
| Per-session credits, atomic deduction | `sessions.charge`, `BEGIN IMMEDIATE` |
| Hourly cap on new sessions per IP | `config.MAX_SESSIONS_PER_IP_PER_HOUR` |
| Project allowlist | `routes.validate_project` |
| Delivery allowlist | `routes.validate_tone` |
| Phrase length ceiling | `config.MAX_PHRASE_WORDS` |
| Parameterised queries, never string interpolation | `pipeline/queries.py` |
| Read-only endpoints | everything except render |

## Credits

In one sentence: **the impressive path is free, the expensive path is metered.**

| Operation | Credits |
| --- | --- |
| `find_line`, `propose_cut`, `preview_segment` | 0 |
| `commit_render` | 1 |
| Ingesting your own media | 1 per minute |

Search is free because a judge should be able to see what the product is worth without
hitting a wall. Credits are not revenue here, they are a brake against a surprise bill.

The ledger is SQLite. Cloud Run's filesystem is not durable, so guest sessions reset on
restart — not a bug but accepted behaviour: a visitor gets a fresh session and a fresh
allowance.

## Endpoints

| Endpoint | Job | Credits |
| --- | --- | --- |
| `GET /healthz` | health | - |
| `GET /api/session` | session, balance, price list (creates one if absent) | 0 |
| `POST /api/find_line` | phrase → ranked candidates | 0 |
| `GET /api/word/{word}` | every occurrence of a word (single-word assembly) | 0 |
| `GET /api/library/stats` | what the library holds | 0 |
| `POST /api/render` | queue an approved cut | 1 |
| `GET /api/render/{id}` | job status, owner only | 0 |
| `GET /api/render/{id}/file` | download the output, owner only | 0 |
| `GET /api/chat/status` | is the assistant on, how many messages left | 0 |
| `POST /api/chat` | in-page assistant (ADK) | counter |
| `GET /media/*` | media, HTTP range supported | 0 |

`find_line` **needs no LLM**: phrase in, ClickHouse out, ranked. The ADK agent that turns
natural language into these parameters sits on top of it. Which means search works
fully with no Gemini key.

Ranking is product logic and lives in `routes.py` rather than the SQL: the highest
delivery confidence first. Given a tone filter, that reads directly as "the best example
of that delivery first".

## Render

The single irreversible step and the only one that spends credit. On the browser side it
is never reached without human approval, see `web/src/approve.js`.

### The path to the file being cut does NOT come from the request

This is the most important decision here. The request carries only a `candidate_id`
(`take_id:line_id:start_ms`) and a time range. The media path is derived from
`source_url` in ClickHouse, resolved under `MEDIA_DIR`, and checked to actually be there.

Letting the client name a file would be path traversal — and every path handed to ffmpeg
is a readable file, so `../../.env` is a real exfiltration route. `resolve_media` takes
the basename *and* verifies the resolved path stays inside the allowed directory; the
test tries `../../.env`, `..\\..\\.env`, `/etc/passwd`, `..` and empty input.

The time range is validated too: it cannot fall outside the take's known duration in
ClickHouse, and total output cannot exceed ten minutes. Otherwise a single request could
burn hours of CPU.

### Order: validate first, charge second

Charging a credit for a request we rejected means making the user pay for their own
mistake. A test checks this separately: the balance is unchanged after an invalid call.

### The concat filter, not the demuxer

The demuxer wants every input to share codec and parameters, and different takes can come
from different recordings. The filter re-encodes and tolerates that, with inputs
normalised to a common sample rate.

Output container follows the inputs: MP4 if every source has a video stream, WAV
otherwise. Detection reads ffmpeg's own stream summary, since `imageio-ffmpeg` ships no
ffprobe. There is no silent fallback — the chosen mode is reported on the job.

### Outputs are NOT mounted as StaticFiles

`/api/render/{id}/file` checks the job belongs to the session and then serves the file.
Mounting would make the directory listable, or downloadable by anyone who learns an id.
Another session's job returns **404**, not 403: we do not confirm it exists.

## Why the ADK agent does not sit under WebMCP

The blueprint had "WebMCP `find_line` → ADK agent". That was wrong.

The external agent, ChatGPT's in-app browser, **is already an LLM**. It calls `find_line`
with structured parameters. Routing those through our agent as well means asking a second
model to redo a mapping the first one already did, which adds latency and failure surface
and nothing else. So the WebMCP tools go straight to the API, and **search works fully
without a Gemini key.**

The ADK agent is for something else: **the visitor with no agent.** Most people are not
browsing inside ChatGPT's in-app browser. Carrying our own assistant means the product is
usable in natural language on its own, and both entry points sit on the same ClickHouse
queries.

### Three tools, testable without an LLM

The functions in `agent_tools.py` are plain: `find_line`, `assemble_proposal`,
`get_library_stats`. They can be called without an LLM and the tests do exactly that.

Three things there are deliberate.

**The docstrings are the schema the agent sees.** ADK builds the tool definition from the
signature and the docstring, so that text is interface rather than documentation. Which
is why they describe **when to reach for** each tool more than what it does.

**Failures return dicts, not exceptions.** `{"error": ..., "allowed_tones": [...]}` is
something a model can read and correct; an exception would only tell it that something
broke.

**The proposal reaches the browser through a collector.** The agent runs server-side and
cannot touch the page store. The tool records its intent in a per-request `ContextVar`,
the response carries it back, and the page applies it through `store.applyAgentResult()` —
so the external agent and the in-page assistant change the same timeline the same way. A
module-level dict would have leaked one visitor's proposal into another's.

`config.DEMO_PROJECT` is fixed in the tools. That is a security property: the agent
cannot be talked into querying another project.

### With no key

The assistant closes and the product keeps working: search panel, timeline, preview and
provenance are all LLM-free. `/api/chat/status` states the reason and the interface shows
it. This is intentional — what the product does should be visible even in an environment
with no key.

### The assistant is metered by count, not credits

Credits pay for rendering and ingest. An assistant turn is an LLM call, a different
resource, and leaving it free would publish an open LLM endpoint. The
`MAX_CHAT_MESSAGES` (40) counter lives in the `chat_used` column, and `consume_chat`
takes the same `BEGIN IMMEDIATE` lock as the credit deduction — two concurrent messages
reading the same counter and both passing would make the limit meaningless.

## Running

```powershell
# from the app/ root
docker compose -f dev\docker-compose.yml up -d
.venv\Scripts\python.exe -m dev.load_demo
.venv\Scripts\python.exe -m uvicorn server.main:app --reload --port 8080
```

`http://127.0.0.1:8080/api/docs` serves the OpenAPI UI.

## Tests

```powershell
.venv\Scripts\python.exe -m server.test_api
```

101 tests: security headers, session creation and reuse, cookie flags, the session id
staying out of response bodies, input validation, media URL mapping, five hostile paths
through `resolve_media`, search ranking and provenance fields, a real render whose output
length matches the requested spans, cross-session isolation on both status and download,
402 with no job when credits are short, atomicity of both the credit ledger and the
assistant counter, what the assistant says with no key, and every path through the agent
tools without an LLM.

The atomicity tests fire two or three times the balance or limit concurrently and assert
exactly the limit succeeds. Without that, two concurrent renders could spend the same
credit twice.

The tests are hermetic: they write the contract fixture into an `__api_test__` project and
drop it afterwards. They used to assert against whatever happened to be in `demo`, and
three of them broke the moment `dev/seed_demo` replaced that data.
