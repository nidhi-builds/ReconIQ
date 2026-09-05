import { ChevronRight } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/status-pill";
import { RunSelector } from "@/components/run-selector";
import { getResults, listRuns } from "@/lib/api";
import { formatCurrency, readableLabel, resultStatus, runHref, selectRun } from "@/lib/view-model";

export default async function ReconciliationsPage({ searchParams }: { searchParams: Promise<{ run?: string; status?: string }> }) {
  const [params, runs] = await Promise.all([searchParams, listRuns()]);
  const run = selectRun(runs, params.run);
  if (!run) throw new Error("No completed reconciliation runs are available.");
  const status = ["matched", "exception"].includes(params.status ?? "") ? params.status : "all";
  const results = await getResults(run.id, status);
  return <div className="page-wrap">
    <PageHeader eyebrow="Evidence ledger" title="Reconciliations" description="Inspect every confirmed group and unresolved exception with its supporting rationale." />
    <div className="table-toolbar"><RunSelector runs={runs} selectedRunId={run.id} /><div className="segmented" aria-label="Result filter"><a className={status === "all" ? "selected" : ""} href={runHref("/reconciliations", run.id)}>All</a><a className={status === "matched" ? "selected" : ""} href={runHref("/reconciliations?status=matched", run.id)}>Matched</a><a className={status === "exception" ? "selected" : ""} href={runHref("/reconciliations?status=exception", run.id)}>Exceptions</a></div></div>
    <section className="table-panel"><table><thead><tr><th>Primary record</th><th>Date</th><th>Amount</th><th>Method</th><th>Status</th><th>Confidence</th><th aria-label="Open" /></tr></thead><tbody>{results.items.map((item) => { const itemStatus = resultStatus(item); return <tr key={item.id}><td><strong>{item.record_ids[0]}</strong><small>{item.record_ids.length} linked records</small></td><td>{new Date(item.date).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}</td><td>{formatCurrency(item.amount)}</td><td>{readableLabel(item.method)}</td><td><StatusPill tone={itemStatus === "Matched" ? "positive" : itemStatus === "Exception" ? "warning" : "neutral"}>{itemStatus}</StatusPill></td><td>{Math.round(item.confidence * 100)}%</td><td><a className="icon-link" href={runHref(`/reconciliations/${item.id}`, run.id)} title="Open result"><ChevronRight size={18} /></a></td></tr>; })}</tbody></table>{results.items.length === 0 && <div className="empty-state"><FileEmpty />No reconciliation results match this view.</div>}</section>
  </div>;
}

function FileEmpty() { return <span aria-hidden="true">0</span>; }
