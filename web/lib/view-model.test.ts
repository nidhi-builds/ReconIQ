import assert from "node:assert/strict";

import { formatCurrency, formatPercent, requireApiUrl, resultStatus, runHref, selectRun, visibleRuns } from "./view-model.ts";
import type { RunSummary } from "./types.ts";

assert.equal(formatCurrency(12450.5), "₹12,450.50");
assert.equal(formatPercent(0.967), "96.7%");

assert.equal(resultStatus({ matched: true, exception_reason: null }), "Matched");
assert.equal(
  resultStatus({ matched: false, exception_reason: "MISSING_REF_ID" }),
  "Exception",
);
assert.equal(resultStatus({ matched: false, exception_reason: null }), "Pending");

const run = (id: string, run_type: "design" | "holdout" | "upload"): RunSummary => ({
  id,
  run_type,
  name: run_type === "holdout" ? "Final Holdout Run - untouched" : "Design Run - development set",
  created_at: "2026-09-05T00:00:00Z",
  status: "completed",
  code_version: "abc123",
  source_counts: { ledger: 1, settlements: 1, bank: 1, tax_26as: 0 },
  summary: { total_records: 1, matched_records: 1, exception_records: 0, pending_records: 0, matched_amount: 100, match_rate: 1, precision: 1, recall: 1 },
  stage_counts: [],
  exception_metrics: [],
  llm_metrics: { calls: 0, cache_hit_rate: 0, average_latency_ms: 0, estimated_cost_usd: 0 },
});

const design = run("design-1", "design");
const holdout = run("holdout-1", "holdout");
assert.equal(selectRun([design, holdout])?.id, "holdout-1");
assert.equal(selectRun([design])?.id, "design-1");
assert.equal(selectRun([design, holdout], "design-1")?.id, "design-1");
assert.equal(selectRun([run("upload-1", "upload")])?.id, "upload-1");
const newerDesign = { ...design, id: "design-2", created_at: "2026-09-06T00:00:00Z" };
const olderUpload = { ...run("upload-1", "upload"), created_at: "2026-09-04T00:00:00Z" };
const newerUpload = { ...run("upload-2", "upload"), created_at: "2026-09-06T00:00:00Z" };
assert.deepEqual(visibleRuns([design, newerUpload, newerDesign, holdout, olderUpload]), [holdout, newerDesign, newerUpload, olderUpload]);
assert.equal(runHref("/tax", "design-1"), "/tax?run=design-1");
assert.equal(runHref("/reconciliations?status=matched", "design-1"), "/reconciliations?status=matched&run=design-1");
assert.throws(() => requireApiUrl(undefined), /NEXT_PUBLIC_API_URL/);
assert.equal(requireApiUrl("http://localhost:8000/"), "http://localhost:8000");

console.log("view-model tests passed");
