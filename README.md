# ReconIQ

ReconIQ reconciles internal ledger, gateway settlement, and bank records through
a rules-first pipeline. Deterministic stages handle clear cases; Gemini reviews
only the unresolved remainder. The dashboard persists completed runs in
Supabase and lets an analyst inspect results, tax checks, and run-scoped Q&A.

## What It Does

- Removes injected ledger duplicates and non-transaction bank rows.
- Matches exact references, fee-adjusted settlements, split settlements, and
  refund reversals before semantic review.
- Uses Gemini with structured output for ambiguous bank narrations only.
- Categorizes every unresolved record into one exception reason.
- Enriches confirmed settlements with GST/TDS checks against Tax 26AS.
- Persists completed runs and isolates Q&A to one selected run.

## Run Types

`design` is the synthetic development dataset. `holdout` is reserved for one
frozen evaluation run. `upload` runs user-provided CSVs and reports operational
results only: it deliberately has no precision, recall, or exception-accuracy
score because it has no hidden ground truth.

Uploads require Ledger, Settlement, and Bank CSVs. Tax 26AS is optional. The
Ask page states exactly which documents belong to the selected run; when Tax
26AS is absent, Tax shows an empty state rather than inventing tax findings.

## Local Setup

Create `.env` from `.env.example` and set:

```env
GEMINI_API_KEY=...
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

Apply [001_runs.sql](db/migrations/001_runs.sql) in the Supabase SQL editor.
Then start the API and dashboard in separate terminals:

```powershell
uv run --no-project --with "fastapi>=0.115,<1" --with "google-genai>=1,<2" --with "pydantic>=2.12,<3" --with "python-dotenv>=1,<2" --with "uvicorn>=0.34,<1" --env-file .env uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload

cd web
npm install
npm run dev
```

Open `http://127.0.0.1:3000`. API documentation is available at
`http://127.0.0.1:8000/docs`.

## CSV Uploads

Use **Upload & run** on the Overview page. **Reset files** only clears selected
files before execution; it never deletes completed runs. Uploaded runs stay
selectable in the Dataset dropdown, where each upload is timestamped.

## Verification

```powershell
cd web
npm test
npx tsc --noEmit
```

The Python suite uses the same no-project runtime approach when a local `uv`
cache is available:

```powershell
uv run --no-project --with "pytest>=8.4,<9" --with "fastapi>=0.115,<1" --with "google-genai>=1,<2" --with "pydantic>=2.12,<3" --with "python-dotenv>=1,<2" python -m pytest -m "not live"
```

## Project Map

- `data/` - schemas and deterministic synthetic generator
- `matching/` - reconciliation stages and evaluation
- `tax/` - GST/TDS enrichment
- `qa/` - safe text-to-SQL Q&A over one run snapshot
- `api/` - FastAPI run persistence and query endpoints
- `db/migrations/` - Supabase schema
- `web/` - Next.js dashboard
- `tests/` - stage and integration tests

Implementation decisions and gate evidence are recorded in
[IMPLEMENTATION_LOG.md](IMPLEMENTATION_LOG.md).
