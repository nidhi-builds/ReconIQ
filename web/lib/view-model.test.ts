import assert from "node:assert/strict";

import { formatCurrency, formatPercent, resultStatus } from "./view-model.ts";

assert.equal(formatCurrency(12450.5), "₹12,450.50");
assert.equal(formatPercent(0.967), "96.7%");

assert.equal(resultStatus({ matched: true, exception_reason: null }), "Matched");
assert.equal(
  resultStatus({ matched: false, exception_reason: "MISSING_REF_ID" }),
  "Exception",
);
assert.equal(resultStatus({ matched: false, exception_reason: null }), "Pending");

console.log("view-model tests passed");
