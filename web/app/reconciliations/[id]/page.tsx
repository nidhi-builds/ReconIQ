import { ArrowLeft, CircleCheck, TriangleAlert } from "lucide-react";
import { StatusPill } from "@/components/status-pill";
import { RunSelector } from "@/components/run-selector";
import { getResult, listRuns } from "@/lib/api";
import { formatCurrency, readableLabel, resultStatus, runHref, selectRun } from "@/lib/view-model";

export default async function ResultDetailPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ run?: string }> }) {
  const [{ id }, query, runs] = await Promise.all([params, searchParams, listRuns()]);
  const run = selectRun(runs, query.run);
  if (!run) throw new Error("No completed reconciliation runs are available.");
  const result = await getResult(run.id, id);
  const status = resultStatus(result);
  return <div className="page-wrap narrow">
    <RunSelector runs={runs} selectedRunId={run.id} />
    <a className="back-link" href={runHref("/reconciliations", run.id)}><ArrowLeft size={16} />All reconciliations</a>
    <header className="detail-title"><div className={status === "Matched" ? "detail-symbol positive" : "detail-symbol warning"}>{status === "Matched" ? <CircleCheck /> : <TriangleAlert />}</div><div><p className="eyebrow">Decision record</p><h1>{result.record_ids[0]}</h1><StatusPill tone={status === "Matched" ? "positive" : "warning"}>{status}</StatusPill></div><strong>{formatCurrency(result.amount)}</strong></header>
    <section className="detail-grid"><div><span>Method</span><strong>{readableLabel(result.method)}</strong></div><div><span>Confidence</span><strong>{Math.round(result.confidence * 100)}%</strong></div><div><span>Value date</span><strong>{result.date}</strong></div><div><span>Exception</span><strong>{result.exception_reason ? readableLabel(result.exception_reason) : "None"}</strong></div></section>
    <section className="panel detail-panel"><p className="eyebrow">Linked evidence</p><h2>Record identifiers</h2><div className="id-list">{result.record_ids.map((recordId) => <code key={recordId}>{recordId}</code>)}</div></section>
    <section className="panel detail-panel"><p className="eyebrow">Decision rationale</p><h2>Why this result</h2><p className="reasoning">{result.reasoning ?? "No reasoning was recorded."}</p></section>
  </div>;
}
