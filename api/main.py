from collections.abc import Callable
import os
import subprocess

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api.models import (
    ExceptionMetric,
    LLMMetricsResponse,
    Page,
    QuestionRequest,
    QuestionResponse,
    ResultSummary,
    RunSummary,
    SourceCountsResponse,
    StageCount,
    SummaryMetrics,
    UploadRequest,
)
from api.service import PipelineRunner, RunService
from api.store import (
    DuplicateHoldoutError,
    InMemoryRunStore,
    InvalidRunStateError,
    RunNotFoundError,
    RunStore,
    SupabaseRunStore,
)
from matching.pipeline import PipelineInput, PipelineRunResult, run_reconciliation
from observability.llm_tracker import LLMTracker
from qa.text_to_sql_agent import QAProvider, answer_question, make_gemini_provider


def _default_runner(pipeline_input: PipelineInput) -> PipelineRunResult:
    tracker = LLMTracker(os.getenv("RECONIQ_LLM_DB", "data/llm_cache.sqlite3"))
    return run_reconciliation(pipeline_input, tracker=tracker)


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()


def _configured_store() -> RunStore:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if bool(url) != bool(key):
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be configured together"
        )
    return SupabaseRunStore(url, key) if url and key else InMemoryRunStore()


def create_app(
    *,
    store: RunStore | None = None,
    run_pipeline: PipelineRunner = _default_runner,
    code_version: Callable[[], str] = _git_sha,
    qa_provider: QAProvider | None = None,
) -> FastAPI:
    app = FastAPI(title="ReconIQ API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv(
            "RECONIQ_CORS_ORIGIN",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(","),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    run_store = store or _configured_store()
    app.state.run_store = run_store
    service = RunService(run_store, run_pipeline, code_version)

    def completed(run_id: str):
        try:
            return run_store.get_completed(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(404, "Run not found.") from exc
        except InvalidRunStateError as exc:
            raise HTTPException(409, "Run is not completed.") from exc

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/runs/upload", status_code=201)
    def create_upload(request: UploadRequest) -> RunSummary:
        try:
            return _summary(service.create_upload(**request.model_dump()))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, "Run execution failed.") from exc

    @app.post("/api/v1/runs/{run_type}", status_code=201)
    def create_run(run_type: str) -> RunSummary:
        if run_type not in {"design", "holdout"}:
            raise HTTPException(404, "Run type not found.")
        try:
            return _summary(service.create_partition(run_type))
        except DuplicateHoldoutError as exc:
            raise HTTPException(409, "A holdout run already exists.") from exc
        except Exception as exc:
            raise HTTPException(500, "Run execution failed.") from exc

    @app.get("/api/v1/runs")
    def list_runs() -> list[RunSummary]:
        return [_summary(row) for row in run_store.list_completed()]

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str) -> RunSummary:
        return _summary(completed(run_id))

    @app.get("/api/v1/runs/{run_id}/results")
    def get_results(
        run_id: str,
        status: str = "all",
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ) -> Page:
        run = completed(run_id)
        items = [_result_summary(run, index) for index in range(len(run.output.results))]
        if status == "matched":
            items = [row for row in items if row.matched]
        elif status == "exception":
            items = [row for row in items if not row.matched]
        return Page(
            items=[row.model_dump(mode="json") for row in items[offset : offset + limit]],
            total=len(items),
            offset=offset,
            limit=limit,
        )

    @app.get("/api/v1/runs/{run_id}/metrics")
    def get_metrics(run_id: str) -> dict:
        return _summary(completed(run_id)).model_dump(mode="json")

    @app.get("/api/v1/runs/{run_id}/results/{result_id}")
    def get_result(run_id: str, result_id: str) -> ResultSummary:
        run = completed(run_id)
        try:
            index = int(result_id.removeprefix("result-")) - 1
            if index < 0:
                raise ValueError
            return _result_summary(run, index)
        except (ValueError, IndexError) as exc:
            raise HTTPException(404, "Result not found.") from exc

    @app.get("/api/v1/runs/{run_id}/llm-decisions")
    def get_llm_decisions(run_id: str, offset: int = 0, limit: int = 50) -> Page:
        rows = completed(run_id).output.llm_decisions
        return Page(
            items=[row.model_dump(mode="json") for row in rows[offset : offset + limit]],
            total=len(rows),
            offset=offset,
            limit=limit,
        )

    @app.get("/api/v1/runs/{run_id}/tax-results")
    def get_tax_results(run_id: str, offset: int = 0, limit: int = 50) -> Page:
        rows = completed(run_id).output.tax_findings
        return Page(
            items=[row.model_dump(mode="json") for row in rows[offset : offset + limit]],
            total=len(rows),
            offset=offset,
            limit=limit,
        )

    @app.post("/api/v1/runs/{run_id}/questions")
    def ask(run_id: str, request: QuestionRequest) -> QuestionResponse:
        run = completed(run_id)
        provider = qa_provider or make_gemini_provider()
        answer = answer_question(
            request.question,
            run.output.results,
            run.pipeline_input.settlements,
            run.output.tax_findings,
            primary=provider,
        )
        return QuestionResponse(
            **answer.model_dump(exclude={"rows", "metrics"}),
            rows=[row.values for row in answer.rows],
        )

    return app


def _summary(run) -> RunSummary:
    output = run.output
    evaluation = run.evaluation
    matched = [row for row in output.results if row.matched]
    settlement_by_id = {
        row.settlement_id: row for row in run.pipeline_input.settlements
    }
    matched_amount = sum(
        settlement_by_id[record_id].gross_amount
        for result in matched
        for record_id in result.record_ids
        if record_id in settlement_by_id
    )
    decisions = output.llm_decisions
    calls = [row for row in decisions if row.status == "evaluated"]
    return RunSummary(
        id=run.id,
        name=run.label,
        run_type=run.run_type,
        created_at=run.created_at,
        status=run.status,
        code_version=run.code_version,
        source_counts=SourceCountsResponse(**output.metrics.source_counts.model_dump()),
        summary=SummaryMetrics(
            total_records=len(output.results),
            matched_records=len(matched),
            exception_records=len(output.results) - len(matched),
            matched_amount=matched_amount,
            match_rate=output.metrics.match_rate,
            precision=evaluation.match_precision if evaluation else None,
            recall=evaluation.match_recall if evaluation else None,
        ),
        stage_counts=[
            StageCount(stage=stage, label=stage.replace("_", " ").title(), count=count)
            for stage, count in output.metrics.stage_counts.items()
        ],
        exception_metrics=[
            ExceptionMetric(
                category=category,
                count=metric.support,
                precision=metric.precision,
                recall=metric.recall,
            )
            for category, metric in (evaluation.exception_metrics.items() if evaluation else [])
        ],
        llm_metrics=LLMMetricsResponse(
            calls=len(calls),
            cache_hit_rate=(sum(row.cache_hit for row in calls) / len(calls) if calls else 0),
            average_latency_ms=(sum(row.latency_ms for row in calls) / len(calls) if calls else 0),
            estimated_cost_usd=sum(row.estimated_cost_usd for row in calls),
        ),
    )


def _result_summary(run, index: int) -> ResultSummary:
    result = run.output.results[index]
    settlements = {
        row.settlement_id: row for row in run.pipeline_input.settlements
    }
    ledgers = {row.order_id: row for row in run.pipeline_input.ledger}
    banks = {row.bank_txn_id: row for row in run.pipeline_input.bank}
    settlement = next((settlements[row] for row in result.record_ids if row in settlements), None)
    ledger = next((ledgers[row] for row in result.record_ids if row in ledgers), None)
    bank = next((banks[row] for row in result.record_ids if row in banks), None)
    amount = settlement.gross_amount if settlement else ledger.amount if ledger else bank.amount
    event_date = settlement.settlement_date if settlement else ledger.order_date if ledger else bank.value_date
    return ResultSummary(
        id=f"result-{index + 1:04d}",
        **result.model_dump(),
        amount=amount,
        date=event_date.isoformat(),
    )


app = create_app()
