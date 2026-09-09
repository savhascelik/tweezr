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
| `POST /api/lines` | the words of given lines, with timings (clickable transcript) | 0 |
| `GET /api/word/{word}` | every occurrence of a word (single-word assembly) | 0 |
| `GET /api/vocabulary` | every word in the library, and the takes it spans | 0 |
| `GET /api/library/stats` | what the library holds | 0 |
| `GET /api/upload/status` | are uploads on, and the limits | 0 |
| `POST /api/upload` | bring your own audio or video | 1 per started minute |
| `GET /api/upload/{id}` | ingest progress, owner only | 0 |
| `POST /api/render` | queue an approved cut | 1 |
| `GET /api/render/{id}` | job status, owner only | 0 |
| `GET /api/render/{id}/file` | download the output, owner only | 0 |
| `GET /api/chat/status` | is the assistant on, how many messages left | 0 |
| `POST /api/chat` | in-page assistant (ADK) | counter |
| `GET /media/*` | media, HTTP range supported | 0 |

`find_line` **needs no LLM**: phrase in, ClickHouse out, ranked. The ADK agent that turns
natural language into these parameters sits on top of it. Which means search works
fully with no Gemini key.

`POST /api/lines` is what makes the transcript clickable. `find_line` returns the matched
range; this returns the sentence around it, so a single word can be previewed and a range
picked by hand. It is batched over `(take_id, line_id)` pairs rather than one request per
line, because a search returns up to fifty candidates and fifty round trips to answer one
question is the wrong shape. Reading the transcript you are already looking at costs
nothing, and a test asserts the balance does not move.

Ranking is product logic and lives in `routes.py` rather than the SQL: the highest
delivery confidence first. Given a tone filter, that reads directly as "the best example
of that delivery first".

## Vocabulary

`GET /api/vocabulary` is the answer to a complaint that was entirely fair: you upload a
recording, the take counter moves, and nothing else on the page changes. A counter is not
an answer to "what is in my footage". A search-first interface is fine for a corpus you
already know and a memory test on footage you just brought in, because the transcriber does
not always hear what you said — and a search that comes back empty then reads as a broken
product rather than a wrong guess.

So the endpoint returns every distinct word, ordered by how often it is spoken, plus one
row per take for the interface's scope selector. `?take=` narrows it to one recording, which
is what the interface asks for straight after an ingest so the words that just arrived are
visible on their own rather than diluted into everything else.

Three decisions worth knowing:

- **The tone filter applies here too.** Without it a chip could report five occurrences and
  return nothing when clicked, because the delivery filter excluded all five.
- **The display spelling is cleaned but keeps its case** (`schema.display_word`). The stored
  spelling can end a sentence, and a chip reading `go.` next to a search box that fills with
  `go.` looks like a defect. The invariant the panel rests on is that a cleaned spelling
  normalises back to the key it was listed under, so clicking a chip cannot come back empty.
  `pipeline/test_queries.py` and `server/test_api.py` both pin it, the second by actually
  searching for every word the endpoint returned.
- **An unknown `take` needs no validation.** The project filter is what confines the read,
  so a take id belonging to another session matches nothing rather than leaking anything.

`limit` is clamped rather than rejected: it is a display limit, and a client asking for more
than we will draw is not worth failing a page load over.

There is deliberately **no agent tool for this**. The agent's job is phrase search; handing
it the corpus vocabulary would pad a tool result with hundreds of words to answer a question
it was not asked. `get_library_stats` already reports the vocabulary *size*.

## Uploads

A library a visitor cannot add to is a demo of itself, so `POST /api/upload` takes audio or
video in any language and puts it in. It mirrors render's shape — an in-memory job, a
background thread, a status endpoint the client polls — because Whisper on CPU takes
seconds to minutes and inventing a second job mechanism would only mean two things to
reason about.

**Uploads are isolated per session.** The take goes into a project derived from the session
cookie, and search spans two projects: the shared demo corpus plus the caller's own. The
upload project id is not something a caller can supply, which is what makes the isolation
hold rather than depending on a validation rule. `dev/check_deploy` and the API tests both
assert a second session does not see the first one's take.

What is validated, in order, before anything is charged:

- The container extension, before a byte is stored
- The concurrency and per-session count, so a hundred megabytes are not streamed to disk
  before finding out the answer is no
- The size, **while streaming** rather than from `Content-Length`: a client can claim any
  length, and by the time the lie is obvious the disk is full
- The duration, measured from the real file with ffmpeg

The stored filename is generated, never the one the browser sent — a browser will happily
send a name with slashes in it. The take id is sanitised down to ASCII for the same reason:
it ends up in URLs and in ClickHouse.

Priced per **started** minute, so a twenty second clip costs one credit. And an ingest that
fails after being charged is refunded, because a silent track or a container ffmpeg cannot
read is nobody's fault. `sessions.refund` writes a positive ledger row rather than
rewriting the balance, so the history still shows what happened.

Uploaded media lives in `UPLOAD_DIR`, apart from the demo corpus, mounted at `/uploads`.
Two directories because the image layer is read-only on Cloud Run and visitor files must
not be able to reach the committed repository. **That mount is not access controlled**: an
upload is reachable by anyone who knows the filename. The names carry eight random hex
characters so they are not guessable, and search is isolated so nobody discovers them by
looking, but signed URLs on object storage are the real fix and it is the same work as
moving the demo corpus to GCS.

### Transcription is optional, and says so

faster-whisper lives in `requirements-ingest.txt`. The deployed image installs it — that is
a reversal of the earlier decision, and it costs about 260 MB with the model baked in — but
a server without it still runs the whole product. `GET /api/upload/status` reports
`available: false` with a reason, and the interface disables the control and explains why
rather than accepting a file and failing a minute later.

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

138 tests: security headers, session creation and reuse, cookie flags, the session id
staying out of response bodies, input validation, media URL mapping, five hostile paths
through `resolve_media`, search ranking and provenance fields, line words including
duplicate pairs collapsing and an unknown line being absent rather than an error, a real
render whose output length matches the requested spans, cross-session isolation on both
status and download, 402 with no job when credits are short, atomicity of both the credit
ledger and the assistant counter, what the assistant says with no key, and every path
through the agent tools without an LLM.

On uploads specifically: the limits being stated before a file is chosen, an unknown
container refused, someone else's job reported as absent rather than forbidden, the
per-started-minute pricing at four boundaries, a take id surviving `../../etc`, the stored
filename being generated rather than the browser's, the upload project coming from the
session id and not being in the queryable allowlist, and a refund landing as a ledger row.

The atomicity tests fire two or three times the balance or limit concurrently and assert
exactly the limit succeeds. Without that, two concurrent renders could spend the same
credit twice.

The tests are hermetic: they write the contract fixture into an `__api_test__` project and
drop it afterwards. They used to assert against whatever happened to be in `demo`, and
three of them broke the moment `dev/seed_demo` replaced that data.
