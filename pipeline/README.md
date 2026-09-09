# pipeline — the retrieval backbone

The layer that makes a library searchable. No UI, no agent.

```
media ──▶ transcribe.py ──▶ tone.py ──▶ ingest.py ──▶ ClickHouse
          (words + ms)      (Gemini)                       │
                                                    search.py ◀── the agent asks here
```

## Division of labour

| Job | Tool | Why |
| --- | --- | --- |
| Words and timing | `faster-whisper`, CPU | An alignment problem. You have the audio and the words; you are looking for the correspondence. |
| Delivery | Gemini multimodal | A prosody and meaning problem. The one thing alignment cannot answer. |
| Search | ClickHouse | Word-level retrieval. Not a metrics bucket, the engine itself. |

## Files

| File | Job |
| --- | --- |
| `__init__.py` | Package, because `server/` shares the schema and the SQL. |
| `schema.py` | The data contract. Normalisation, flattening, validation, alignment report. Single source of truth. |
| `fixture.json` | Hand-written example of the contract. The pipeline has to produce this shape. |
| `transcribe.py` | Media → words with millisecond timing. |
| `tone.py` | One Gemini call per take → a delivery label per line. |
| `queries.py` | All of the SQL. No query strings are built anywhere else. |
| `db.py` | ClickHouse connection, from the environment. |
| `ingest.py` | Document → the `words` table. |
| `search.py` | Line search, plus the line words behind the clickable transcript. |
| `test_queries.py` | Query correctness tests. |
| `verify_cut.py` | Cut verification plus the local reference implementation. |
| `schema.sql` | The `words` table. Column order matches `schema.py`. |
| `make_test_audio.ps1` | Known-text test audio via Windows SAPI. |

## Setup

Setup and every command run **from the `app/` root**. `pipeline` is a package because
`server/` uses the same schema and the same SQL, and neither should exist twice.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-ingest.txt
docker compose -f dev\docker-compose.yml up -d
```

No system `ffmpeg` needed.

## End to end

For your own footage there is one command that does all of this — see
`dev/add_take.py`, or the "Adding your own footage" section below. The steps here are the
same chain with the seams visible, which is what you want when something goes wrong.

```powershell
$py = ".venv\Scripts\python.exe"

# 0. Test audio, if you have no footage of your own yet
powershell -ExecutionPolicy Bypass -File pipeline\make_test_audio.ps1
Move-Item sample.wav scratch\

# 1. Word-level timing  (the language is detected; name it with --language when you know it)
& $py -m pipeline.transcribe scratch\sample.wav --take-id T01 --scene S01 --camera A `
    --speaker MAYA --out scratch\T01.json

# 2. Delivery  (without a key use --dry-run; everything becomes neutral)
& $py -m pipeline.tone scratch\T01.json --media scratch\sample.wav

# 3. Write to ClickHouse
& $py -m pipeline.ingest scratch\T01.json --replace

# 4. Search
& $py -m pipeline.search --phrase "I never asked for this"
& $py -m pipeline.search --phrase "I never asked for this" --tone calm
& $py -m pipeline.search --word asked
& $py -m pipeline.search --stats
```

## Adding your own footage

```powershell
# Audio or video, any language. One command for the whole chain.
& $py -m dev.add_take C:\kayit\take01.mp4 --take-id S02_T01 --speaker MAYA

# When you know the language, say so. Detection reads only the opening seconds.
& $py -m dev.add_take take01.mp4 --take-id S02_T01 --language tr --model small

# Make this the whole library instead of adding to it
& $py -m dev.add_take take01.mp4 --take-id S02_T01 --replace
```

It copies the media under `demo/media`, transcribes it, labels the delivery if a Gemini
key is present, writes to ClickHouse, and saves the ingest document next to the media so
the take can be reloaded later without transcribing again.

The media is **copied rather than referenced**. `source_url` is a bare filename because
the server resolves it under `MEDIA_DIR` and refuses anything that escapes — that check is
what stops a request from choosing which file ffmpeg opens. A path pointing anywhere on
disk would have to defeat it.

### Language

The default model is multilingual and the language is detected from the audio. That is a
deliberate default: the `.en` models physically cannot transcribe anything else, and
choosing one as the default would have made the product English-only without saying so.
Asking for a non-English language with an `.en` model now raises instead of returning
confident nonsense.

Naming the language beats detection when you know it. Detection reads only the opening
seconds, so a quiet or musical intro can mislead it, and the language also decides
orthography: told it is Turkish, Whisper writes `ışık` rather than guessing a spelling,
and the search key depends on that spelling. `small` is a clear step up from `base` off
English and still runs on CPU.

Nothing in the search path is English-specific. `normalize_word` case folds through
Unicode, which handles German ß, French accents, Greek and Cyrillic, and leaves scripts
without case untouched. One case needed explicit handling: the Turkish dotted and dotless
i. `"İ".casefold()` produces `i` plus a combining dot, which does not equal `i`, and
Turkish capitalises `ı` as `I`, which casefolds to `i`. Both break search on real
transcripts and neither can be resolved without knowing the language of each word, so the
search key folds them together. The `word` column keeps the original spelling, and that is
what appears on screen.

### Video is not a special case

Nothing special-cases it. Whisper reads the audio track through ffmpeg, the player element
is a `<video>`, and the render detects a video stream and produces mp4 with h264 and aac
instead of wav. What video adds: a real frame in the story-track thumbnails and a picture
on the stage.

One honest difference. Audio cuts are sample accurate; **video cuts are frame accurate**.
At 24fps a frame is 41.7ms and you cannot cut through the middle of one, so a 600ms pick
came out as 667ms in a measured run. That is the format, not a bug, but it is worth
knowing before you claim millisecond precision about a video render.

## Verification

```powershell
& $py -m pipeline.test_queries                    # SQL correctness (20)
& $py -m doctest pipeline\schema.py               # normalisation
& $py -m pipeline.search --phrase "..." --compare scratch\T01.json
```

`--compare` is the important one: it asserts the SQL and the local reference
implementation (`verify_cut.find_phrase`) return the same answer. If those two ever
diverge, the product starts returning the wrong takes without telling anyone.

## Verify the cut by ear

The numbers say the alignment is plausible. Only listening says it is good.

```powershell
# Cut every occurrence of a phrase and splice them together
& $py -m pipeline.verify_cut scratch\T01.json --phrase "I never asked for this" `
    --media scratch\sample.wav --splice --out scratch\spliced.wav

# THE HARD TEST: build a new sentence from words taken from different places
& $py -m pipeline.verify_cut scratch\T01.json --phrase "I asked for quiet on set" `
    --media scratch\sample.wav --assemble --out scratch\assembled.wav
```

If words sound clipped, the first knob to turn is `--pad-ms 40`.

## Measured

Windows, CPU, `base.en`, int8. The multilingual `base` is the same size and runs at the
same speed; `small` is roughly three times slower and still comfortably faster than
realtime:

| | |
| --- | --- |
| 9.03s of audio → transcription | 1.09s (realtime factor **0.12**) |
| Model load | 16.6s, once, then cached |
| Zero-length words / overlaps | 0 / 0 |
| Word duration p50 / p95 | 240 / 340 ms |
| Cut accuracy | Sample accurate (measured: 6 words = 1660 ms, exactly the sum) |

## What the SAPI test does not measure

Synthesised speech is cleaner than real speech: even pace, clear gaps between words, no
room noise. Its delivery is also flat, so it does not meaningfully exercise `tone.py`.

Two things have to be measured on real material:

1. **Alignment** — listen to a cut and hear whether it slices through a word
2. **Delivery labels** — is the take Gemini called "calm" actually calm

Both are core claims of the product. Neither is worth building past unverified.
