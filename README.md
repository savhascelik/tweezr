# Tweezr

**Word tweezing and dialogue assembly.** Find a spoken line down to the word, rank the
takes by how they were delivered, and assemble the cut without rendering anything.

A shooting day turns a forty minute scene into six hours of footage. The same line gets
recorded twelve times, in different deliveries, from different angles. Then in the
edit room somebody asks:

> "Which take reads 'I never asked for this' more calmly?"

Today's answer is patchy script supervisor notes or hours of scrubbing.

Type a line into your library and this tool finds which recording said it and at which
millisecond, ranks the takes by how they were delivered, and proposes the rough cut on
the timeline **without rendering anything**. Under every fragment: which recording,
which timecode.

## What works today

This repository is mid-build. As of now, **working and tested**:

- Word-level timecodes from media, on CPU, at eight times realtime, **in any language** —
  multilingual model by default, language detected or named
- Your own audio or video into the library in one command: `python -m dev.add_take`
- Video end to end, verified: search → word picking → mp4 render with h264 and aac
- ClickHouse ingest with word and phrase search, filtered by delivery — 31 query and
  contract tests
- Sample-accurate cutting and splicing on word boundaries
- Assembling a new sentence from single words taken across different recordings
- HTTP API: anonymous sessions, credit ledger, the `Origin-Agent-Cluster` header,
  ranked candidates, provenance fields — 138 API tests
- Timeline interface: virtual-splice player (double buffered, rAF driven), a clickable
  provenance strip, and a panel that drives the same flow by hand without WebMCP
- Five WebMCP tools: `find_line`, `propose_cut`, `preview_segment`,
  `get_timeline_state`, `commit_render`
- Approval dialog: closed shadow root, provenance visible, focus defaults to Cancel
- Render: FFmpeg splice after approval, one credit, session-scoped download —
  measured live at 2160ms from two different takes, exactly the sum of the ranges
- In-page ADK assistant when a key is present; the product works without it
- English and Turkish interface, English by default
- Story track reordering by drag or by arrow keys on a block's handle
- **Word-level picking**: click a word to hear exactly that word, shift-click a second to
  take the span. Verified end to end — a hand-picked 360ms range rendered to 360ms of
  audio, measured off the output file, where the phrase match had been 820ms
- **Bring your own footage from the browser**: drop in audio or video, any language, and it
  is transcribed and searchable. Isolated per session, so one visitor's upload does not
  appear in anyone else's library — asserted by a test that opens a second session
- Interface rendered from state and checked headless — 183 UI tests, 122 web tests

Written but **not verified**: the Gemini tone pass and an assistant turn (no live key
here), and the tools appearing in a real agent client (nothing local exposes
`document.modelContext`).

`pipeline/README.md`, `server/README.md` and `web/README.md` carry the detail.

## Why ClickHouse

Word-level retrieval is an analytical query problem. The table is shaped for it: one row
per spoken word, primary key `(project_id, word_norm, start_ms)`.

"Where does this word occur in the library" comes straight off the primary index. Phrase
search builds on that with ClickHouse's higher-order array functions — `arraySort` plus
`arrayFilter` plus `arraySlice` for consecutive matching, one pass, no joins. If a line
contains the phrase more than once, every occurrence comes back.

ClickHouse is not a metrics bucket here, it is **the retrieval engine**. The SQL lives
in `pipeline/queries.py`, its tests in `pipeline/test_queries.py`.

## Why both Whisper and Gemini

Two different kinds of problem, so two different tools.

**Word timing is an alignment problem.** You have the audio and you have the words; what
you are looking for is the correspondence. Asking a generative model to emit timestamps
is giving it the wrong kind of work. Hence `faster-whisper`, on CPU, free and repeatable.

**Delivery is a prosody and meaning problem.** "This take is calmer" is the thing
alignment cannot answer. Gemini multimodal handles it, one call per take.

## Layout

| Directory | Contents |
| --- | --- |
| `pipeline/` | Alignment, tone, ingest, ClickHouse queries |
| `server/` | FastAPI: API, sessions, credits, render, ADK assistant |
| `web/` | Timeline interface, virtual splicing, WebMCP tools, approval dialog |
| `demo/` | Demo corpus — **content**, has to be in the image |
| `dev/` | Local ClickHouse, `add_take`, corpus build and load, deployment checks |
| `scratch/` | Generated noise, fully ignored |

The split between `demo/` and `scratch/` is deliberate: we do not commit what the code
generates, but we do commit **what the product shows**. If the demo media is not in the
image a judge hears nothing.

## Setup

Every command runs from this directory.

```powershell
python -m venv .venv

# Server only, which is what the Cloud Run image carries
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Add the transcription stack to prepare a corpus
.venv\Scripts\python.exe -m pip install -r requirements-ingest.txt

docker compose -f dev\docker-compose.yml up -d
Copy-Item .env.example .env    # then fill it in
```

Splitting the dependencies is intentional: `faster-whisper` plus `ctranslate2` runs to
hundreds of megabytes and never executes on the server. Transcription happens offline,
the result goes into ClickHouse, and the server only queries.

## Running

```powershell
.venv\Scripts\python.exe -m dev.load_demo          # write the demo corpus to ClickHouse
.venv\Scripts\python.exe -m uvicorn server.main:app --reload --port 8080
```

To rebuild the demo corpus (needs Windows SAPI):
`.venv\Scripts\python.exe -m dev.seed_demo`

## Container

```powershell
docker build -t cinema-app:dev .
docker run --rm -p 8090:8080 --network dev_default `
  -e CLICKHOUSE_HOST=clickhouse -e CLICKHOUSE_PORT=8123 `
  -e CLICKHOUSE_PASSWORD=dev -e CLICKHOUSE_DATABASE=cinema cinema-app:dev
```

866MB, single stage. No node stage, because there is no build step.

It was 337MB until uploads arrived. `faster-whisper` used to be out of the image on the
grounds that transcription happens offline — a fair argument until a visitor wants to bring
their own footage, at which point the library is a demo of itself. The 529MB is
`ctranslate2`, PyAV, `onnxruntime`, `numpy` and the 141MB model baked in so a cold start
does not fetch it from a third party. Dropping `requirements-ingest.txt` from the Dockerfile
puts it back to 337MB and turns uploads off cleanly, which the interface already handles.

Verified end to end in the container: page, search, media range requests, the FFmpeg render,
and an upload transcribed and searched.

Deploying to Cloud Run: `DEPLOY.md`.

## Tests

```powershell
.venv\Scripts\python.exe -m pipeline.test_queries    #  31  SQL and the data contract
.venv\Scripts\python.exe -m server.test_api          # 138  API, uploads, render, agent tools
.venv\Scripts\python.exe -m doctest pipeline\schema.py
node web\test_web.mjs                                # 122  store, WebMCP, injection, i18n
node web\test_ui.mjs                                 # 183  the interface, rendered headless
node web\test_approve.mjs                            #  35  approval dialog
```

Against a running instance, local or deployed:

```powershell
.venv\Scripts\python.exe -m dev.check_deploy http://127.0.0.1:8080   # 27 checks
```

End-to-end ingest and cut verification: `pipeline/README.md`.
API surface and security posture: `server/README.md`.

## Where the footage comes from

The user brings their own licensed library — from the browser, with the upload control, or
from the command line with `python -m dev.add_take`. Editing software is not answerable for
what someone cuts with it, and a news editor cutting an archive clip is doing a legitimate
job.

This repository's demo corpus comes from clean sources. The tool does **not** download
from third-party platforms; it processes files the user already has.

Every fragment keeps its source and timecode, and the interface makes them clickable.
That is not only an editor convenience: it is what makes this **"I assembled what they
said, with sources"** rather than "I made them say something". The difference between
journalism and a deepfake tool is that strip of text, which is why `source_url` has been
in the schema from the first commit.

## Licence

MIT, see `LICENSE`.
