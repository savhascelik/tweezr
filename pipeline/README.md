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

```powershell
$py = ".venv\Scripts\python.exe"

# 0. Test audio, if you have no footage of your own yet
powershell -ExecutionPolicy Bypass -File pipeline\make_test_audio.ps1
Move-Item sample.wav scratch\

# 1. Word-level timing
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

Windows, CPU, `base.en`, int8:

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
