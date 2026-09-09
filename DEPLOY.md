# Deploying to Cloud Run

The container is verified end to end locally: page, search, media range requests and the
render including FFmpeg all work on the slim image. What is left is opening accounts and
deploying.

## Two limits to know first

**`--max-instances=1` is required.** Render jobs are held in memory and the session ledger
is SQLite in `/tmp`. With two instances running, the status of a render started on one
returns 404 on the other. This is a deliberate simplicity decision; a durable queue would
be unnecessary complexity at this scale. With no traffic expected, one instance is more
than enough.

**`--allow-unauthenticated` is necessary** and intentional. A mandatory login kills WebMCP
discovery: tools are registered by page JS, so a judge hitting a login wall gives the
agent zero tools. The brakes that pay for this are listed in `server/README.md` — per
session credits, a per-IP session cap, a project allowlist, parameterised queries.

## 1. ClickHouse Cloud

Create a service, take the connection details, then create the table and load the corpus
from your machine:

```powershell
$env:CLICKHOUSE_HOST     = "xxx.clickhouse.cloud"
$env:CLICKHOUSE_PORT     = "8443"
$env:CLICKHOUSE_SECURE   = "1"
$env:CLICKHOUSE_USER     = "default"
$env:CLICKHOUSE_PASSWORD = "..."
$env:CLICKHOUSE_DATABASE = "cinema"

.venv\Scripts\python.exe -m dev.load_demo
```

`load_demo` creates the table, writes `demo/takes/*.json` and verifies search. It runs no
Whisper and needs no GPU.

If you are adding your own footage, build the corpus first — that is the heavy part and it
belongs on a developer machine:

```powershell
.venv\Scripts\python.exe -m pipeline.transcribe <file> --take-id T09 ... --out demo\takes\T09.json
.venv\Scripts\python.exe -m pipeline.tone demo\takes\T09.json --media demo\media\T09.wav
Copy-Item <file> demo\media\
.venv\Scripts\python.exe -m dev.load_demo
```

## 2. Cloud Run

```powershell
$PROJECT = "<gcp-project-id>"
$REGION  = "europe-west1"
$SERVICE = "cinema"

gcloud config set project $PROJECT
gcloud services enable run.googleapis.com artifactregistry.googleapis.com

gcloud run deploy $SERVICE `
  --source . `
  --region $REGION `
  --allow-unauthenticated `
  --max-instances=1 `
  --min-instances=1 `
  --cpu=2 --memory=2Gi `
  --timeout=300 `
  --set-env-vars="CLICKHOUSE_HOST=$env:CLICKHOUSE_HOST,CLICKHOUSE_PORT=8443,CLICKHOUSE_SECURE=1,CLICKHOUSE_USER=default,CLICKHOUSE_DATABASE=cinema" `
  --set-secrets="CLICKHOUSE_PASSWORD=clickhouse-password:latest,GEMINI_API_KEY=gemini-key:latest"
```

Notes:

- **`--min-instances=1`** so a judge does not wait on a cold start. An idle instance costs
  money; drop it to `0` once judging is over.
- **`--cpu=2`** because the FFmpeg concat is CPU work. One CPU works, just slower.
- **Secrets go through `--set-secrets`, not `--set-env-vars`.** A secret passed as an
  environment variable shows up in plain text in `gcloud run services describe` and in the
  console. Put them in Secret Manager:

```powershell
"..." | gcloud secrets create clickhouse-password --data-file=-
"..." | gcloud secrets create gemini-key --data-file=-
```

- **`GEMINI_API_KEY` is optional.** Without it the in-page assistant closes and states why
  in the interface; search, timeline, preview, provenance and the WebMCP tools all keep
  working.

## 3. Verify

```powershell
.venv\Scripts\python.exe -m dev.check_deploy https://<service-url>
```

25 checks: the WebMCP preconditions (https, `Origin-Agent-Cluster: ?1`, no CSP that would
break the tools), assets, a session established without a login, library and search, media
HTTP range support, and a real render.

### From here it is manual, and this is the part that matters

The script **cannot** verify that `registerTool` succeeded; that is a browser job.

1. Open the URL in ChatGPT's in-app browser, or in Chrome with WebMCP enabled
2. Ask the agent what tools it has. Five should appear: `find_line`, `propose_cut`,
   `preview_segment`, `get_timeline_state`, `commit_render`
3. Ask it to find the calmest reading of "I never asked for this"
4. A proposal should appear on the timeline, with a provenance strip under every segment
5. Ask it to render: the approval dialog should open and the agent should wait on it

If the tools do not appear, look at the **browser console** first. If `registerTool` was
refused the reason is printed there; the most common cause is a missing
`Origin-Agent-Cluster` header and a `SecurityError` — but the server does send that header
and `check_deploy` asserts it, so if it is something else the console will say so.

## 4. The budget brake

The global budget brake promised in the blueprint **does not exist yet**. What exists: per
session credits, a per-IP session cap, the assistant message counter, `MAX_OUTPUT_MS`,
`MAX_UPLOAD_*` and the single instance limit.

Those limit abuse, not the Google Cloud bill. Before deploying:

- Set a **budget alert** in GCP (Billing → Budgets & alerts)
- Once submission and judging are done, `--min-instances=0`
- When the hackathon ends, delete the service: `gcloud run services delete $SERVICE`
