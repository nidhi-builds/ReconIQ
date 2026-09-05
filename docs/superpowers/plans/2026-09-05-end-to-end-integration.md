# End-to-End Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute Stages 0-9 as one run-scoped product backed by Supabase and
displayed by Next.js without fixture data.

**Architecture:** `matching/pipeline.py` owns deterministic stage ordering and
returns typed outputs. A narrow store contract persists immutable run snapshots
to Supabase; FastAPI exposes completed runs, and Next.js selects one `run_id`
for every screen and Q&A request.

**Tech Stack:** Python 3.13, Pydantic 2, FastAPI, stdlib HTTP/JSON, Supabase
PostgREST, pytest, Next.js 15, React 19, TypeScript.

**Spec:** `docs/superpowers/specs/2026-09-05-end-to-end-integration-design.md`

## Global Constraints

- Work on `main`; request approval before every commit.
- Ground truth is scoring-only and never enters `PipelineInput` or an LLM prompt.
- Only completed runs are readable by result, metric, tax, or Q&A endpoints.
- Supabase service-role credentials remain backend-only.
- The database permits at most one active `holdout` run.
- Do not implement blockchain in this plan.

---

### Task 1: Pipeline Orchestrator And Evaluation

**Files:**
- Create: `matching/pipeline.py`
- Create: `matching/evaluation.py`
- Create: `tests/test_pipeline.py`
- Create: `tests/test_evaluation.py`
- Modify: `IMPLEMENTATION_LOG.md`

**Interfaces:**
- Produces: `PipelineInput`, `PipelineMetrics`, `PipelineRunResult`, and
  `run_reconciliation(input, *, tracker, decide=None) -> PipelineRunResult`.
- Produces: `evaluate_run(result, ground_truth) -> EvaluationMetrics`.

- [x] Write failing tests for exact stage order, unresolved-only forwarding,
  Stage 2 confirmation wiring into refunds, final record conservation, unique
  final record ownership, stable ordering, and ground-truth-free inputs.
- [x] Write failing evaluator tests for precision, recall, match rate,
  per-exception precision/recall, hard-negative precision, and tax accuracy.
- [x] Run both test modules and confirm failures are caused by missing modules.
- [x] Implement the smallest orchestration and scoring code using existing stage
  functions without changing their matching rules.
- [x] Run focused tests, then all offline Python tests.
- [x] Run Ponytail review, update the implementation log, and request commit
  approval.

### Task 2: Supabase Store And FastAPI

**Files:**
- Create: `db/migrations/001_runs.sql`
- Create: `api/models.py`
- Create: `api/store.py`
- Create: `api/service.py`
- Create: `api/main.py`
- Create: `tests/test_store.py`
- Create: `tests/test_api.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `IMPLEMENTATION_LOG.md`

**Interfaces:**
- Produces: `RunStore`, `InMemoryRunStore`, `SupabaseRunStore`, and typed run
  records with lifecycle transitions.
- Produces: `app` with run list/detail/results/metrics/LLM/tax/Q&A routes and
  synchronous design/holdout execution routes.

- [x] Write failing store-contract tests for lifecycle, immutable completion,
  single-holdout enforcement, completed-only reads, failure visibility, and
  exact typed JSON round-trips for every source model.
- [x] Write failing API tests for run scoping, filters, pagination, missing and
  incomplete runs, Q&A completed-run gating, and sanitized failures.
- [x] Run focused tests and confirm red.
- [x] Add the SQL migration, minimal store implementations, configuration,
  service, routes, CORS, and required FastAPI dependencies.
- [x] Verify Stage 9 reconstructs typed Supabase rows before building SQLite.
- [x] Run focused and complete offline suites; run Ponytail review and request
  commit approval.
- [x] Request `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`, apply the migration,
  then persist and read back one real design run.

### Task 3: Real-Data Next.js Dashboard

**Files:**
- Modify: `web/lib/api.ts`
- Modify: `web/lib/types.ts`
- Modify: `web/app/page.tsx`
- Modify: `web/app/reconciliations/page.tsx`
- Modify: `web/app/reconciliations/[id]/page.tsx`
- Modify: `web/app/tax/page.tsx`
- Modify: `web/app/ask/page.tsx`
- Modify: `web/app/api/question/route.ts`
- Modify: `web/components/nav.tsx`
- Create: `web/components/run-selector.tsx`
- Modify: `web/lib/view-model.test.ts`
- Modify: `IMPLEMENTATION_LOG.md`

**Interfaces:**
- Consumes: completed FastAPI `run_id` routes.
- Produces: URL-persisted run selection across overview, results, detail, tax,
  and Q&A pages.

- [x] Write failing frontend tests for holdout-first selection, design fallback,
  explicit labels, and API-only behavior.
- [x] Remove every fixture fallback and make missing API configuration fail
  visibly.
- [x] Add the shared run selector and propagate `run_id` through navigation and
  question requests.
- [x] Hide audit navigation and UI until blockchain exists.
- [x] Run frontend tests, TypeScript checks, and a production build.
- [ ] Start both servers and use browser screenshots to verify desktop/mobile,
  real design data, selector persistence, result drill-down, tax, and Q&A.
- [ ] Run Ponytail review, update the log, and request commit approval.

### Task 4: Live Design Gate And Frozen Holdout

**Files:**
- Modify: `LLM_EVALUATION_LOG.md`
- Modify: `IMPLEMENTATION_LOG.md`
- Modify: `README.md`

**Interfaces:**
- Produces: one completed design run and one immutable holdout run in Supabase.

- [x] Run and record the complete persisted design pipeline, including stage,
  exception, tax, LLM, and total-runtime metrics.
- [x] Re-run all 24 Stage 9 questions through Supabase reconstruction; require
  at least 90% accuracy, 100% unsafe rejection, and 100% run isolation.
- [ ] Verify every frontend page against the real design `run_id`.
- [ ] Record hosting timeout and prove it exceeds `3 * design runtime + 10s`, or
  use the documented non-proxied command for holdout.
- [ ] Freeze and record the current `HEAD` SHA; execute holdout exactly once.
- [ ] Persist final headline metrics and verify the UI defaults to the labeled
  holdout while design remains selectable.
- [ ] Run the final Python suite, frontend tests/build, API smoke, and browser
  checks; update logs and request final integration commit approval.
