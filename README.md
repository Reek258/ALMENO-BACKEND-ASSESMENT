# AI-Powered Transaction Processing Pipeline

FastAPI + PostgreSQL + RQ/Redis backend that ingests a dirty transactions CSV,
cleans and analyses it asynchronously in a worker process, classifies and
summarises it with Gemini, and exposes the results through a polling API.

## Stack

- **API:** FastAPI
- **Database:** PostgreSQL (SQLAlchemy ORM, `Base.metadata.create_all` on startup)
- **Job queue:** RQ + Redis (chosen over Celery for this scope — same async-job
  guarantee with far less operational config: no separate beat process, no
  result-backend setup, one Redis instance does double duty as broker)
- **LLM:** Gemini, via the current `google-genai` SDK. The brief names
  "Gemini 1.5 Flash", but that model line has been fully retired by Google as
  of mid-2026 (it now 404s) — this project defaults to `gemini-2.5-flash`,
  the current free-tier equivalent. Override via `GEMINI_MODEL` in `.env`.
- **Containerisation:** Docker + Docker Compose (api, worker, postgres, redis —
  single image, two different commands)

## Quick start

```bash
cp .env.example .env
# edit .env and paste your real Gemini API key
docker compose up --build
```

That's it — Postgres, Redis, the API, and the worker all start together. The
API is then live at `http://localhost:8000` (interactive docs at `/docs`).

No local Python/Postgres/Redis install is required; everything runs inside
the containers. The only manual step is putting a Gemini key in `.env`
because that's a secret and can't be baked into the image.

## API walkthrough (curl)

**1. Upload a CSV and get a job_id back immediately**
```bash
curl -X POST http://localhost:8000/jobs/upload \
  -F "file=@sample_data/transactions.csv"
```
```json
{"job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "status": "pending"}
```

**2. Poll status**
```bash
curl http://localhost:8000/jobs/3fa85f64-5717-4562-b3fc-2c963f66afa6/status
```
Returns `pending` → `processing` → `completed` (or `failed`). Once
`completed`, the response includes a `summary` block with totals, top
merchants, anomaly count, and risk level.

**3. Get full results**
```bash
curl http://localhost:8000/jobs/3fa85f64-5717-4562-b3fc-2c963f66afa6/results
```
Returns the cleaned transaction list, the anomalies subset, a per-category
spend breakdown, and the LLM narrative summary.

**4. List jobs, optionally filtered by status**
```bash
curl http://localhost:8000/jobs
curl "http://localhost:8000/jobs?status=completed"
```

## Pipeline design decisions worth knowing for the review

**Date parsing.** The brief documents two date formats (`DD-MM-YYYY` and
`YYYY/MM/DD`), but the actual `transactions.csv` also contains a third shape
already in ISO (`YYYY-MM-DD`). Rather than guessing format from the
separator alone (which would break on the ISO rows, since both ISO and
`DD-MM-YYYY` use `-`), the parser matches each row against three explicit
regex shapes before picking a `strptime` format. Caught this by actually
running the cleaner against the real file rather than only the documented
spec — worth mentioning in the video as evidence of testing against real
data, not just the brief.

**What counts as "missing category" for the LLM step.** Step (a) says to
fill blank categories with `'Uncategorised'`; step (c) says to call the LLM
"for transactions without a category". Taken literally, by the time step (c)
runs, nothing is blank anymore. The pipeline tracks an internal
`_category_missing` flag *before* the fill happens, so step (c) still knows
which rows were originally blank and routes exactly those to the LLM —
everything that had a real category from the source file is left alone.

**Narrative summary is a hybrid, not a single LLM-computed JSON.** The spec
asks the LLM to produce totals, top merchants, and anomaly count directly.
This implementation computes those deterministically in Python (a `sum()`
or `groupby()` cannot hallucinate; an LLM doing arithmetic over 80+ rows
occasionally can) and only asks the LLM for the two genuinely qualitative
fields — the narrative sentence and the risk_level judgment — passing the
computed stats in as context. Same single LLM call, same final JSON shape,
more reliable numbers. This is a good "why" talking point for the video.

**Anomaly detection has a third, unrequested check.** Beyond the two
required rules (3x account median, USD on a domestic-only merchant), rows
whose `notes` field contains "SUSPICIOUS" are also flagged
(`notes_flagged_suspicious`). The source data clearly intends that column as
an analyst signal; throwing it away to match the spec exactly felt like the
wrong call. Worth flagging as a deliberate scope addition, not an oversight.

**Retry/backoff granularity.** The spec says retry failed LLM calls 3x with
backoff and mark a *batch* `llm_failed` if all retries fail. This is
implemented at the batch level for classification (one Gemini call per
~15 rows), but the final `llm_failed` flag is stored per-transaction — only
the rows that didn't get a category back from a failed batch are marked,
not the entire batch's row count. More granular than the spec strictly
requires, and a deliberate choice to make in the review.

## Data model

`Job` → `Transaction` (many) → `JobSummary` (one), matching the structure
suggested in the brief. `Job.id` is a UUID (not an autoincrement int) since
it's exposed externally as the `job_id` API consumers poll with.

## Where this breaks at 100x scale (see video for full discussion)

- **Single Postgres connection pool, single worker container.** At 100x
  traffic the worker becomes the bottleneck first — only one `rq worker`
  process is running, processing jobs strictly sequentially. Horizontal fix:
  run multiple worker replicas (`docker compose up --scale worker=8`), which
  RQ supports natively since workers just pull from the same Redis queue.
- **Synchronous LLM calls inside the worker.** Each classification batch
  blocks the worker thread for the duration of the Gemini round-trip. At
  scale this serializes badly; production would move to an async LLM client
  with a worker pool, or batch even more aggressively per call.
- **CSV read into memory via pandas.** Fine at ~90 rows; a 100x larger file
  (or 100x more concurrent uploads) risks worker OOM. Production fix:
  chunked/streaming CSV processing, or push large files to object storage
  (S3) and stream-process from there instead of loading the whole frame.
- **`create_all` instead of migrations.** Fine for a 4-day assignment;
  any real schema change in production needs Alembic so changes are
  reviewable and reversible.
- **No rate limiting or backpressure on `/jobs/upload`.** A traffic spike
  can enqueue far more jobs than the worker fleet can drain, growing the
  Redis queue unboundedly. Production needs queue depth monitoring and
  either backpressure (reject uploads past a queue-depth threshold) or
  autoscaling workers off queue length.

## Repo structure

```
app/
  main.py              FastAPI app + startup table creation
  config.py            Settings (env-driven)
  database.py          SQLAlchemy engine/session
  models.py             Job, Transaction, JobSummary ORM models
  schemas.py            Pydantic request/response models
  queue.py              RQ queue connection
  storage.py             Upload file persistence helper
  routers/jobs.py        All 4 required endpoints
  worker/
    cleaning.py          Step (a) data cleaning
    anomaly.py            Step (b) anomaly detection
    llm.py                 Step (c)+(d) Gemini calls, retry/backoff
    tasks.py                Orchestrates the full pipeline per job
sample_data/transactions.csv   Copy of the provided sample file
docker-compose.yml
Dockerfile
requirements.txt
```
