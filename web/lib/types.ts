export type RunStatus = "queued" | "running" | "completed" | "failed";
export type RunType = "design" | "holdout" | "upload";

export type SourceCounts = {
  ledger: number;
  settlements: number;
  bank: number;
};

export type SummaryMetrics = {
  total_records: number;
  matched_records: number;
  exception_records: number;
  pending_records: number;
  matched_amount: number;
  match_rate: number;
  precision: number | null;
  recall: number | null;
};

export type StageCount = {
  stage: string;
  label: string;
  count: number;
};

export type ExceptionMetric = {
  category: string;
  count: number;
  precision: number | null;
  recall: number | null;
};

export type LlmMetrics = {
  calls: number;
  cache_hit_rate: number;
  average_latency_ms: number;
  estimated_cost_usd: number;
};

export type RunSummary = {
  id: string;
  run_type: RunType;
  name: string;
  created_at: string;
  status: RunStatus;
  code_version: string;
  source_counts: SourceCounts;
  summary: SummaryMetrics;
  stage_counts: StageCount[];
  exception_metrics: ExceptionMetric[];
  llm_metrics: LlmMetrics;
};

export type ReconciliationRecord = {
  id: string;
  record_ids: string[];
  matched: boolean;
  confidence: number;
  method: string;
  exception_reason: string | null;
  reasoning: string | null;
  amount: number;
  date: string;
};

export type PaginatedResults = {
  items: ReconciliationRecord[];
  offset: number;
  limit: number;
  total: number;
};

export type TaxFinding = {
  record_ids: string[];
  settlement_id: string;
  ref_id: string | null;
  gst_category: string;
  tds_section: string;
  expected_tds: number | null;
  actual_tds: number;
  status: "CLEAR" | "MISMATCH" | "UNVERIFIABLE";
  mismatch_reason: string | null;
  reasoning: string;
};

export type QuestionResponse = {
  question: string;
  answer: string;
  rows: Array<Record<string, string | number>>;
  generated_sql: string | null;
  status: "ANSWERED" | "REFUSED" | "QA_UNAVAILABLE";
};
