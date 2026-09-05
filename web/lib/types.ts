export type RunStatus = "completed" | "processing" | "failed";

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

export type AuditSummary = {
  status: "not_anchored" | "anchored";
  merkle_root: string | null;
  transaction_hash: string | null;
};

export type RunSummary = {
  id: string;
  name: string;
  created_at: string;
  status: RunStatus;
  source_counts: SourceCounts;
  summary: SummaryMetrics;
  stage_counts: StageCount[];
  exception_metrics: ExceptionMetric[];
  llm_metrics: LlmMetrics;
  audit: AuditSummary;
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
  id: string;
  reference: string;
  section: string;
  expected_tds: number;
  actual_tds: number;
  status: "clear" | "mismatch";
  mismatch_reason: string | null;
};

export type QuestionResponse = {
  question: string;
  answer: string;
  rows: Array<Record<string, string | number>>;
};

export type AuditProof = {
  record_id: string;
  leaf_hash: string;
  merkle_root: string;
  proof: string[];
  verified: boolean;
};
