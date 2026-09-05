# ReconIQ FastAPI Design

## Purpose and scope

FastAPI is a thin HTTP boundary around the reconciliation application. It validates uploaded source files, starts one pipeline run, and exposes the finalized results, metrics, LLM decisions, tax findings, Q&A answers, and audit proofs needed by the Next.js dashboard.

Route handlers never call individual matching stages. They depend on `matching/pipeline.py`, which owns stage ordering, passes only unresolved records forward, keeps ground truth outside matcher inputs, and returns one typed `PipelineRunResult`. Until that module exists, the API can be scaffolded and contract-tested, but reconciliation endpoints are not complete.

## Runtime shape

- Package: `api/`
- Entry point: `api/main.py` exposing `app`
- Versioned prefix: `/api/v1`
- Initial execution: FastAPI `BackgroundTasks` with an in-process run store. `POST` returns `202` immediately and the frontend polls the run resource.
- Initial deployment assumption: one Render process. Restarting it loses run history; this is an accepted demo limitation.
- Configuration: environment variables parsed once by a Pydantic settings model. No environment reads inside routes.

The API adds only `fastapi`, `uvicorn`, `pydantic-settings`, and `python-multipart`. CSV parsing uses Python's `csv` module; storage remains in memory for the first working slice.

## Pipeline boundary

`matching/pipeline.py` must expose the following contract before route implementation can be completed:

```python
class PipelineInput(BaseModel):
    ledger: list[LedgerEntry]
    settlements: list[SettlementEntry]
    bank: list[BankEntry]
    tax_26as: list[Tax26ASEntry] = Field(default_factory=list)

class ExceptionResult(BaseModel):
    record_ids: list[str]
    exception_reason: str
    reasoning: str

class PipelineMetrics(BaseModel):
    total_input_records: int
    transaction_records: int
    excluded_non_transactions: int
    matched_groups: int
    unresolved_groups: int
    match_rate: float
    precision: float | None
    recall: float | None

class TaxFinding(BaseModel):
    record_ids: list[str]
    ref_id: str | None
    gst_category: str
    tds_section: str
    mismatch_reason: Literal[
        "SHORT_DEDUCTION", "MISSING_CHALLAN", "WRONG_SECTION"
    ] | None
    reasoning: str

class PipelineRunResult(BaseModel):
    results: list[ReconciliationResult]
    exceptions: list[ExceptionResult]
    llm_decisions: list[LLMDecisionRecord]
    tax_results: list[TaxFinding] = Field(default_factory=list)
    metrics: PipelineMetrics
```

`run_reconciliation(input, *, tracker) -> PipelineRunResult` is synchronous. The API invokes it in a background task. Evaluation ground truth, when present for the generated design set, is passed to a separate scoring function after matching and is never included in `PipelineInput`.

Stage 8 populates `tax_results`; it stays empty until that module is implemented. Q&A and audit consume a completed result; they do not alter it.

## HTTP contracts

All JSON models are Pydantic models. Dates use ISO `YYYY-MM-DD`, timestamps use UTC ISO 8601, and IDs are opaque strings.

```python
RunStatus = Literal["queued", "running", "completed", "failed"]

class HealthResponse(BaseModel):
    status: Literal["ok"]

class ErrorDetail(BaseModel):
    file: str | None = None
    row: int | None = None
    field: str | None = None
    message: str

class ErrorResponse(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None

class SourceCounts(BaseModel):
    ledger: int
    settlements: int
    bank: int

class SummaryMetrics(BaseModel):
    total_records: int
    matched_records: int
    exception_records: int
    pending_records: int
    matched_amount: float
    match_rate: float
    precision: float | None
    recall: float | None
    matched_groups: int
    exceptions: int

class RunAccepted(BaseModel):
    run_id: str
    status: RunStatus

class StageCount(BaseModel):
    stage: str
    label: str
    count: int

class ExceptionMetric(BaseModel):
    category: str
    count: int
    precision: float | None
    recall: float | None

class LLMMetrics(BaseModel):
    calls: int
    cache_hit_rate: float
    average_latency_ms: float
    estimated_cost_usd: float

class AuditStatus(BaseModel):
    status: Literal["not_anchored", "anchored"]
    merkle_root: str | None
    transaction_hash: str | None

class RunSummary(BaseModel):
    id: str
    name: str
    status: RunStatus
    created_at: datetime
    source_counts: SourceCounts
    summary: SummaryMetrics
    stage_counts: list[StageCount]
    exception_metrics: list[ExceptionMetric]
    llm_metrics: LLMMetrics
    audit: AuditStatus

class ResultSummary(BaseModel):
    id: str
    record_ids: list[str]
    matched: bool
    confidence: float
    method: str
    exception_reason: str | None
    reasoning: str | None
    amount: float
    date: date

class ResultPage(BaseModel):
    items: list[ResultSummary]
    total: int
    offset: int
    limit: int

class LLMDecisionPage(BaseModel):
    items: list[LLMDecisionRecord]
    total: int
    offset: int
    limit: int

class TaxResultPage(BaseModel):
    items: list[TaxFinding]
    total: int
    offset: int
    limit: int

class CategoryCount(BaseModel):
    category: str
    count: int

class RunMetricsResponse(BaseModel):
    pipeline: PipelineMetrics
    llm: LLMMetrics
    exception_counts: list[CategoryCount]

class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)

class QuestionResponse(BaseModel):
    question: str
    answer: str
    rows: list[dict[str, str | int | float | bool | None]]
    generated_sql: str | None = None

class AuditSummary(BaseModel):
    run_id: str
    merkle_root: str
    record_count: int
    chain: str | None
    transaction_hash: str | None

class AuditVerification(BaseModel):
    record_id: str
    verified: bool
    leaf_hash: str
    merkle_root: str
    proof: list[str]
```

Precision and recall are populated only for the generated design run; they are `null` for uploads without ground truth. Domain payloads reuse models from `data/schemas.py`, `matching/llm_match.py`, and `observability/llm_tracker.py`; the API does not duplicate them.

## Endpoints

| Method and path | Request | Response | Purpose |
|---|---|---|---|
| `GET /api/v1/health` | none | `HealthResponse` | Deployment health check; no dependency probing. |
| `GET /api/v1/runs` | none | `list[RunSummary]` | List runs in newest-first order for dashboard selection. |
| `POST /api/v1/runs/demo` | none | `202 RunAccepted` | Run the generated **design** partition. The holdout is deliberately unavailable. |
| `POST /api/v1/runs/upload` | multipart `ledger`, `settlements`, `bank`, optional `tax_26as` CSV files | `202 RunAccepted` | Parse each row into its existing source model, then enqueue the run. |
| `GET /api/v1/runs/{run_id}` | none | `RunSummary` | Poll state and obtain stable counts. |
| `GET /api/v1/runs/{run_id}/results` | `status`, `method`, `exception_reason`, `offset=0`, `limit=50` | `ResultPage` | Paginated matched and exception rows. `limit` is capped at 200. |
| `GET /api/v1/runs/{run_id}/results/{result_id}` | none | `ResultSummary` | Return one decision and its display-ready source summary. |
| `GET /api/v1/runs/{run_id}/metrics` | none | `RunMetricsResponse` | Dashboard match, exception, latency, cache, and cost metrics. |
| `GET /api/v1/runs/{run_id}/llm-decisions` | `status`, `offset=0`, `limit=50` | `LLMDecisionPage` | Stage 6 reasoning and operational evidence. |
| `GET /api/v1/runs/{run_id}/tax-results` | `offset=0`, `limit=50` | `TaxResultPage` | Return enrichment and mismatch findings for matched records. |
| `POST /api/v1/runs/{run_id}/questions` | `QuestionRequest` | `QuestionResponse` | Read-only Q&A over one completed run. |
| `GET /api/v1/runs/{run_id}/audit` | none | `AuditSummary` | Return the local root and optional chain anchor. |
| `GET /api/v1/runs/{run_id}/audit/{record_id}/verify` | none | `AuditVerification` | Verify one finalized result against the run root. |

The list route filters by `matched`/exception state using `status=matched|exception|all`. Exception rows are serialized as `ReconciliationResult` with `matched=False`, a populated `exception_reason`, and retained Stage 6 reasoning where available, giving the frontend one table shape.

## Services and ownership

- `api/main.py`: app creation, middleware, router registration, exception handlers.
- `api/models.py`: API-only request, pagination, run-state, and response models.
- `api/routes.py`: HTTP translation only; no matching or metric calculations.
- `api/service.py`: validates run state, invokes `run_reconciliation`, and calls later tax/Q&A/audit services.
- `api/store.py`: minimal `RunStore` protocol plus `InMemoryRunStore`; stores input metadata and typed outputs by `run_id`.
- `matching/pipeline.py`: sole orchestration authority for Stages 0-7.

The store owns run lifecycle transitions: `queued -> running -> completed|failed`. Completed outputs are immutable. A second execution creates a new UUID run rather than overwriting an old one.

## Validation and errors

- `400 INVALID_CSV`: unreadable UTF-8, missing required header, empty required source, or a row that cannot be parsed. `details` identifies file, row, and field without echoing the full row.
- `404 RUN_NOT_FOUND`: unknown run or record ID.
- `409 RUN_NOT_READY`: result-dependent endpoint called before completion.
- `409 FEATURE_NOT_READY`: tax, Q&A, or audit endpoint called before its service output exists.
- `422`: FastAPI/Pydantic request validation, normalized to `ErrorResponse`.
- `500 RUN_FAILED`: the background pipeline failed; the run remains queryable with a sanitized message. Provider secrets and raw exception traces are logged server-side only.

Gemini exhaustion does not fail a run. Stage 6 already returns `LLM_UNAVAILABLE`, and Stage 7 categorizes those unresolved records. Upload size is capped by configuration before parsing; default 10 MB per file.

## Configuration and CORS

```text
RECONIQ_ENV=development
RECONIQ_CORS_ORIGINS=http://localhost:3000
RECONIQ_MAX_UPLOAD_MB=10
RECONIQ_LLM_DB=data/llm_cache.sqlite3
GEMINI_API_KEY=
SUPABASE_URL=                 # unused until persistence transition
SUPABASE_SERVICE_ROLE_KEY=    # unused until persistence transition
```

CORS allows only configured origins, methods `GET` and `POST`, and headers `Content-Type` and `Authorization`. Local development defaults to `http://localhost:3000`; production has no wildcard origin. Credentials are disabled because the first version has no cookie authentication.

## Supabase transition

The HTTP and pipeline contracts do not change. Replace `InMemoryRunStore` with `SupabaseRunStore` behind the same small protocol and persist:

- `runs`: lifecycle, source, timestamps, counts, error, metrics JSON.
- `reconciliation_results`: one row per final result with `record_ids` and reasoning stored as JSON-compatible columns.
- `llm_decisions`: one row per `LLMDecisionRecord`.
- `tax_results` and `audit_runs`: added when their modules land.

Raw uploaded financial rows are not persisted in the initial transition unless the demo requires run replay. Background execution should then move to a durable worker only when deployment measurements show in-process tasks are unreliable; that is outside the first API slice.

## Test plan

Tests use `fastapi.testclient.TestClient`; Gemini is never called in API tests.

1. Health and CORS: health response, configured origin accepted, unconfigured origin rejected.
2. Upload validation: valid CSVs become typed entries; missing headers, malformed dates/numbers, oversized files, and empty files return the documented error.
3. Run lifecycle: create returns `202`; a stub pipeline moves `queued -> running -> completed`, and failures become queryable `failed` runs.
4. Pipeline boundary: assert the service calls `matching.pipeline.run_reconciliation` once with `PipelineInput`, never individual stage functions, and never includes ground truth.
5. Results: filtering, stable input order, pagination bounds, exception serialization, and unknown/not-ready runs.
6. Metrics and LLM decisions: values are passed from typed outputs without recomputation or leaking candidate inputs.
7. Feature endpoints: completed typed tax/Q&A/audit responses plus `FEATURE_NOT_READY` until each service is present.
8. Store contract: run transitions and immutable completed results pass against the in-memory implementation; reuse the same tests for Supabase later.

## Deliberate limits

- No authentication or multi-tenancy in the submission build.
- No WebSockets; polling one run resource is sufficient for this batch size.
- No generic CRUD layer or ORM before Supabase is implemented.
- No endpoint can expose or execute the holdout partition before the final evaluation gate.
- No route may alter a completed reconciliation result.
