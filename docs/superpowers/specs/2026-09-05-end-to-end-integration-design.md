# End-to-End Integration Design

## Goal And Priority

Stages 0-9 must run as one demonstrable product before blockchain work begins.
One batch execution creates an immutable `run_id`; the pipeline processes it,
Supabase persists it, FastAPI serves it, Next.js displays it, and Q&A answers
only from that completed run. The design and holdout partitions are separate
runs produced by identical pipeline code.

## Pipeline Boundary

`matching/pipeline.py` is the only orchestration authority. It accepts typed
ledger, settlement, bank, and 26AS rows and runs:

1. Ledger deduplication and bank scope filtering.
2. Exact-reference, fee-adjusted, split-settlement, and refund matching.
3. Gemini review of the unresolved Stage 6 candidates.
4. Deterministic Stage 7 exception categorization.
5. Stage 8 tax enrichment over accepted matches.

The pipeline returns one typed `PipelineRunResult` containing final results,
exclusions, duplicate evidence, LLM decisions and metrics, tax findings, and
source counts. Every stage receives only the previous stage's unresolved rows.
Ground truth is never part of `PipelineInput`; a separate evaluator compares a
completed result with truth after execution.

## Run And Evaluation Semantics

Each run has a UUID, `run_type` (`design` or `holdout`), label,
status (`queued`, `running`, `completed`, or `failed`), UTC timestamps, and
`code_version`. `code_version` is the current Git `HEAD` SHA captured when the
run starts.

Design runs may be recreated. The database has a partial unique index allowing
at most one `holdout` row. The normal API has no holdout deletion or overwrite
operation. Exceptional recovery archives the existing row outside the API by
changing its type and label to `archived-holdout-<sha>`; it is never deleted.
A later run after matching-rule or threshold changes is a new evaluation and
cannot be described as the original untouched holdout.

The holdout endpoint is not used until the pipeline, persistence, API, frontend,
and design run are verified. Its label is `Final Holdout Run - untouched` and
its metrics are the headline dashboard values. The design run remains available
as `Design Run - development set`.

## Supabase Persistence

Supabase is the deployed source of truth. A checked-in SQL migration creates:

- `runs`: identity, type, label, lifecycle, code version, source counts,
  summary metrics, error, and timestamps.
- `source_records`: run ID, source kind, stable ordinal, and typed source row as
  JSONB. This retains settlements required by Q&A without exposing them to the
  browser directly.
- `reconciliation_results`: final matched and exception outputs.
- `llm_decisions`: Stage 6 decisions and operational evidence.
- `tax_findings`: Stage 8 outputs.
- `evaluation_metrics`: post-run truth scores only; ground-truth rows themselves
  are not stored with matcher inputs.

The backend uses Supabase PostgREST directly with the service-role key; the key
never enters Next.js or any response. Persistence writes `running` first, then
child tables, then changes the run to `completed`. Any failed write marks the
run `failed`; partial child rows may remain for diagnosis but list, result, and
Q&A endpoints never surface them as completed data. No cross-table transaction
layer is added for this submission.

## FastAPI Contract

FastAPI owns execution and read access:

- `POST /api/v1/runs/design` creates and executes a design run.
- `POST /api/v1/runs/holdout` creates the single holdout run and returns conflict
  if one already exists.
- `GET /api/v1/runs` lists completed runs, holdout first and then newest design.
- `GET /api/v1/runs/{run_id}` returns one completed run summary.
- Result, metric, LLM-decision, and tax routes remain scoped beneath the run.
- `POST /api/v1/runs/{run_id}/questions` reconstructs typed rows from Supabase
  and invokes Stage 9 only when the run status is `completed`.

Pipeline work runs synchronously for the initial batch endpoints so a successful
response proves persistence finished. Before holdout execution, the deployed
request timeout must be recorded and exceed three times the measured complete
design-run duration plus ten seconds; otherwise holdout creation is blocked and
run through a non-proxied deployment command instead. Run status still records
failures. There is no queue, worker, authentication, upload flow, or generic
CRUD layer in this integration slice.

## Frontend Data Flow

Fixture fallback is removed. Every page calls FastAPI with the active `run_id`.
A shared selector shows both runs with explicit labels and defaults to the
holdout once it exists; before then it defaults to the latest completed design
run. Selection is carried in the URL so refreshes and shared links preserve the
same run.

Overview, reconciliations, detail, tax, and Q&A use the same selected run. Empty,
loading, API-unavailable, and no-run states remain explicit. Audit UI is hidden
until blockchain is actually implemented, so the core demo never suggests an
unavailable proof exists.

## Verification And Delivery Order

1. TDD the orchestrator with injected Stage 6 decisions and prove stage order,
   record conservation, truth isolation, and design metrics.
2. TDD the Supabase store contract and FastAPI endpoints with an in-memory test
   store. Prove every source model survives JSONB serialization and typed
   reconstruction without date or amount changes; apply the migration and
   persist a real design run.
3. Replace frontend fixtures, add the run selector, build Next.js, and verify
   every page against that persisted design run.
4. Run live Gemini Q&A against the persisted design run.
5. Freeze the pipeline commit, create the holdout exactly once, persist its SHA
   and metrics, and verify the dashboard defaults to it.

Required gates are the existing per-stage gates plus complete record coverage,
zero duplicate final record IDs, completed-only reads, one holdout maximum,
FastAPI contract tests, a successful Next.js production build, and browser
verification of the real design and holdout runs. Stage 9 is rerun through the
Supabase reconstruction path and must retain at least 90% accuracy, 100% unsafe
SQL rejection, and 100% run isolation. Blockchain is considered only after all
five steps pass.

## Configuration Checkpoint

`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are required only after offline
store and API contract tests pass. At that point the migration is applied and
the first real design run is persisted. Neither value is required for pipeline
unit tests or the in-memory API tests.
