# Stage 9 Text-to-SQL Q&A Design

## Scope

Stage 9 answers questions over one completed reconciliation run using SQL, not
vector retrieval. Supabase is the deployed source of truth. FastAPI fetches one
run's reconciliation and tax rows, then copies them into an isolated in-memory
SQLite snapshot so model-generated SQL never executes against production.
Ground truth and secrets never enter either model prompt.

## Providers

The primary model is `gemini-2.5-flash`. Live probes rejected
`deepseek-ai/deepseek-v4-pro-0813` after repeated 30-90 second timeouts. The
replacement fallback candidate is `nvidia/nemotron-3.5-lightning-30b-a3b`.
Both run at temperature zero with structured JSON validated by Pydantic.

Fallback is attempted for timeouts, quota or server errors, malformed JSON,
and syntactically invalid read-only SQL. Unsafe SQL is rejected locally and
never sent to another model. If both providers fail, the result is
`QA_UNAVAILABLE` rather than a guessed answer.

NVIDIA describes its hosted endpoint as a trial service, not a permanent free
tier. If it is unavailable at demo time, Q&A continues Gemini-only and returns
`QA_UNAVAILABLE` when Gemini also fails.

## Provider Affinity

SQL generation and answer synthesis are distinct calls and are tracked
separately. When Gemini generates valid SQL, Gemini normally synthesizes the
answer. If Gemini SQL generation fails and NVIDIA generates it, NVIDIA also
synthesizes that question's answer. If Gemini generated the SQL but only its
answer call fails, NVIDIA receives the already-executed rows and performs
answer synthesis only.

The response exposes `sql_provider`, `sql_model`, `answer_provider`,
`answer_model`, and `fallback_used`, so mixed-provider execution is explicit.

## Data And Flow

The SQLite snapshot exposes only `reconciliation_results` and
`tax_enrichment`. Inputs are typed finalized results, settlement-derived
display fields, and `TaxFinding` rows fetched for the requested `run_id`.

1. Build the run-scoped snapshot.
2. Ask the SQL provider for one structured `SELECT` or `WITH` query.
3. Validate and execute the query.
4. For safe queries with rows, ask the answer provider to summarize only those
   rows. Empty results use a deterministic no-results answer.
5. Return the question, answer, rows, generated SQL, provider details, and call
   metrics.

Unsafe queries short-circuit to a deterministic refusal. No answer-synthesis
call is made and the rejected SQL is never executed.

## SQL Safety

- Accept only `SELECT` and `WITH`; reject writes, schema operations, pragmas,
  attachments, and unknown tables through SQLite's authorizer.
- Use `sqlite3.Cursor.execute()`, whose single-statement contract rejects
  stacked statements. A semicolon-injection regression test must prove that
  `SELECT ...; DROP TABLE ...` executes nothing. Its `ProgrammingError` is
  caught and recorded as the SQL call metric's failure reason.
- Wrap accepted queries with a 50-row output limit.
- Install a SQLite progress handler that aborts after 250 milliseconds, so
  recursive queries and large cross-joins cannot stall the request. A runtime
  abort returns a deterministic refusal without invoking the fallback model.
- Build a new snapshot per question containing only the requested run.

## Typed Outputs And Observability

`SQLGeneration` contains the generated SQL and brief reasoning. `QueryRow`
contains typed scalar values. `QuestionAnswer` contains the answer, evidence
rows, SQL, provider fields, fallback state, and status.

Each SQL-generation and answer-synthesis call produces a `QACallMetric` with
phase, provider, model, latency, input/output tokens, estimated cost in USD,
error, and fallback reason. NVIDIA trial calls report zero estimated cost;
Gemini uses the existing paid-tier estimate.

## Testing And Gate

Offline tests inject provider functions and cover valid queries, aggregates,
empty results, provider affinity, both fallback paths, total provider failure,
malformed responses, SQL syntax errors, unsafe and stacked statements, unknown
tables, output limits, runtime cancellation, run isolation, non-mutation, and
telemetry.

A fixed 24-question design set covers reconciliation counts, methods, amounts,
exceptions, confidence, GST/TDS, grouping, empty results, ambiguous wording,
and adversarial requests. Accuracy is scored from returned rows, not subjective
answer wording.

The gate requires at least 90% answer accuracy, 100% unsafe-query rejection,
and 100% run isolation. Nemotron must independently score at least 90% before
fallback is enabled. Until that live gate completes, production integration is
Gemini-only and fails closed with `QA_UNAVAILABLE`.
