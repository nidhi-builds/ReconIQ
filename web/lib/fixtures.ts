import type { AuditProof, PaginatedResults, RunSummary, TaxFinding } from "./types";

export const fixtureRun: RunSummary = {
  id: "design-2026-09-05",
  name: "Design dataset",
  created_at: "2026-09-05T16:34:00+05:30",
  status: "completed",
  source_counts: { ledger: 91, settlements: 94, bank: 88 },
  summary: {
    total_records: 273,
    matched_records: 244,
    exception_records: 29,
    pending_records: 0,
    matched_amount: 1842670.5,
    match_rate: 0.894,
    precision: 1,
    recall: 0.962,
  },
  stage_counts: [
    { stage: "stage_2", label: "Exact reference", count: 42 },
    { stage: "stage_3", label: "Fee adjusted", count: 18 },
    { stage: "stage_4", label: "Split settlement", count: 12 },
    { stage: "stage_5", label: "Refund reversal", count: 8 },
    { stage: "stage_6", label: "LLM assisted", count: 5 },
  ],
  exception_metrics: [
    { category: "MISSING_REF_ID", count: 8, precision: 1, recall: 1 },
    { category: "TIMING_LAG_EXCEEDED", count: 6, precision: 1, recall: 0.833 },
    { category: "AMOUNT_MISMATCH_UNEXPLAINED", count: 5, precision: 1, recall: 1 },
    { category: "AMBIGUOUS_CANDIDATES", count: 4, precision: 1, recall: 1 },
  ],
  llm_metrics: { calls: 7, cache_hit_rate: 0, average_latency_ms: 2692, estimated_cost_usd: 0.0039348 },
  audit: { status: "not_anchored", merkle_root: null, transaction_hash: null },
};

export const fixtureResults: PaginatedResults = {
  items: [
    { id: "result-001", record_ids: ["ord-1042", "set-1042", "bank-1042"], matched: true, confidence: 1, method: "exact_ref", exception_reason: null, reasoning: "Unique reference present across all three sources.", amount: 24990, date: "2026-08-30" },
    { id: "result-002", record_ids: ["ord-1088", "set-1088", "bank-1088"], matched: true, confidence: 0.98, method: "fee_adjusted", exception_reason: null, reasoning: "Bank receipt equals settlement net after gateway fee and GST.", amount: 18450, date: "2026-08-31" },
    { id: "result-003", record_ids: ["ord-1126", "set-1126-a", "set-1126-b", "bank-1126"], matched: true, confidence: 0.94, method: "split_settlement", exception_reason: null, reasoning: "Two settlement parts reconcile to one bank receipt.", amount: 31780, date: "2026-09-01" },
    { id: "result-004", record_ids: ["ord-1161", "set-1161", "bank-1161"], matched: false, confidence: 0, method: "exception", exception_reason: "TIMING_LAG_EXCEEDED", reasoning: "Amount aligns, but the bank receipt is outside the permitted settlement window.", amount: 8960, date: "2026-09-02" },
    { id: "result-005", record_ids: ["ord-1184", "set-1184", "bank-1184"], matched: false, confidence: 0.32, method: "llm", exception_reason: "AMBIGUOUS_CANDIDATES", reasoning: "Two candidates carry indistinguishable usable evidence, so no match was selected.", amount: 12450, date: "2026-09-03" },
  ],
  offset: 0,
  limit: 20,
  total: 5,
};

export const fixtureTaxFindings: TaxFinding[] = [
  { id: "tax-1", reference: "pay_1042", section: "194H", expected_tds: 249.9, actual_tds: 249.9, status: "clear", mismatch_reason: null },
  { id: "tax-2", reference: "pay_1088", section: "194H", expected_tds: 184.5, actual_tds: 92.25, status: "mismatch", mismatch_reason: "SHORT_DEDUCTION" },
  { id: "tax-3", reference: "pay_1126", section: "194C", expected_tds: 317.8, actual_tds: 317.8, status: "mismatch", mismatch_reason: "WRONG_SECTION" },
  { id: "tax-4", reference: "pay_1161", section: "194H", expected_tds: 89.6, actual_tds: 89.6, status: "mismatch", mismatch_reason: "MISSING_CHALLAN" },
];

export const fixtureProof: AuditProof = {
  record_id: "result-001",
  leaf_hash: "0x6e89477250894eb134fe439133477c925f445cfad8229b7e42ef3a44c77b63b8",
  merkle_root: "0x2774c1aaf267929c16d47b937bd980277024de7209461644f380118af6772bb8",
  proof: ["0xb153b41c...f2a9", "0x87c0e67d...d31e"],
  verified: true,
};
