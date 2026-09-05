import { ArrowLeft, CircleCheck, TriangleAlert } from "lucide-react";
import { StatusPill } from "@/components/status-pill";
import { getResult, listRuns } from "@/lib/api";
import { formatCurrency, readableLabel, resultStatus } from "@/lib/view-model";

export default async function ResultDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const [{ id }, [run]] = await Promise.all([params, listRuns()]);
  const result = await getResult(run.id, id);
  const status = resultStatus(result);
  return <div className="page-wrap narrow">
    <a className="back-link" href="/reconciliations"><ArrowLeft size={16} />All reconciliations</a>
    <header className="detail-title"><div className={status === "Matched" ? "detail-symbol positive" : "detail-symbol warning"}>{status === "Matched" ? <CircleCheck /> : <TriangleAlert />}</div><div><p className="eyebrow">Decision record</p><h1>{result.record_ids[0]}</h1><StatusPill tone={status === "Matched" ? "positive" : "warning"}>{status}</StatusPill></div><strong>{formatCurrency(result.amount)}</strong></header>
    <section className="detail-grid"><div><span>Method</span><strong>{readableLabel(result.method)}</strong></div><div><span>Confidence</span><strong>{Math.round(result.confidence * 100)}%</strong></div><div><span>Value date</span><strong>{result.date}</strong></div><div><span>Exception</span><strong>{result.exception_reason ? readableLabel(result.exception_reason) : "None"}</strong></div></section>
    <section className="panel detail-panel"><p className="eyebrow">Linked evidence</p><h2>Record identifiers</h2><div className="id-list">{result.record_ids.map((recordId) => <code key={recordId}>{recordId}</code>)}</div></section>
    <section className="panel detail-panel"><p className="eyebrow">Decision rationale</p><h2>Why this result</h2><p className="reasoning">{result.reasoning ?? "No reasoning was recorded."}</p></section>
  </div>;
}
