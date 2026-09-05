from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from matching.evaluation import EvaluationMetrics
from matching.pipeline import PipelineInput, PipelineRunResult


RunType = Literal["design", "holdout", "upload"]
RunStatus = Literal["queued", "running", "completed", "failed"]


class RunRecord(BaseModel):
    id: str
    run_type: RunType
    label: str
    status: RunStatus
    code_version: str
    created_at: datetime
    completed_at: datetime | None = None
    duration_ms: float | None = None
    error: str | None = None
    pipeline_input: PipelineInput
    output: PipelineRunResult | None = None
    evaluation: EvaluationMetrics | None = None


class SourceCountsResponse(BaseModel):
    ledger: int
    settlements: int
    bank: int


class SummaryMetrics(BaseModel):
    total_records: int
    matched_records: int
    exception_records: int
    pending_records: int = 0
    matched_amount: float
    match_rate: float
    precision: float | None
    recall: float | None


class StageCount(BaseModel):
    stage: str
    label: str
    count: int


class ExceptionMetric(BaseModel):
    category: str
    count: int
    precision: float | None
    recall: float | None


class LLMMetricsResponse(BaseModel):
    calls: int
    cache_hit_rate: float
    average_latency_ms: float
    estimated_cost_usd: float


class RunSummary(BaseModel):
    id: str
    name: str
    run_type: RunType
    created_at: datetime
    status: RunStatus
    code_version: str
    source_counts: SourceCountsResponse
    summary: SummaryMetrics
    stage_counts: list[StageCount]
    exception_metrics: list[ExceptionMetric]
    llm_metrics: LLMMetricsResponse
class ResultSummary(BaseModel):
    id: str
    record_ids: list[str]
    matched: bool
    confidence: float
    method: str
    exception_reason: str | None
    reasoning: str | None
    amount: float
    date: str


class Page(BaseModel):
    items: list[dict]
    total: int
    offset: int
    limit: int


class QuestionRequest(BaseModel):
    question: str


class UploadRequest(BaseModel):
    ledger_csv: str
    settlement_csv: str
    bank_csv: str
    tax_26as_csv: str = ""


class QuestionResponse(BaseModel):
    question: str
    answer: str
    rows: list[dict[str, str | int | float | bool | None]]
    generated_sql: str | None
    status: Literal["ANSWERED", "REFUSED", "QA_UNAVAILABLE"]
    sql_provider: str | None = None
    sql_model: str | None = None
    answer_provider: str | None = None
    answer_model: str | None = None
    fallback_used: bool = False
