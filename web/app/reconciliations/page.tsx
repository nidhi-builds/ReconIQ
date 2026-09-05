import { ChevronRight, Search } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { StatusPill } from "@/components/status-pill";
import { getResults, listRuns } from "@/lib/api";
import { formatCurrency, readableLabel, resultStatus } from "@/lib/view-model";

export default async function ReconciliationsPage() {
  const [run] = await listRuns();
  const results = await getResults(run.id);
  return <div className="page-wrap">
    <PageHeader eyebrow="Evidence ledger" title="Reconciliations" description="Inspect every confirmed group and unresolved exception with its supporting rationale." />
    <div className="table-toolbar"><div className="search-box"><Search size={17} /><input aria-label="Search reconciliations" placeholder="Search IDs or reason" /></div><div className="segmented" aria-label="Result filter"><button className="selected">All</button><button>Matched</button><button>Exceptions</button></div></div>
    <section className="table-panel"><table><thead><tr><th>Primary record</th><th>Date</th><th>Amount</th><th>Method</th><th>Status</th><th>Confidence</th><th aria-label="Open" /></tr></thead><tbody>{results.items.map((item) => { const status = resultStatus(item); return <tr key={item.id}><td><strong>{item.record_ids[0]}</strong><small>{item.record_ids.length} linked records</small></td><td>{new Date(item.date).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}</td><td>{formatCurrency(item.amount)}</td><td>{readableLabel(item.method)}</td><td><StatusPill tone={status === "Matched" ? "positive" : status === "Exception" ? "warning" : "neutral"}>{status}</StatusPill></td><td>{Math.round(item.confidence * 100)}%</td><td><a className="icon-link" href={`/reconciliations/${item.id}`} title="Open result"><ChevronRight size={18} /></a></td></tr>; })}</tbody></table>{results.items.length === 0 && <div className="empty-state"><FileEmpty />No reconciliation results match this view.</div>}</section>
  </div>;
}

function FileEmpty() { return <span aria-hidden="true">0</span>; }
